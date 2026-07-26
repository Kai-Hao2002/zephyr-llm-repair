# dataset/scripts/mine_commits.py
import os
import re
import requests
import json
import logging
import time
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("GitHubMiner")

# 執行期崩潰關鍵字 (用於從 PR 標題/內文判斷是否為 runtime crash 案例)
# Runtime-crash keywords used to classify PRs from their title/body
CRASH_KEYWORDS = [
    "panic", "crash", "fault", "assert", "hang", "oops",
    "deadlock", "overflow", "corrupt", "use-after-free", "null pointer",
]

# 用於猜測開發板的架構關鍵字 -> board 對照表
# Architecture keyword -> board mapping used to guess a QEMU target
ARCH_BOARD_MAP = [
    (re.compile(r"arch/xtensa|/xtensa/|xtensa"), "qemu_xtensa"),
    (re.compile(r"arch/riscv|/riscv/"), "qemu_riscv32"),
    (re.compile(r"arch/arm64|/arm64/"), "qemu_cortex_a53"),
    (re.compile(r"arch/arm|/arm/"), "qemu_cortex_m3"),
    (re.compile(r"arch/x86|/x86/"), "qemu_x86"),
]

# 板級目錄修改 (boards/<vendor>/<board_name>/...) 應該直接用該板子本身當作
# --board 參數，而不是套用架構關鍵字對照表——否則像 native_sim 這種通用板子
# 根本不會載入該廠商板子的 DTS，導致誤判「建置成功」。
# A board-directory edit (boards/<vendor>/<board_name>/...) should use that
# board itself as --board, not the arch keyword map — a generic board like
# native_sim never loads the vendor board's DTS, causing false "build succeeded".
BOARD_PATH_RE = re.compile(r"boards/[^/]+/([^/]+)/")

# 預先註冊的合成錯誤注入目錄 (在任何修補實驗開始前就固定下來，避免事後
# 針對系統調整)。每一筆指定：分類、目標檔案 (相對於 Zephyr repo 根目錄)、
# mutation operator (定義在 tools/mutate_inject.py)，以及用來觸發/驗證這個
# mutation 的 target_app 與 board。target_app/board 特意選成「一定會載入/
# 編譯到目標檔案」的組合 (例如板子自己的 DTS 就用該板子本身建置)，避免真實
# 挖礦時遇到的 target_app/board 猜錯問題。
#
# 這份清單只是起點，不保證每一筆都能通過 verify_cases.py 的雙向驗證閘——
# 通不過的會被自動捨棄，這跟真實挖礦案例的篩選邏輯一致。
#
# A pre-registered catalog of synthetic fault-injection candidates (fixed
# before any repair experiments run, so it isn't tuned post hoc). Each entry
# specifies: category, target file (relative to the Zephyr repo root), the
# mutation operator (defined in tools/mutate_inject.py), and the
# target_app/board used to trigger/verify it. target_app/board are chosen so
# the target file is guaranteed to be loaded/compiled (e.g. a board's own
# DTS is built for that exact board), sidestepping the target-guessing
# problem seen with real mining.
#
# This is a starting catalog, not a guarantee — entries that fail the
# verify_cases.py two-sided gate are discarded automatically, same as mined
# candidates.
INJECTION_CATALOG = [
    # --- Kconfig Dependency and Configuration Conflicts ---
    # 兩次都失敗的教訓：console 相關的 Kconfig 符號 (lib/libc/Kconfig 的
    # MINIMAL_LIBC_SUPPORTED、drivers/console/Kconfig 的 POSIX_ARCH_CONSOLE)
    # 對 samples/hello_world 完全無效——因為 native_sim 上它的 printf()
    # 是直接呼叫 host 真正的 libc (NATIVE_LIBC)，繞過了整個 Zephyr console
    # 子系統；換成 target_app=tests/subsys/fs/fcb 一樣沒用，代表 native_sim
    # 的 stdout 是透過某個不受這些 Kconfig 開關控制的底層機制送出的。
    # 所以這次改找一個「符號被拿掉後，某個原本會被呼叫的函式就不會被編譯
    # 進去，導致連結期 undefined reference」的目標，而不是又賭一次「這個
    # driver 是否存在會影響 stdout 有沒有輸出」。subsys/fs/fcb/Kconfig 的
    # `config FCB` 正好是這種型：`depends on FLASH_MAP` 反轉後，
    # subsys/fs/fcb/*.c 整包都不會被編譯，但 tests/subsys/fs/fcb 的測試碼
    # 仍然呼叫 fcb_init()/fcb_append() 等函式，保證連結失敗；`select CRC`
    # 拿掉後，fcb_elem_info.c 呼叫的 crc8_ccitt() 找不到實作，一樣是連結
    # 失敗。兩個 mutation 都精準命中 tests/subsys/fs/fcb 這個「已知會被完整
    # 建置並執行」的測試目標本身依賴的基礎設施，不是賭邊緣的驅動程式。
    # Lesson from two failed attempts: console-related Kconfig symbols
    # (lib/libc/Kconfig's MINIMAL_LIBC_SUPPORTED, drivers/console/Kconfig's
    # POSIX_ARCH_CONSOLE) have zero effect on samples/hello_world, because
    # its printf() on native_sim calls the host's real libc directly
    # (NATIVE_LIBC), bypassing the entire Zephyr console subsystem;
    # retargeting to tests/subsys/fs/fcb didn't help either, meaning
    # native_sim's stdout reaches the terminal through some mechanism these
    # Kconfig switches don't gate at all. So this time the target is a
    # symbol whose removal makes some already-called function stop being
    # compiled in, guaranteeing a link-time undefined reference — not
    # another bet on "does this driver's presence affect whether stdout
    # appears". `config FCB` in subsys/fs/fcb/Kconfig fits exactly:
    # inverting `depends on FLASH_MAP` makes the whole subsys/fs/fcb/*.c
    # source set stop compiling, while tests/subsys/fs/fcb's test code still
    # calls fcb_init()/fcb_append()/etc — guaranteed link failure. Removing
    # `select CRC` means fcb_elem_info.c's call to crc8_ccitt() has no
    # implementation — also a guaranteed link failure. Both mutations land
    # squarely on infrastructure that tests/subsys/fs/fcb — a target already
    # confirmed to build and run fully — itself depends on, not a bet on a
    # peripheral driver.
    {
        "id_suffix": "kconfig_fcb_depends",
        "category": "kconfig",
        "target_file": "subsys/fs/fcb/Kconfig",
        "operator": "kconfig_invert_depends:FCB",
        "target_app": "tests/subsys/fs/fcb",
        "board": "native_sim",
    },
    {
        "id_suffix": "kconfig_fcb_select",
        "category": "kconfig",
        "target_file": "subsys/fs/fcb/Kconfig",
        "operator": "kconfig_remove_select:FCB",
        "target_app": "tests/subsys/fs/fcb",
        "board": "native_sim",
    },
    # --- Device Tree (DTS) Node Errors ---
    # 同樣道理：native_sim.dts 根節點的 `compatible = "zephyr,posix"` 沒有
    #任何 binding 真的去檢查它，刪掉不影響建置。flashcontroller0 節點的
    # `compatible = "zephyr,sim-flash"` 則是 tests/subsys/fs/fcb 用來產生
    # storage_partition flash 裝置的必要 binding，刪掉會讓 flash_area_open
    # 找不到底層裝置。
    # Root-node `compatible` isn't checked by any binding, so removing it
    # doesn't affect the build. flashcontroller0's `compatible =
    # "zephyr,sim-flash"` is the binding that produces the storage_partition
    # flash device used by tests/subsys/fs/fcb; removing it breaks
    # flash_area_open's underlying device.
    {
        "id_suffix": "dts_native_sim_compatible",
        "category": "dts",
        "target_file": "boards/native/native_sim/native_sim.dts",
        "operator": "dts_remove_compatible:zephyr,sim-flash",
        "target_app": "tests/subsys/fs/fcb",
        "board": "native_sim",
    },
    {
        "id_suffix": "dts_native_sim_phandle",
        "category": "dts",
        "target_file": "boards/native/native_sim/native_sim.dts",
        "operator": "dts_break_phandle",
        "target_app": "samples/hello_world",
        "board": "native_sim",
    },
    # --- C Syntax and Macro Errors ---
    {
        "id_suffix": "c_hello_world_semicolon",
        "category": "c_syntax",
        "target_file": "samples/hello_world/src/main.c",
        "operator": "c_remove_semicolon",
        "target_app": "samples/hello_world",
        "board": "native_sim",
    },
    {
        "id_suffix": "c_hello_world_brace",
        "category": "c_syntax",
        "target_file": "samples/hello_world/src/main.c",
        "operator": "c_remove_closing_brace",
        "target_app": "samples/hello_world",
        "board": "native_sim",
    },
    # --- Runtime Crashes ---
    # tests/subsys/fs/fcb 已在既有的挖礦驗證中確認過能在 native_sim 上
    # 完整建置並跑完整組 ztest (見 bug_111891 的驗證紀錄)，因此是執行期
    # 崩潰類別最有把握的注入目標：mutation 一定會被測試套件實際執行到。
    # tests/subsys/fs/fcb was already confirmed (via bug_111891's mined-case
    # verification) to fully build and run its ztest suite on native_sim, so
    # it's the most reliable injection target for the runtime-crash category
    # — the mutation is guaranteed to actually be exercised by the tests.
    # 樸素套用 runtime_off_by_one 抓到的第一個「真正」比較式是
    # fcb_append() 裡的一個安全餘裕檢查 (sector->fs_size < ...)，改嚴格一格
    # 只會讓函式更早回傳 -ENOSPC，屬於不痛不癢的保守方向，實測完全不影響
    # ztest 套件的結果 (PROJECT EXECUTION SUCCESSFUL)。真正會被
    # fcb_test_rotate/fcb_test_append 等測試實際命中、且改壞了會出問題的，
    # 是 fcb_new_sector() 迴圈邊界 `while (i++ < cnt)`——用 postinc_loop_bound
    # hint 鎖定這個特定寫法。
    # The first "real" comparison the naive scan finds is a safety-margin
    # check in fcb_append() (sector->fs_size < ...); tightening it by one
    # merely makes the function return -ENOSPC a bit earlier — harmless, and
    # empirically doesn't affect the ztest suite's outcome at all (still
    # PROJECT EXECUTION SUCCESSFUL). The loop bound `while (i++ < cnt)` in
    # fcb_new_sector() is what's actually exercised by
    # fcb_test_rotate/fcb_test_append and breaks things when perturbed — the
    # postinc_loop_bound hint pins the mutation to that specific idiom.
    {
        "id_suffix": "runtime_fcb_offbyone",
        "category": "runtime_crash",
        "target_file": "subsys/fs/fcb/fcb_append.c",
        "operator": "runtime_off_by_one:postinc_loop_bound",
        "target_app": "tests/subsys/fs/fcb",
        "board": "native_sim",
    },
    {
        "id_suffix": "runtime_fcb_nullcheck",
        "category": "runtime_crash",
        "target_file": "subsys/fs/fcb/fcb_getnext.c",
        "operator": "runtime_remove_null_check",
        "target_app": "tests/subsys/fs/fcb",
        "board": "native_sim",
    },
    # --- Semantic Mutation Operators (thesis-proposal-mandated, 2026-07-26 revision) ---
    # DTS reg range 邊界 off-by-one：native_sim 上的路已證實走不通——native
    # 的 host-simulator 驅動 (flash_simulator/eeprom_simulator/otp_emulator)
    # 全部都用「backing buffer 大小」跟「執行期邊界檢查」共用同一個 DT
    # 運算式，reg 改多少兩邊就跟著變多少，永遠不會出現真正的 OOB 縫隙；像
    # fcb/NVS 這類用硬編碼 C 常數的地方，DTS 的 reg 編輯又完全碰不到那段
    # 邏輯。改探 QEMU 真實 SoC 板子 (qemu_cortex_m3/TI LM3S6965)：
    # tests/drivers/gpio/gpio_mmio_latch 的 qemu_cortex_m3.overlay 把
    # sram0 的 reg 縮小成 0xff00 (65280 bytes)，特意在真實 SRAM 頂端保留
    # 0x100 bytes 給一個假的 "gpio-mmio-latch" 暫存器 (reg=<0x2000ff00 4>)，
    # 該驅動直接 sys_write32/sys_read32 存取這個位址，完全沒有邊界檢查。
    # 把這個位址加上 0x100 (= 0x20010000，剛好是這顆 MCU 真實 64KB SRAM
    # 的實體終點) 之後，經實測 (west build -t run，讀完整日誌) 確認：
    # mutate 端 5 個 ztest 全部因為 sys_read32 讀到錯誤的值而斷言失敗，印出
    # "PROJECT EXECUTION FAILED" (qemu_oracle.py 既有的 crash_patterns 之
    # 一)；revert 端 5 個全過，印出 "PROJECT EXECUTION SUCCESSFUL"。這是一
    # 個貨真價實的執行期記憶體存取錯誤 (讀寫了不該讀寫的位址)，不是建置期
    # 語法/binding 檢查擋下來的錯誤。
    # DTS reg-range-boundary off-by-one: the native_sim route is a proven
    # dead end — its host-simulator drivers (flash_simulator/
    # eeprom_simulator/otp_emulator) all derive the backing buffer's size
    # AND the runtime bounds check from the exact same DT expression, so a
    # reg edit moves both in lockstep and never opens a real OOB gap; places
    # using hardcoded C constants instead (fcb, NVS) are never reached by a
    # DTS reg edit at all. Pivoted to a real QEMU SoC board
    # (qemu_cortex_m3 / TI LM3S6965) instead:
    # tests/drivers/gpio/gpio_mmio_latch's qemu_cortex_m3.overlay shrinks
    # sram0's reg to 0xff00 (65280 bytes), deliberately reserving 0x100
    # bytes at the top of *real* SRAM for a fake "gpio-mmio-latch" register
    # (reg=<0x2000ff00 4>) that the driver accesses via a raw, unguarded
    # sys_write32/sys_read32. Adding 0x100 to that address (-> 0x20010000,
    # exactly this MCU's true 64KB SRAM end) was empirically verified
    # (west build -t run, full raw log read): the mutated side fails all 5
    # ztests on a wrong sys_read32 value and prints "PROJECT EXECUTION
    # FAILED" (already one of qemu_oracle.py's crash_patterns); the
    # reverted side passes all 5 and prints "PROJECT EXECUTION SUCCESSFUL".
    # This is a genuine runtime memory-access fault (reading/writing an
    # address it shouldn't), not a build-time syntax/binding rejection.
    {
        "id_suffix": "dts_gpio_latch_offbyone",
        "category": "runtime_crash",
        "target_file": "tests/drivers/gpio/gpio_mmio_latch/boards/qemu_cortex_m3.overlay",
        "operator": "dts_reg_offbyone:0x2000ff00:0x100",
        "target_app": "tests/drivers/gpio/gpio_mmio_latch",
        "board": "qemu_cortex_m3",
    },
    # 執行緒優先權對調：tests/kernel/sched/schedule_api 的
    # test_sched_priority.c 裡，test_priority_preemptible_wait_prio 建立
    # 4 個執行緒，tid[0]/tid[1] 用 K_PRIO_PREEMPT(0)、tid[2]/tid[3] 用
    # K_PRIO_PREEMPT(1)，並斷言實際執行順序精準等於
    # tid_chk={0,1,2,3} (memcmp)。把 tid[0] 跟 tid[2] 的優先權對調
    # (K_PRIO_PREEMPT(0):K_PRIO_PREEMPT(1) 這個 hint 精準命中檔案裡這兩個
    # 值「各自第一次出現」的位置，不會誤觸 tid[1]/tid[3] 共用的相同字面
    # 值) 之後，tid[1] 跟 tid[2] 變成同一優先權，且 tid[2] 的等待時間比
    # tid[1] 短 (K_MSEC(10) vs K_MSEC(20))，照排程規則會搶先於 tid[1]
    # 執行，讓實際順序偏離 tid_chk。已用 west build -t run 實測驗證
    # (讀完整日誌)：mutate 端只有這一個 test case 失敗 (斷言訊息精準是
    # "scheduling priority failed")，同一個 binary 裡其餘 27 個測試案例
    # 全過；revert 端全部通過、印出 PROJECT EXECUTION SUCCESSFUL。這是
    # 語法完全合法、但排程結果錯誤的執行期行為缺陷，不是編譯/建置期錯誤。
    # Thread priority swap: in tests/kernel/sched/schedule_api's
    # test_sched_priority.c, test_priority_preemptible_wait_prio creates 4
    # threads — tid[0]/tid[1] at K_PRIO_PREEMPT(0), tid[2]/tid[3] at
    # K_PRIO_PREEMPT(1) — and asserts the actual run order exactly matches
    # tid_chk={0,1,2,3} (via memcmp). Swapping tid[0]'s and tid[2]'s
    # priorities (the "K_PRIO_PREEMPT(0):K_PRIO_PREEMPT(1)" hint pins
    # exactly each value's first occurrence in the file, so it can't
    # mis-hit the same literal values shared by tid[1]/tid[3]) makes
    # tid[1] and tid[2] tie at the same priority, with tid[2] having
    # waited less time (K_MSEC(10) vs K_MSEC(20)) — per the scheduler's
    # tie-break rule it now runs ahead of tid[1], diverging from
    # tid_chk. Empirically verified with west build -t run (full raw log
    # read): on the mutated side, only this one test case fails (assertion
    # message is precisely "scheduling priority failed"), the other 27
    # test cases in the same binary all still pass; the reverted side
    # passes everything and prints PROJECT EXECUTION SUCCESSFUL. A
    # syntactically valid but behaviorally wrong runtime scheduling defect,
    # not a compile/build-time error.
    {
        "id_suffix": "thread_priority_swap_sched",
        "category": "runtime_crash",
        "target_file": "tests/kernel/sched/schedule_api/src/test_sched_priority.c",
        "operator": "thread_priority_swap:K_PRIO_PREEMPT(0):K_PRIO_PREEMPT(1)",
        "target_app": "tests/kernel/sched/schedule_api",
        "board": "native_sim",
    },
    # API 替換 (k_sleep <-> k_yield)：同一個 test app 的
    # test_sched_timeslice_and_lock.c 裡，test_sleep_cooperative 建立 3 個
    # 執行緒 (優先權分別比目前執行緒高、相同、低)，呼叫 k_sleep(K_MSEC(100))
    # 之後斷言 3 個都執行過了。同檔案裡緊接在前面的 test_yield_cooperative
    # 用的是完全一樣的 setup，只是呼叫 k_yield()，而且明確斷言優先權較低
    # 的那個執行緒「不會」被執行——這正是 Zephyr 自己的測試套件已經記錄
    # 下來的 k_sleep/k_yield 語意差異，不用臆測。把 test_sleep_cooperative
    # 裡的 k_sleep(K_MSEC(100)) 換成 k_yield() (用 test_name 鎖定，因為同一
    # 個檔案裡 test_lock_preemptible 也有一模一樣字面文字的
    # k_sleep(K_MSEC(100)) 呼叫，樸素文字比對會抓錯測試案例)，已用
    # west build -t run 實測驗證兩次、結果完全一致：mutate 端
    # test_sleep_cooperative 先如預期斷言失敗，但因為那個被餓死的低優先權
    # 執行緒從未被排程執行、卻仍被 teardown 呼叫 k_thread_abort()，殘留的
    # 排程狀態接著讓後面幾個共用同一組靜態執行緒陣列的測試案例接連失敗，
    # 最終在 test_unlock_nested_sched_lock 觸發 Segmentation fault (已是
    # qemu_oracle.py 既有的 crash pattern)，整個 process 崩潰結束。這是
    # starvation 造成的真實連鎖失效，比單一斷言失敗更貼近真實世界裡「餓死
    # 的執行緒污染共用資源、在別處才真正炸掉」的診斷難度；revert 端跑兩次
    # 都乾淨通過、印出 PROJECT EXECUTION SUCCESSFUL，且用 diff 確認還原後
    # 的檔案內容跟原始檔逐位元組相同。
    # API substitution (k_sleep <-> k_yield): in the same test app's
    # test_sched_timeslice_and_lock.c, test_sleep_cooperative creates 3
    # threads (priority higher/equal/lower than the current thread), calls
    # k_sleep(K_MSEC(100)), then asserts all 3 ran. The immediately
    # preceding test in the same file, test_yield_cooperative, uses the
    # *identical* setup but calls k_yield() instead, and explicitly asserts
    # the lower-priority thread does *not* run — this is the exact
    # k_sleep/k_yield semantic difference already documented by Zephyr's
    # own test suite, no guesswork needed. Swapping
    # test_sleep_cooperative's k_sleep(K_MSEC(100)) for k_yield() (pinned
    # via test_name, since test_lock_preemptible in the same file has a
    # textually identical k_sleep(K_MSEC(100)) call that a naive text match
    # would mis-hit) was empirically verified twice with west build -t run,
    # with identical results both times: on the mutated side,
    # test_sleep_cooperative fails its assertion as expected first, but
    # because the starved lower-priority thread was never scheduled yet
    # still gets k_thread_abort()'d during teardown, the leftover
    # scheduling state cascades into failing several subsequent test cases
    # that share the same static thread arrays, ultimately triggering a
    # Segmentation fault (already one of qemu_oracle.py's crash patterns)
    # in test_unlock_nested_sched_lock that crashes the whole process. This
    # is a genuine starvation-driven cascading failure — a harder, more
    # realistic diagnostic case than an isolated single-assertion failure,
    # closer to how a starved thread corrupting shared state actually
    # manifests in real embedded systems (the crash surfaces somewhere
    # else entirely). The reverted side passed cleanly both runs (PROJECT
    # EXECUTION SUCCESSFUL), and a diff confirmed the reverted file is
    # byte-identical to the original.
    {
        "id_suffix": "api_substitute_sleep_yield",
        "category": "runtime_crash",
        "target_file": "tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c",
        "operator": "c_api_substitute:test_sleep_cooperative:k_sleep(K_MSEC(100));:k_yield();",
        "target_app": "tests/kernel/sched/schedule_api",
        "board": "native_sim",
    },
]


class ZephyrBugMiner:
    """
    自動探勘 Zephyr 官方儲存庫中的真實 Bug 案例，用於建構 Zephyr-Eval 基準測試。
    """
    def __init__(self):
        self.repo = "zephyrproject-rtos/zephyr"
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {GITHUB_TOKEN}" if GITHUB_TOKEN else ""
        }
        if not GITHUB_TOKEN:
            logger.warning("未偵測到 GITHUB_TOKEN，API 請求將受到嚴格的速率限制 (60次/小時)。")
        # 快取 GitHub contents API 查詢結果，避免對同一路徑重複發送請求
        self._path_exists_cache = {}

    def search_merged_bug_prs(self, max_results: int = 150, merged_after: str = "2026-03-20") -> list:
        """
        搜尋已合併 (is:merged)、標籤包含 bug (label:bug) 的 Pull Requests。
        支援分頁 (每頁最多 100 筆)，以取得足夠的候選案例。
        Searches for merged PRs labeled `bug`, paginating (100/page) to gather
        enough raw candidates.

        :param merged_after: 只保留這個日期之後合併的 PR (預設 2026-03-20，也就是
            Zephyr commit d204d248769 把最低 Zephyr SDK 需求 bump 到 1.0 之後幾天)。
            太舊的 commit 需要舊版 SDK (如 0.16)，會在我們固定用新版 SDK 的沙盒環境裡
            於 CMake 設定階段就失敗，這是環境版本不合造成的假陽性，不是真正的 bug 重現。
            Only keep PRs merged after this date (default 2026-03-20, a few days
            after Zephyr commit d204d248769 bumped the minimum Zephyr SDK
            requirement to 1.0). Older commits need an older SDK (e.g. 0.16) and
            will fail at CMake configure in our fixed-SDK-version sandbox — an
            environment-version false positive, not a real bug repro.
        """
        logger.info(f"🔍 開始搜尋 {self.repo} 中的 Bug 案例 (目標: {max_results} 筆，merged>={merged_after})...")

        query = f"repo:{self.repo} is:pr is:merged label:bug merged:>={merged_after}"
        items = []
        per_page = 100
        page = 1

        while len(items) < max_results:
            url = (
                f"https://api.github.com/search/issues?q={query}"
                f"&sort=updated&order=desc&per_page={per_page}&page={page}"
            )
            response = requests.get(url, headers=self.headers)
            if response.status_code != 200:
                logger.error(f"搜尋失敗 (page {page}): {response.text}")
                break

            page_items = response.json().get("items", [])
            if not page_items:
                break

            items.extend(page_items)
            logger.info(f"   ↳ 第 {page} 頁: 累計 {len(items)} 個潛在的 PR。")
            page += 1

            # GitHub Search API 的速率限制較嚴格 (30/分鐘)，稍作停頓
            time.sleep(2)

        logger.info(f"✅ 共找到 {len(items)} 個潛在的 PR。")
        return items[:max_results]

    def filter_and_extract_pr_details(self, pr_items: list, max_modified_files: int = 3) -> list:
        """
        過濾 PR，只保留修改過 .c, .conf, Kconfig 或 .dts/.overlay 檔案，
        且修改檔案數量精簡 (預設 <=3) 的案例，以確保黃金修補程式聚焦、易於評估。
        並提取其損壞提交 (Broken Commit) 與黃金修補 (Golden Patch)，
        同時猜測錯誤分類、目標測試應用程式與開發板。

        Filters PRs to ones touching .c/.conf/Kconfig/.dts/.overlay files with a
        small, focused changeset (<=3 files by default), extracts the broken/fixed
        commits, and guesses the bug category, target_app, and board.
        """
        valid_cases = []

        for item in pr_items:
            pr_number = item["number"]
            logger.info(f"⏳ 正在分析 PR #{pr_number}: {item['title']}")

            pr_url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}"
            pr_resp = requests.get(pr_url, headers=self.headers)
            if pr_resp.status_code != 200:
                continue
            pr_data = pr_resp.json()
            broken_commit = pr_data["base"]["sha"]
            fixed_commit = pr_data["head"]["sha"]

            files_url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/files"
            files_resp = requests.get(files_url, headers=self.headers)
            if files_resp.status_code != 200:
                continue
            files_data = files_resp.json()
            modified_files = [f["filename"] for f in files_data]

            has_relevant_files = any(
                f.endswith(".c") or f.endswith(".h") or
                f.endswith(".conf") or "Kconfig" in f or
                f.endswith(".dts") or f.endswith(".dtsi") or f.endswith(".overlay")
                for f in modified_files
            )

            if not has_relevant_files:
                logger.info("   ⏭️ 無相關檔案，跳過此 PR。")
                time.sleep(0.5)
                continue

            if len(modified_files) > max_modified_files:
                logger.info(f"   ⏭️ 修改檔案數過多 ({len(modified_files)} > {max_modified_files})，跳過以確保修補聚焦。")
                time.sleep(0.5)
                continue

            category = self._guess_category(modified_files, item.get("title", ""), item.get("body", "") or "")
            board = self._guess_board(modified_files)
            target_app = self._guess_target_app(modified_files)

            logger.info(f"   🎯 找到相關檔案！分類: {category} | 開發板: {board} | 目標 App: {target_app}")
            valid_cases.append({
                "id": f"bug_{pr_number}",
                "title": item["title"],
                "url": item["html_url"],
                "broken_commit": broken_commit,
                "fixed_commit": fixed_commit,
                "modified_files": modified_files,
                "category": category,
                "target_app": target_app,
                "board": board,
            })

            # 避免觸發 API 限制
            time.sleep(1)

        return valid_cases

    def _guess_category(self, modified_files: list, title: str, body: str) -> str:
        """
        依照修改檔案類型與 PR 標題/內文關鍵字，猜測錯誤分類。
        Guesses the bug category from modified file types and PR title/body keywords.

        注意：真正的「C 語言語法錯誤」極少出現在已合併的歷史紀錄中 (CI 會在合併前擋下)，
        因此這裡的 'c_bug' 代表已修復的 C 邏輯/執行期錯誤，語法錯誤案例建議另外用合成注入方式產生。
        Note: literal C *syntax* errors almost never appear in merged history (CI blocks
        them pre-merge), so 'c_bug' here means a fixed C logic/runtime bug. True syntax-error
        cases should be generated synthetically instead of mined.
        """
        text = f"{title} {body}".lower()
        has_dts = any(f.endswith((".dts", ".dtsi", ".overlay")) for f in modified_files)
        has_kconfig = any("kconfig" in f.lower() or f.endswith(".conf") for f in modified_files)
        has_crash_kw = any(kw in text for kw in CRASH_KEYWORDS)
        has_c = any(f.endswith((".c", ".h")) for f in modified_files)

        if has_dts:
            return "dts"
        if has_kconfig:
            return "kconfig"
        if has_crash_kw and has_c:
            return "runtime_crash"
        if has_c:
            return "c_bug"
        return "other"

    def _guess_board(self, modified_files: list) -> str:
        """
        依修改檔案路徑猜測適合的開發板。
        Guesses a suitable board from the modified file paths.

        優先順序 (Priority order):
        1. 若修改的是 boards/<vendor>/<board_name>/ 底下的檔案，直接用該板子本身
           (該 bug 通常就是那塊板子特有的設定問題，換成別的板子根本不會重現)。
        2. 否則依架構關鍵字對照表猜測 QEMU 板子。
        3. 都猜不到則退回 native_sim。
        """
        for f in modified_files:
            match = BOARD_PATH_RE.search(f)
            if match:
                return match.group(1)

        joined = " ".join(modified_files).lower()
        for pattern, board in ARCH_BOARD_MAP:
            if pattern.search(joined):
                return board
        return "native_sim"

    def _is_buildable_app(self, path: str) -> bool:
        """
        檢查該路徑是否為「可直接建置」的 Zephyr 應用程式根目錄，
        判斷依據是該路徑底下是否存在 CMakeLists.txt (單純的父目錄不算)。
        Checks whether a path is a directly-buildable Zephyr app root by
        checking for a CMakeLists.txt at that exact path (a plain parent
        directory without one is not buildable via `west build <dir>`).
        """
        if path in self._path_exists_cache:
            return self._path_exists_cache[path]
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}/CMakeLists.txt"
        resp = requests.get(url, headers=self.headers)
        exists = resp.status_code == 200
        self._path_exists_cache[path] = exists
        return exists

    def _guess_target_app(self, modified_files: list) -> str:
        """
        嘗試在 tests/ 底下尋找與修改檔案路徑對應、且真的可建置 (含 CMakeLists.txt) 的測試應用程式，
        找不到則退回 samples/hello_world (無法重現的案例會在 verify_cases.py 階段被自動捨棄)。
        Tries to find a tests/ directory mirroring the modified file's path that is
        actually buildable (has a CMakeLists.txt), falling back to samples/hello_world
        (unreproducible cases are discarded automatically during verify_cases.py).
        """
        for f in modified_files:
            parts = f.split("/")
            # 由最深的目錄逐層往上嘗試 (例如 subsys/fs/fcb/fcb.c -> tests/subsys/fs/fcb -> tests/subsys/fs)
            for depth in range(len(parts) - 1, 0, -1):
                candidate = "tests/" + "/".join(parts[:depth])
                if self._is_buildable_app(candidate):
                    return candidate
        return "samples/hello_world"

    def _resolve_main_commit(self) -> str:
        """解析 main 分支目前的 tip commit SHA，作為所有合成注入案例共用的固定 baseline。"""
        url = f"https://api.github.com/repos/{self.repo}/commits/main"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json()["sha"]

    def generate_injection_candidates(self, catalog: list = None) -> list:
        """
        根據預先註冊的 mutation catalog (INJECTION_CATALOG)，產生合成錯誤
        注入候選案例。不需要呼叫 GitHub PR 搜尋 API，只需要解析一次
        baseline commit (main 分支目前的 tip)，所有案例共用同一個 commit，
        徹底避開挖礦時遇到的 SDK/Python 版本漂移問題。

        每筆候選都還沒經過驗證——實際能不能用，交給
        verify_cases.py 的雙向驗證閘 (FaultInjector) 判斷。

        Generates synthetic fault-injection candidates from the pre-registered
        mutation catalog. Doesn't need the PR search API — just resolves the
        baseline commit once (the current tip of main), shared by every
        candidate, entirely avoiding the SDK/Python version drift problem
        seen during mining. Each candidate is unverified until it passes
        verify_cases.py's two-sided gate (FaultInjector).
        """
        if catalog is None:
            catalog = INJECTION_CATALOG

        baseline_commit = self._resolve_main_commit()
        logger.info(f"📌 使用 baseline commit: {baseline_commit}")

        cases = []
        for entry in catalog:
            case_id = f"inject_{entry['id_suffix']}"
            cases.append({
                "id": case_id,
                "title": f"[Injected] {entry['category']}: {entry['operator']} on {entry['target_file']}",
                "category": entry["category"],
                "broken_commit": baseline_commit,
                "fixed_commit": baseline_commit,
                "target_app": entry["target_app"],
                "board": entry["board"],
                "injection": {
                    "target_file": entry["target_file"],
                    "operator": entry["operator"],
                },
            })

        logger.info(f"🧬 產生了 {len(cases)} 筆合成注入候選案例 (尚未驗證)。")
        return cases

    def save_dataset(self, cases: list, output_path: str):
        """將提取的案例儲存為 JSON 檔案"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(cases, f, indent=4, ensure_ascii=False)
        logger.info(f"💾 資料集已儲存至: {output_path} (共 {len(cases)} 筆)")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mine merged bug-fix PRs from the Zephyr repo, or generate synthetic fault-injection candidates, for Zephyr-Eval.")
    parser.add_argument("--mode", choices=["mine", "inject"], default="mine",
                        help="'mine' (default): search real GitHub PRs. 'inject': generate synthetic fault-injection candidates from the pre-registered INJECTION_CATALOG.")
    parser.add_argument("--max-results", type=int, default=150, help="[mine mode] Number of raw PRs to search before filtering")
    parser.add_argument("--max-modified-files", type=int, default=3, help="[mine mode] Skip PRs touching more than this many files")
    parser.add_argument("--output", default=None, help="Output JSON filename under dataset/cases/ (default: zephyr_bugs.json for mine, zephyr_injected_candidates.json for inject)")
    parser.add_argument("--exclude-existing", default=None, help="[mine mode] JSON filename under dataset/cases/ whose ids should be skipped (avoids re-fetching PRs already mined)")
    args = parser.parse_args()

    miner = ZephyrBugMiner()
    from collections import Counter

    if args.mode == "inject":
        valid_cases = miner.generate_injection_candidates()
        output_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cases", args.output or "zephyr_injected_candidates.json"))
        miner.save_dataset(valid_cases, output_file)
        counts = Counter(c["category"] for c in valid_cases)
        logger.info(f"📊 分類統計: {dict(counts)}")
        logger.info("⚠️ 這些是尚未驗證的候選案例，請接著執行 verify_cases.py 跑雙向驗證閘。")
    else:
        exclude_ids = set()
        if args.exclude_existing:
            exclude_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cases", args.exclude_existing))
            if os.path.exists(exclude_path):
                with open(exclude_path, "r", encoding="utf-8") as f:
                    exclude_ids = {c["id"] for c in json.load(f)}
                logger.info(f"🚫 將排除 {len(exclude_ids)} 個已存在於 {args.exclude_existing} 的候選 PR。")

        raw_prs = miner.search_merged_bug_prs(max_results=args.max_results)
        if exclude_ids:
            before = len(raw_prs)
            raw_prs = [item for item in raw_prs if f"bug_{item['number']}" not in exclude_ids]
            logger.info(f"   ↳ 排除後剩 {len(raw_prs)}/{before} 個待分析的 PR。")

        valid_cases = miner.filter_and_extract_pr_details(raw_prs, max_modified_files=args.max_modified_files)

        output_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cases", args.output or "zephyr_bugs.json"))
        miner.save_dataset(valid_cases, output_file)

        # 分類統計 (Category breakdown)
        counts = Counter(c["category"] for c in valid_cases)
        logger.info(f"📊 分類統計: {dict(counts)}")
