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
    # --- Scaling round: applying the 3 semantic operators to new targets for diversity ---
    # thread_priority_swap #2：tests/kernel/semaphore/semaphore 的
    # test_sem_take_multiple 建立 4 個不同優先權的執行緒搶同一個
    # multiple_thread_sem，測試依序驗證「哪個優先權/等待時間組合的執行緒
    # 先拿到 sem」。把 sem_tid_1 (K_PRIO_PREEMPT(3)，"low") 跟 sem_tid_3
    # (K_PRIO_PREEMPT(1)，"high_prio_long"，第一個出現的 K_PRIO_PREEMPT(1)，
    # sem_tid_4 也用同一個值但不會被誤觸) 的優先權對調，實測 (west build
    # -t run) 只讓 test_sem_take_multiple 這一個案例失敗，同一支 binary
    # 裡其餘 20 個案例全過；revert 端用 diff 確認還原後與原始檔逐位元組
    # 相同、重新建置執行也全部通過。
    # thread_priority_swap #2: tests/kernel/semaphore/semaphore's
    # test_sem_take_multiple creates 4 threads at distinct priorities all
    # racing for the same multiple_thread_sem, with the test asserting a
    # precise priority/wait-time-based winner order at each step. Swapping
    # sem_tid_1's (K_PRIO_PREEMPT(3), "low") priority with sem_tid_3's
    # (K_PRIO_PREEMPT(1), "high_prio_long" — the first occurrence of
    # K_PRIO_PREEMPT(1); sem_tid_4 uses the same value but isn't touched)
    # was empirically verified (west build -t run) to fail exactly
    # test_sem_take_multiple while the other 20 test cases in the same
    # binary all still pass; the reverted side was confirmed byte-identical
    # to the original via diff and rebuilds/runs clean.
    {
        "id_suffix": "thread_priority_swap_semaphore",
        "category": "runtime_crash",
        "target_file": "tests/kernel/semaphore/semaphore/src/main.c",
        "operator": "thread_priority_swap:K_PRIO_PREEMPT(3):K_PRIO_PREEMPT(1)",
        "target_app": "tests/kernel/semaphore/semaphore",
        "board": "native_sim",
    },
    # dts_reg_offbyone #2：tests/drivers/retained_mem/api 的
    # qemu_cortex_m3.overlay 跟 gpio_mmio_latch 是同一種手法——把 sram0 的
    # reg 縮小成 DT_SIZE_K(60)，在真實 64KB SRAM 頂端保留 0x20 bytes 給
    # "zephyr,retained-ram" 裝置 (reg=<0x2000ffe0 0x20>)。這個驅動
    # (drivers/retained_mem/retained_mem_zephyr_ram.c) 的 read/write/clear
    # 直接對 DT_REG_ADDR 轉型出來的 raw pointer 做 memcpy/memset，完全沒有
    # 邊界檢查。把位址加上自己的大小 (0x20) 推到 0x20010000 (這顆 MCU 真實
    # SRAM 的實體終點，跟 gpio_mmio_latch 案例是同一個邊界) 之後，實測
    # (west build -t run) 確認 test_read_write/test_clear 兩個會實際做
    # memcpy 的案例都因為讀回資料對不上而斷言失敗，不涉及記憶體存取的
    # test_size 則不受影響、正常通過；revert 端 diff 確認逐位元組相同、
    # 重新建置執行三個案例全過。
    # dts_reg_offbyone #2: tests/drivers/retained_mem/api's
    # qemu_cortex_m3.overlay uses the exact same technique as
    # gpio_mmio_latch — sram0's reg is shrunk to DT_SIZE_K(60), reserving
    # 0x20 bytes at the top of *real* 64KB SRAM for a "zephyr,retained-ram"
    # device (reg=<0x2000ffe0 0x20>). Its driver
    # (drivers/retained_mem/retained_mem_zephyr_ram.c) does raw
    # memcpy/memset straight through a pointer cast from DT_REG_ADDR, with
    # zero bounds checking. Adding the region's own size (0x20) to push the
    # address to 0x20010000 (this MCU's true SRAM end — the same boundary
    # as the gpio_mmio_latch case) was empirically verified (west build -t
    # run): both test_read_write and test_clear (which actually memcpy)
    # fail on a data mismatch, while test_size (no memory access) is
    # unaffected and passes; the reverted side was confirmed byte-identical
    # via diff and all 3 test cases pass on rebuild.
    {
        "id_suffix": "dts_retained_mem_offbyone",
        "category": "runtime_crash",
        "target_file": "tests/drivers/retained_mem/api/boards/qemu_cortex_m3.overlay",
        "operator": "dts_reg_offbyone:0x2000ffe0:0x20",
        "target_app": "tests/drivers/retained_mem/api",
        "board": "qemu_cortex_m3",
    },
    # c_api_substitute #2：tests/kernel/mutex/mutex_api 的
    # test_mutex_recursive (test_mutex_apis.c) 遞迴鎖住一個 mutex 兩次，
    # 期間讓一個優先權 K_PRIO_PREEMPT(12) 的等待執行緒 (明顯比預設的 ztest
    # 主執行緒優先權低) 卡在 mutex 上；主執行緒解鎖兩次後呼叫
    # `k_sleep(K_MSEC(1));` (註解直接寫「Give thread_waiter a chance to
    # get the mutex」)，才斷言 `thread_ret == TC_PASS`。換成 `k_yield();`
    # 後，這個明顯較低優先權的等待執行緒完全排不上，跟
    # test_sched_timeslice_and_lock.c 的第一個案例是同一種語意差異，但這次
    # 影響範圍更乾淨：實測 (west build -t run) 只有 test_mutex_recursive
    # 這一個案例失敗，同一支 binary 裡橫跨 mutex_api/mutex_api_1cpu 兩個
    # suite 共 10 個其他案例全過，沒有級聯效應；revert 端 diff 確認逐位元
    # 組相同、重新建置執行全過。
    # c_api_substitute #2: tests/kernel/mutex/mutex_api's
    # test_mutex_recursive (test_mutex_apis.c) locks a mutex recursively
    # twice while a K_PRIO_PREEMPT(12) waiter thread (clearly lower
    # priority than the default ztest main thread) blocks on it; after
    # unlocking twice, the main thread calls `k_sleep(K_MSEC(1));` (the
    # comment literally reads "Give thread_waiter a chance to get the
    # mutex") before asserting `thread_ret == TC_PASS`. Substituting
    # `k_yield();` starves that clearly-lower-priority waiter entirely —
    # the same semantic gap as the first api_substitute case, but this time
    # with a much cleaner blast radius: empirically verified (west build -t
    # run) that only test_mutex_recursive fails, while the other 10 test
    # cases spanning the mutex_api/mutex_api_1cpu suites in the same binary
    # all still pass — no cascading failure this time; the reverted side
    # was confirmed byte-identical via diff and rebuilds/runs clean.
    {
        "id_suffix": "api_substitute_mutex_recursive",
        "category": "runtime_crash",
        "target_file": "tests/kernel/mutex/mutex_api/src/test_mutex_apis.c",
        "operator": "c_api_substitute:test_mutex_recursive:k_sleep(K_MSEC(1));:k_yield();",
        "target_app": "tests/kernel/mutex/mutex_api",
        "board": "native_sim",
    },
    # --- Scaling round 2 ---
    # thread_priority_swap #3：tests/kernel/mem_protect/sys_sem 的
    # test_sem_take_multiple 跟先前 tests/kernel/semaphore/semaphore 的
    # 同名測試是同一種結構 (3 個不同優先權的執行緒依序搶 multiple_thread_sem，
    # 逐輪驗證誰先拿到)，但這次是 sys_sem (userspace-safe 系統號誌) 這個
    # 不同的 API/檔案。把 sem_tid (K_PRIO_PREEMPT(3)，low) 跟 sem_tid_2
    # (K_PRIO_PREEMPT(1)，high) 的優先權對調，實測 (west build -t run) 只讓
    # test_sem_take_multiple 這一個案例失敗 (斷言訊息精準是 "Higher
    # priority threads didn't execute")，同一支 binary 裡橫跨
    # sys_sem/sys_sem_1cpu 兩個 suite 共 13 個其他案例全過；revert 端 diff
    # 確認逐位元組相同、重新建置執行全過。
    # thread_priority_swap #3: tests/kernel/mem_protect/sys_sem's
    # test_sem_take_multiple has the same structure as the earlier
    # tests/kernel/semaphore/semaphore test of the same name (3 threads at
    # distinct priorities racing multiple_thread_sem, with a per-round
    # winner check), but against sys_sem (the userspace-safe system
    # semaphore), a different API/file. Swapping sem_tid's
    # (K_PRIO_PREEMPT(3), low) priority with sem_tid_2's (K_PRIO_PREEMPT(1),
    # high) was empirically verified (west build -t run) to fail exactly
    # test_sem_take_multiple (assertion message precisely "Higher priority
    # threads didn't execute"), while the other 13 test cases spanning the
    # sys_sem/sys_sem_1cpu suites in the same binary all still pass; the
    # reverted side was confirmed byte-identical via diff and rebuilds/runs
    # clean.
    #
    # Also fixes the naive-first-match trap for this operator itself:
    # thread_priority_swap now supports an optional scope prefix,
    # "<scope_name>@<value_a>:<value_b>" (resolved via _find_ztest_block,
    # same helper c_api_substitute uses), for cases where the same literal
    # priority value already appears earlier in the file in an unrelated
    # function — hit empirically on tests/kernel/msgq/msgq_api (abandoned
    # as a target: the mutation, once correctly scoped, still turned out to
    # have no observable effect there — a legitimate "doesn't matter here"
    # result, not a bug) before landing on this sys_sem target, which
    # didn't need scoping (no earlier conflicting occurrence in the file).
    {
        "id_suffix": "thread_priority_swap_sys_sem",
        "category": "runtime_crash",
        "target_file": "tests/kernel/mem_protect/sys_sem/src/main.c",
        "operator": "thread_priority_swap:K_PRIO_PREEMPT(3):K_PRIO_PREEMPT(1)",
        "target_app": "tests/kernel/mem_protect/sys_sem",
        "board": "native_sim",
    },
    # c_api_substitute #3：tests/kernel/mutex/sys_mutex 的 thread_09 (一個
    # 一般的執行緒進入函式，不是直接寫在 ZTEST(...) 本體裡——用
    # _find_ztest_block 的「一般 C 函式定義」退回比對機制鎖定) 有一行
    # `k_sleep(K_MSEC(500));	/* Allow lower priority thread to run */`，
    # 註解直接寫明用途。換成 `k_yield();` 後，實測 (west build -t run)
    # 讓 test_mutex (mutex_complex suite 裡最複雜的多執行緒案例) 精準地在
    # 「應該還沒能鎖到 mutex_1 卻鎖到了」這個檢查點失敗，其餘
    # test_mutex_multithread_competition/test_supervisor_access 兩個案例
    # 照常通過 (test_user_access 因為 CONFIG_ARCH_HAS_USERSPACE 在這個板子
    # 上本來就是 SKIP，跟這次的注入無關，revert 端也一樣 SKIP)；revert 端
    # diff 確認逐位元組相同、重新建置執行全過。
    # c_api_substitute #3: tests/kernel/mutex/sys_mutex's thread_09 (a
    # plain thread-entry function, not written directly inside a
    # ZTEST(...) body — pinned via _find_ztest_block's "plain C function
    # definition" fallback match) has a
    # `k_sleep(K_MSEC(500));	/* Allow lower priority thread to run */`
    # line, comment stating its purpose explicitly. Substituting
    # `k_yield();` was empirically verified (west build -t run) to fail
    # test_mutex (the most complex multi-thread case in the mutex_complex
    # suite) precisely at the checkpoint that should NOT have been able to
    # lock mutex_1 yet but did; test_mutex_multithread_competition and
    # test_supervisor_access still pass as usual (test_user_access is
    # SKIPped on this board regardless of CONFIG_ARCH_HAS_USERSPACE,
    # unrelated to this injection — same on the reverted side); the
    # reverted side was confirmed byte-identical via diff and rebuilds/runs
    # clean.
    {
        "id_suffix": "api_substitute_sys_mutex_thread09",
        "category": "runtime_crash",
        "target_file": "tests/kernel/mutex/sys_mutex/src/main.c",
        "operator": "c_api_substitute:thread_09:k_sleep(K_MSEC(500));:k_yield();",
        "target_app": "tests/kernel/mutex/sys_mutex",
        "board": "native_sim",
    },
    # --- Scaling round 3: dts_reg_offbyone hit a dead end this round (all
    # candidates checked — mem_attr_heap ruled out statically, arm64
    # high-address tests use a 4-cell reg format the operator doesn't parse
    # and don't fit the boundary-off-by-one semantics anyway, the GPIO
    # aperture-gap idea on qemu_cortex_m3 has no existing consumer test,
    # and the one remaining zephyr,retained-ram user (mps2_an385's mcuboot
    # multiple_keys fixture) is a full sysbuild bootloader+app flow, too
    # complex/risky for the value — so this round's budget went entirely to
    # the other two operators instead, per discussion with the user.
    #
    # c_api_substitute #4：跟前一個 sys_mutex 案例是同一個檔案，但這次是
    # ZTEST_USER_OR_NOT(mutex_complex, test_mutex) 這個最複雜的多執行緒案例
    # 本體，而不是獨立的 thread_XX 函式——ZTEST_USER_OR_NOT 是這個檔案自訂
    # 的巨集 (依 config 展開成 ZTEST_USER 或 ZTEST)，逼得
    # _find_ztest_block 的比對規則從列舉固定巨集名稱改成「只要名稱裡含
    # ZTEST」的通用寫法。目標是
    # `k_sleep(K_MSEC(5));     /* Give thread_12 a chance to block on the
    # mutex */`：thread_12 (K_PRIO_PREEMPT(12)，明顯比目前執行緒優先權低)
    # 需要真的排到 CPU 才能呼叫 sys_mutex_lock() 進入等待佇列；換成
    # k_yield() 後完全排不上，導致主執行緒解鎖兩次後，thread_12 根本沒在
    # 等待佇列裡，讓後面「private mutex 應該還鎖著」的 K_NO_WAIT 檢查點
    # 意外拿到鎖。實測 (west build -t run) 精準命中預期的斷言失敗
    # ("Unexpectedly got lock on private mutex")，其餘 2 個未跳過的案例
    # 全過；revert 端 diff 確認逐位元組相同、重新建置執行全過。
    # c_api_substitute #4: same file as the previous sys_mutex case, but
    # this time the target is the body of
    # ZTEST_USER_OR_NOT(mutex_complex, test_mutex) itself (the most complex
    # multi-thread case in the file), not a standalone thread_XX function.
    # ZTEST_USER_OR_NOT is a locally-defined macro in this file (expands to
    # ZTEST_USER or ZTEST depending on config), which forced
    # _find_ztest_block's matching rule to go from enumerating fixed macro
    # names to a general "anything containing ZTEST" match. Target:
    # `k_sleep(K_MSEC(5));     /* Give thread_12 a chance to block on the
    # mutex */` — thread_12 (K_PRIO_PREEMPT(12), clearly lower priority
    # than the current thread) needs to actually get scheduled to call
    # sys_mutex_lock() and join the wait queue; substituting k_yield()
    # starves it entirely, so after the main thread unlocks the private
    # mutex twice, thread_12 was never in the wait queue, and the later
    # K_NO_WAIT check that expects the mutex to still be held unexpectedly
    # succeeds. Empirically verified (west build -t run) to hit exactly the
    # expected assertion failure ("Unexpectedly got lock on private
    # mutex"), with the other 2 non-skipped test cases in the binary still
    # passing; the reverted side was confirmed byte-identical via diff and
    # rebuilds/runs clean.
    {
        "id_suffix": "api_substitute_sys_mutex_test_mutex",
        "category": "runtime_crash",
        "target_file": "tests/kernel/mutex/sys_mutex/src/main.c",
        "operator": "c_api_substitute:test_mutex:k_sleep(K_MSEC(5));:k_yield();",
        "target_app": "tests/kernel/mutex/sys_mutex",
        "board": "native_sim",
    },
    # --- Scaling round 4 ---
    # thread_priority_swap #4：tests/kernel/mem_slab/mslab_concept 的
    # test_mslab_alloc_wait_prio 跟先前兩個 "wait_prio" 案例是同一種樣式
    # (最高優先權、等待最久的執行緒先拿到資源)，這次是 k_mem_slab_alloc。
    # tid[0] 是「低優先權，預期逾時拿不到 block (斷言 -EAGAIN)」
    # (K_PRIO_PREEMPT(1))，tid[1]/tid[2] 是「高優先權，預期成功拿到」
    # (K_PRIO_PREEMPT(0))。把 tid[0] 跟 tid[1] 的優先權對調後，tid[0]
    # 變成優先權最高、且從一開始 (K_NO_WAIT 建立，比 tid[1]/tid[2] 的
    # K_MSEC(10)/K_MSEC(20) 延遲都早) 就在等，理當搶到唯一釋出的
    # block——但它執行的函式主體 (tmslab_alloc_wait_timeout) 固定寫死斷言
    # -EAGAIN，於是斷言失敗。實測 (west build -t run) 精準命中預期的
    # 那一行斷言；revert 端 diff 確認逐位元組相同、重新建置執行通過 (這個
    # test app 本來就只有這一個測試案例)。
    # thread_priority_swap #4: tests/kernel/mem_slab/mslab_concept's
    # test_mslab_alloc_wait_prio has the same "highest priority, longest
    # waiting wins" idiom as two earlier cases, this time for
    # k_mem_slab_alloc. tid[0] is "low priority, expected to time out"
    # (K_PRIO_PREEMPT(1), asserts -EAGAIN); tid[1]/tid[2] are "high
    # priority, expected to succeed" (K_PRIO_PREEMPT(0)). Swapping tid[0]'s
    # and tid[1]'s priorities makes tid[0] both the highest priority AND
    # the earliest waiter (created with K_NO_WAIT, ahead of tid[1]/tid[2]'s
    # K_MSEC(10)/K_MSEC(20) delays), so it should now win the single freed
    # block — but its thread body (tmslab_alloc_wait_timeout) still
    # hard-codes an assertion expecting -EAGAIN, so the assertion fails.
    # Empirically verified (west build -t run) to hit exactly that
    # assertion line; the reverted side was confirmed byte-identical via
    # diff and rebuilds/runs clean (this test app has only this one test
    # case).
    {
        "id_suffix": "thread_priority_swap_mslab",
        "category": "runtime_crash",
        "target_file": "tests/kernel/mem_slab/mslab_concept/src/test_mslab_alloc_wait.c",
        "operator": "thread_priority_swap:K_PRIO_PREEMPT(1):K_PRIO_PREEMPT(0)",
        "target_app": "tests/kernel/mem_slab/mslab_concept",
        "board": "native_sim",
    },
    # c_api_substitute #5：同一個 test_sched_timeslice_and_lock.c 檔案，
    # 但這次是反方向的代換——test_unlock_preemptible 原本就用
    # `k_yield();` (註解「ensure threads of equal priority can run」)，
    # 緊接著斷言 tdata[2] (低優先權執行緒) 「沒有」被執行到
    # (executed == 0)。把 k_yield() 換成 k_sleep(K_MSEC(100)) 之後，
    # tdata[2] 反而會真的被排到、變成 executed == 1，讓「不應該執行」的
    # 斷言失敗——這是跟先前 api_substitute_sleep_yield 完全對稱、方向相反
    # 的語意變異 (那個是拿掉本該有的排程機會，這個是多給了不該有的排程
    # 機會)，展示同一個 operator 兩個方向都能製造真正的行為缺陷。實測
    # (west build -t run) 只有 test_unlock_preemptible 這一個案例失敗，
    # 同一支 binary 裡其餘 27 個案例 (含 test_unlock_nested_sched_lock，
    # 結構非常相似但沒被動到) 全過；revert 端 diff 確認逐位元組相同、
    # 重新建置執行全過。
    # c_api_substitute #5: same file as api_substitute_sleep_yield, but
    # the reverse substitution direction — test_unlock_preemptible
    # originally uses `k_yield();` (comment: "ensure threads of equal
    # priority can run"), followed immediately by an assertion that
    # tdata[2] (the lower-priority thread) did *not* run
    # (executed == 0). Substituting k_sleep(K_MSEC(100)) instead lets
    # tdata[2] actually get scheduled and run, flipping it to
    # executed == 1 and failing the "should not have run" assertion — the
    # exact mirror image of api_substitute_sleep_yield (that one removed a
    # scheduling opportunity that should have existed; this one grants one
    # that shouldn't), demonstrating the same operator produces genuine
    # behavioral defects in both directions. Empirically verified (west
    # build -t run) that only test_unlock_preemptible fails, while the
    # other 27 test cases in the same binary (including
    # test_unlock_nested_sched_lock, a structurally very similar but
    # untouched neighbor) all still pass; the reverted side was confirmed
    # byte-identical via diff and rebuilds/runs clean.
    {
        "id_suffix": "api_substitute_unlock_preemptible",
        "category": "runtime_crash",
        "target_file": "tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c",
        "operator": "c_api_substitute:test_unlock_preemptible:k_yield();:k_sleep(K_MSEC(100));",
        "target_app": "tests/kernel/sched/schedule_api",
        "board": "native_sim",
    },
    # --- Scaling round 5 ---
    # 這輪先試了 tests/kernel/queue/src/test_queue_contexts.c 的
    # test_queue_multithread_competition (同一種「最高優先權+等待最久」
    # 樣式，用執行期運算式 "prio + 4"/"prio + 2" 而非字面 K_PRIO_PREEMPT
    # 常數，thread_priority_swap 一樣能處理)：mutation 邏輯正確、實測也精準
    #命中預期的斷言失敗，但後續測試案例 (test_queue_poll_race) 卡住直到
    # 180 秒逾時，判斷是斷言失敗發生在「被喚起的 worker 執行緒本身」而非主
    # 執行緒、而 teardown 用的是 k_thread_join(..., K_FOREVER) (無界等待)
    # 而非 k_thread_abort()，可能導致該執行緒沒有乾淨結束、join 卡死。
    # 已驗證過的 3 個「wait_prio」案例 (schedule_api/semaphore/sys_sem)
    # 的斷言都是主執行緒讀取 side-channel 狀態 (陣列索引、semaphore
    # count)，而 mslab 案例雖然斷言在 worker 執行緒內、但 teardown 用的是
    # k_thread_abort()——這兩種組合都安全。已放棄這個 queue 目標並還原，
    # 記錄這個新的風險判斷準則：「斷言寫在 worker 執行緒本身」加上
    # 「teardown 用 k_thread_join(K_FOREVER)」的組合要避開，除非 join
    # 帶的是有界逾時 (像下面這個 c_api_substitute 案例的 LONG_TIMEOUT)。
    # This round first tried
    # tests/kernel/queue/src/test_queue_contexts.c's
    # test_queue_multithread_competition (same "highest priority + longest
    # wait" idiom, expressed via runtime expressions "prio + 4"/"prio + 2"
    # rather than literal K_PRIO_PREEMPT constants — thread_priority_swap
    # handles that fine): the mutation applied correctly and empirically
    # hit exactly the expected assertion failure, but a *later* test case
    # (test_queue_poll_race) then hung until the 180s timeout. Root cause:
    # the failing assertion lives inside the woken *worker* thread itself
    # (not the main thread), and teardown uses an unbounded
    # k_thread_join(..., K_FOREVER) rather than k_thread_abort() — likely
    # leaving that thread not cleanly finished, hanging the join forever.
    # The 3 already-verified "wait_prio" cases (schedule_api/semaphore/
    # sys_sem) all assert from the *main* thread reading side-channel state
    # (an array index, a semaphore count); mslab's assertion *is* inside a
    # worker thread but its teardown uses k_thread_abort() — both
    # combinations are safe. Abandoned this queue target and reverted;
    # recording the new risk rule: avoid "assertion lives inside a worker
    # thread" + "teardown uses unbounded k_thread_join(K_FOREVER)" together,
    # unless the join carries a bounded timeout (as in the
    # c_api_substitute case below, which uses LONG_TIMEOUT).
    #
    # c_api_substitute #6：tests/kernel/events/event_api 的
    # test_event_reset_on_wait，第一個 sync point。接收端邏輯
    # (reset_on_wait()) 完全沒有 zassert，只更新全域變數 test_events/
    # test_event.events，真正的斷言都在驅動端函式
    # drive_reset_on_wait()——這是安全樣式；且 teardown 是
    # `k_thread_join(&treceiver, LONG_TIMEOUT)` (有界 1 秒逾時)，不是無界
    # K_FOREVER，即使真的卡住也不會拖累整個 suite。接收端執行緒優先權是
    # K_PRIO_PREEMPT(0)，明顯比 ztest 主執行緒預設的 K_PRIO_COOP(-1)
    # (CONFIG_ZTEST_THREAD_PRIORITY 預設值) 低。把
    # `k_sleep(DELAY);  /* Give receiver thread time to run */`
    # 換成 `k_yield();` 後，接收端完全排不上，導致它在
    # k_event_post(&test_event, 0x123) 執行「之後」才第一次真正開始等待
    # ——`k_event_wait_all(..., reset_events=true, ...)` 的 reset 語意讓它
    # 在開始等待時把已經存在的 0x123 直接清空，而不是像原本 (先等、後
    # post) 那樣讓 0x123 落在等待期間乾淨地保留下來。實測 (west build
    # -t run) 精準命中預期的那一行斷言 ("test_event.events == 0x123 is
    # false")，同一支 binary 裡其餘 10 個案例全過；revert 端 diff 確認
    # 逐位元組相同、重新建置執行全過。
    # c_api_substitute #6: tests/kernel/events/event_api's
    # test_event_reset_on_wait, first sync point. The receiver's own logic
    # (reset_on_wait()) has zero zassert calls, it just updates the global
    # test_events/test_event.events state; every actual assertion lives in
    # the driving function drive_reset_on_wait() — the safe shape. Teardown
    # is `k_thread_join(&treceiver, LONG_TIMEOUT)` (a bounded 1s timeout),
    # not unbounded K_FOREVER, so even a genuine hang wouldn't stall the
    # whole suite. The receiver thread runs at K_PRIO_PREEMPT(0), clearly
    # lower than the ztest main thread's default K_PRIO_COOP(-1)
    # (CONFIG_ZTEST_THREAD_PRIORITY's default). Substituting
    # `k_sleep(DELAY);  /* Give receiver thread time to run */` for
    # `k_yield();` completely starves the receiver, so it only starts
    # actually waiting *after* `k_event_post(&test_event, 0x123)` has
    # already run — `k_event_wait_all(..., reset_events=true, ...)`'s reset
    # semantics clear the already-set 0x123 the instant it starts waiting,
    # instead of the original (wait-then-post) ordering where 0x123 lands
    # cleanly during an active wait and survives. Empirically verified
    # (west build -t run) to hit exactly the expected assertion line
    # ("test_event.events == 0x123 is false"), with the other 10 test
    # cases in the same binary all still passing; the reverted side was
    # confirmed byte-identical via diff and rebuilds/runs clean.
    {
        "id_suffix": "api_substitute_event_reset_on_wait",
        "category": "runtime_crash",
        "target_file": "tests/kernel/events/event_api/src/main.c",
        "operator": "c_api_substitute:drive_reset_on_wait:k_sleep(DELAY);:k_yield();",
        "target_app": "tests/kernel/events/event_api",
        "board": "native_sim",
    },
    # --- Scaling round 6 ---
    # thread_priority_swap #5：跟 tests/kernel/mutex/sys_mutex 已經用過的
    # 兩個檔案是同一個 test app，這次是 thread_competition.c 的
    # test_mutex_multithread_competition——又是「最高優先權+等待最久」
    # 樣式 (這次是 sys_mutex_lock)，用執行期運算式 "prio + 4"/"prio + 2"。
    # 這次特別確認過安全性：3 個 worker 執行緒本體
    # (low_prio_wait_for_mutex 等) 完全沒有 zassert，只寫入全域 flag[]
    # 陣列，真正的斷言都在主執行緒 (ZTEST 本體) 讀取 flag[]——即使
    # teardown 用的是無界 k_thread_join(K_FOREVER)，因為 worker 執行緒
    # 本身不會斷言失敗，也就不會有「執行緒沒有乾淨結束」的風險，跟上一輪
    # queue 案例踩到的陷阱不同。把 thread_high_data1 (第一個 "prio + 2")
    # 跟 thread_low_data ("prio + 4") 對調後，實測 (west build -t run)
    # 精準命中一個乾淨的斷言失敗 (flag[1] == HIGH_T2)，其餘
    # test_mutex/test_supervisor_access 兩個案例照常通過；revert 端 diff
    # 確認逐位元組相同、重新建置執行全過。
    # thread_priority_swap #5: same test app as two already-used
    # tests/kernel/mutex/sys_mutex files, this time
    # thread_competition.c's test_mutex_multithread_competition — another
    # "highest priority + longest wait" idiom (this time for
    # sys_mutex_lock), expressed via runtime expressions "prio + 4"/
    # "prio + 2". Specifically verified safety this time: the 3 worker
    # thread bodies (low_prio_wait_for_mutex etc.) have zero zassert calls,
    # they only write to the global flag[] array; every actual assertion
    # lives in the main thread (the ZTEST body itself) reading flag[] —
    # so even though teardown uses unbounded k_thread_join(K_FOREVER),
    # there's no "worker thread doesn't cleanly finish" risk since no
    # worker thread can ever fail its own assertion, unlike last round's
    # queue trap. Swapping thread_high_data1's priority (the first
    # "prio + 2") with thread_low_data's ("prio + 4") was empirically
    # verified (west build -t run) to hit a clean assertion failure
    # (flag[1] == HIGH_T2), with test_mutex/test_supervisor_access still
    # passing normally; the reverted side was confirmed byte-identical via
    # diff and rebuilds/runs clean.
    {
        "id_suffix": "thread_priority_swap_thread_competition",
        "category": "runtime_crash",
        "target_file": "tests/kernel/mutex/sys_mutex/src/thread_competition.c",
        "operator": "thread_priority_swap:prio + 4:prio + 2",
        "target_app": "tests/kernel/mutex/sys_mutex",
        "board": "native_sim",
    },
    # c_api_substitute #7：同一個 test_sched_timeslice_and_lock.c 檔案，
    # 這次是 test_lock_preemptible——結構跟 test_sleep_cooperative 幾乎
    # 一樣 (k_sched_lock() 鎖住排程器、產生 3 個執行緒、確認都還沒執行，
    # 再靠 k_sleep(K_MSEC(100)) 讓包括低優先權的 tdata[2] 在內全部執行)，
    # 但這次是透過排程器鎖定/解鎖 (k_sched_lock/k_sched_unlock) 而非直接
    # 設定執行緒優先權來測試。換成 k_yield() 後，低優先權的 tdata[2]
    # 依然排不上，讓「全部都執行過」的斷言失敗。實測 (west build -t run)
    # 只有 test_lock_preemptible 這一個案例失敗，同一支 binary 裡其餘 27
    # 個案例全過；revert 端 diff 確認逐位元組相同、重新建置執行全過。
    # c_api_substitute #7: same file as several earlier cases, this time
    # test_lock_preemptible — structurally almost identical to
    # test_sleep_cooperative (k_sched_lock() locks the scheduler, spawns 3
    # threads, confirms none ran yet, then k_sleep(K_MSEC(100)) lets all of
    # them run including the lower-priority tdata[2]), but exercised via
    # scheduler lock/unlock rather than direct thread-priority assignment.
    # Substituting k_yield() still starves the lower-priority tdata[2],
    # failing the "all threads ran" assertion. Empirically verified (west
    # build -t run) that only test_lock_preemptible fails, with the other
    # 27 test cases in the same binary all still passing; the reverted
    # side was confirmed byte-identical via diff and rebuilds/runs clean.
    {
        "id_suffix": "api_substitute_lock_preemptible",
        "category": "runtime_crash",
        "target_file": "tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c",
        "operator": "c_api_substitute:test_lock_preemptible:k_sleep(K_MSEC(100));:k_yield();",
        "target_app": "tests/kernel/sched/schedule_api",
        "board": "native_sim",
    },
    # --- Scaling round 7 ---
    # 這輪 thread_priority_swap 沒找到新目標：complex_inversion.c 的 5
    # 執行緒優先權繼承鏈太複雜且優先權是用 CREATE_PARTICIPANT_THREAD(id,
    # pri) 巨集呼叫的純數字參數，直接對調兩個完整呼叫文字只會讓「哪一行先
    # 執行」互換、優先權指派本身不變 (等同於先前 mem_attr_heap 那次 #define
    # 換位置的無效 swap 陷阱)；tests/kernel/stack/stack 的
    # wait_prio 案例斷言在 worker 執行緒內、又是無界 k_thread_join，符合
    # 上一輪歸納的卡住風險組合，直接跳過沒有實測浪費建置週期。
    # No new thread_priority_swap target this round: complex_inversion.c's
    # 5-thread priority-inheritance chain is too intricate, and its
    # priorities are passed as bare numeric arguments to a
    # CREATE_PARTICIPANT_THREAD(id, pri) macro call — swapping the two
    # full macro-call texts would just swap *which line runs first*, not
    # the actual priority assignment (the same "swap NAME+VALUE together"
    # trap identified with mem_attr_heap earlier). tests/kernel/stack/
    # stack's wait_prio case has its assertions inside worker threads
    # combined with an unbounded k_thread_join teardown — exactly the
    # hang-risk combination identified last round — skipped without
    # burning a build cycle on it.
    #
    # c_api_substitute #8：tests/kernel/early_sleep 的唯一測試案例
    # test_early_sleep。共用函式 ticks_to_sleep() 裡的
    # `k_sleep(K_MSEC(k_ticks_to_ms_floor64(ticks)));`
    # 同時被兩個 SYS_INIT 鉤子 (POST_KERNEL/APPLICATION 階段) 跟 ZTEST
    # 本體共用，換成 k_yield() 後，POST_KERNEL 階段那次呼叫實際睡眠時間
    # 趨近於 0，讓「睡眠時長至少達到要求」的斷言 (在 ZTEST 本體檢查
    # SYS_INIT 階段記錄下來的結果) 先失敗，比原本設計要測的「低優先權
    # 執行緒有沒有跑到」那個斷言更早觸發——這個 test app 本來就只有一個
    # 測試案例，仍是乾淨、單一、確定性的失敗。實測 (west build -t run)
    # 精準命中預期；revert 端 diff 確認逐位元組相同、重新建置執行通過。
    # c_api_substitute #8: tests/kernel/early_sleep's sole test case,
    # test_early_sleep. The shared helper function ticks_to_sleep()'s
    # `k_sleep(K_MSEC(k_ticks_to_ms_floor64(ticks)));` is called from both
    # two SYS_INIT hooks (POST_KERNEL/APPLICATION stages) and the ZTEST
    # body itself; substituting k_yield() means the POST_KERNEL-stage call
    # sleeps for approximately zero time, so the "slept at least the
    # required duration" assertion (checked later in the ZTEST body
    # against the SYS_INIT-recorded result) fires first — earlier than the
    # test's own "did the lower-priority thread run" assertion it was
    # originally designed to exercise. This test app has only this one
    # test case, so it's still a clean, single, deterministic failure.
    # Empirically verified (west build -t run) to hit exactly the expected
    # outcome; the reverted side was confirmed byte-identical via diff and
    # rebuilds/runs clean.
    {
        "id_suffix": "api_substitute_early_sleep",
        "category": "runtime_crash",
        "target_file": "tests/kernel/early_sleep/src/main.c",
        "operator": "c_api_substitute:ticks_to_sleep:k_sleep(K_MSEC(k_ticks_to_ms_floor64(ticks)));:k_yield();",
        "target_app": "tests/kernel/early_sleep",
        "board": "native_sim",
    },
    # --- Scaling round 8 ---
    # c_api_substitute #9：tests/kernel/context 的 test_k_yield，一個專門
    # 測試 k_yield() 本身優先權語意的既有測試。真正的 k_yield() 呼叫跟
    # zassert 都寫在 worker 函式 k_yield_entry() 裡，同一個函式範圍內有
    # 兩個一模一樣的 `k_yield();` 呼叫 (一個測「該讓高優先權執行緒跑」，
    # 一個測「不該讓低優先權執行緒跑」)——為了精準命中第二個而非第一個，
    # 幫 c_api_substitute 加了 "<test_name>#N" 這種可選的出現次數語法
    # (N=2)，而不是用內嵌換行的周圍文字當錨點 (那種寫法沒辦法安全穿過
    # fault_injector.py 那段「先被 shlex 解析、再被容器內 bash 解析」的
    # 雙重跳脫管線)。已用單元測試確認新語法運作正確、且不影響既有 (無 #N)
    # 的 hint。實測 (west build -t run，重跑兩次結果一致) 精準命中預期的
    # 那一行斷言 ("k_yield() yielded to a lower priority thread")——但因為
    # test_k_yield 的 ZTEST 本體建立完 worker 執行緒後只給了 3 個
    # semaphore 就直接回傳、沒有 join 等它跑完，這個非同步的斷言失敗被
    # ztest 歸到「剛好在那個時間點執行」的下一個測試 test_timer_interrupts
    # 名下，而不是 test_k_yield 自己——訊息本身完全正確地點名
    # k_yield_entry 跟失敗原因，只是測試套件摘要裡掛的名字對不上，兩次
    # 重跑結果一致 (皆歸到 test_timer_interrupts)，是決定性、可重現的。
    # revert 端 diff 確認逐位元組相同、重新建置執行全過 (5/5 皆
    # PASS，包含 test_timer_interrupts)。
    # c_api_substitute #9: tests/kernel/context's test_k_yield, an existing
    # test dedicated to k_yield()'s own priority semantics. The actual
    # k_yield() calls and zasserts live in the worker function
    # k_yield_entry(), which has two identical `k_yield();` calls in its
    # own scope (one testing "should yield to a higher-priority thread",
    # one testing "should NOT yield to a lower-priority thread") — to
    # precisely hit the second, not the first, added an optional
    # "<test_name>#N" occurrence-index syntax to c_api_substitute (N=2)
    # rather than using surrounding text spanning a newline as an anchor
    # (which can't safely survive fault_injector.py's two-layer parse:
    # shlex.split() first, then the real bash inside the container).
    # Verified the new syntax with unit tests, including that it doesn't
    # affect existing hints without "#N". Empirically verified (west build
    # -t run, re-run twice with identical results) to hit exactly the
    # expected assertion line ("k_yield() yielded to a lower priority
    # thread") — but because test_k_yield's ZTEST body only gives 3
    # semaphores and returns immediately after creating the worker thread,
    # without joining it, this asynchronous assertion failure gets
    # attributed by ztest to whichever test happens to be running next
    # (test_timer_interrupts) rather than test_k_yield itself. The message
    # correctly names k_yield_entry and the real cause; only the test-suite
    # summary's label is misleading, and it's deterministic (both re-runs
    # landed on test_timer_interrupts). The reverted side was confirmed
    # byte-identical via diff and rebuilds/runs clean (5/5 pass, including
    # test_timer_interrupts).
    {
        "id_suffix": "api_substitute_k_yield_lower_prio",
        "category": "runtime_crash",
        "target_file": "tests/kernel/context/src/main.c",
        "operator": "c_api_substitute:k_yield_entry#2:k_yield();:k_sleep(K_MSEC(50));",
        "target_app": "tests/kernel/context",
        "board": "native_sim",
    },
    # tests/kernel/pending/src/main.c defines two file-scope preemptible
    # threads via K_THREAD_DEFINE: TASK_LOW (priority 7) and TASK_HIGH
    # (priority 5). test_pending_fifo puts 4 items on a shared fifo while
    # 4 threads (2 cooperative, 2 preemptible) are all pending on it, and
    # asserts they wake in strict priority order (coop_high, coop_low,
    # task_high, task_low). Swapping TASK_LOW's/TASK_HIGH's priority
    # arguments (the bare "7, 0, 0);" / "5, 0, 0);" tail of each
    # K_THREAD_DEFINE call — unique substrings in this file, so no scope
    # needed) makes task_high now the lower-priority thread while keeping
    # its name/body unchanged. An earlier, unrelated assertion in the same
    # test (fifo *timeout* order) is keyed on each thread's fixed timeout
    # duration, not priority, so it's unaffected — only the later
    # priority-ordered wake-up assertion breaks. Empirically this doesn't
    # surface as a clean zassert failure but as a genuine crash: mutated
    # build segfaults deterministically (2/2 runs) right at
    # test_pending_fifo; reverted build passes all 3 tests cleanly.
    {
        "id_suffix": "thread_priority_swap_pending_fifo",
        "category": "runtime_crash",
        "target_file": "tests/kernel/pending/src/main.c",
        "operator": "thread_priority_swap:7, 0, 0);:5, 0, 0);",
        "target_app": "tests/kernel/pending",
        "board": "native_sim",
    },
    # tests/kernel/common/src/constructor.c is not a thread-scheduling test
    # at all — it verifies GCC __constructor__ attribute priorities run in
    # ascending order before main(). Reused the generic thread_priority_swap
    # operator (it just swaps two literal source-text spans, nothing
    # thread-specific about the implementation) on the two constructors'
    # attribute values themselves: __constructor__(101) <-> __constructor__(
    # 1000). Each constructor's own function body still writes its own
    # fixed identifying value (101 or 1000) into constructor_values[], so
    # swapping only the attribute priority makes the "1000" constructor run
    # first and the "101" one run second, while the test still asserts
    # constructor_values[0] == 101. This is a deterministic, non-race
    # ordering bug (GCC constructor ordering is fixed at compile/link time,
    # no scheduling nondeterminism at all) — a different flavor of "priority
    # reassignment mistake" than the thread-scheduling instances above, but
    # the same fault class the thesis's motivating example describes.
    # Mutated build fails test_constructor deterministically and cleanly
    # (2/2 runs, identical assertion message); every other suite in the
    # binary (byteorder, clock, common, irq_offload, multilib, pow2, ...)
    # still passes. Reverted build passes all suites cleanly.
    {
        "id_suffix": "thread_priority_swap_constructor_order",
        "category": "runtime_crash",
        "target_file": "tests/kernel/common/src/constructor.c",
        "operator": "thread_priority_swap:__constructor__(101))):__constructor__(1000)))",
        "target_app": "tests/kernel/common",
        "board": "native_sim",
    },
    # tests/kernel/device/src/main.c's test_pre_kernel_detection registers
    # 4 SYS_INIT hooks (pre1_fn@PRE_KERNEL_1, pre2_fn@PRE_KERNEL_2,
    # post_fn@POST_KERNEL, app_fn@APPLICATION), each appending a record of
    # its own hardcoded "pre_kernel" flag plus the *actual* k_is_pre_kernel()
    # reading at call time. The test's own assertions don't care which
    # named function ran at which level — they just count how many
    # consecutive records (from the start) have .pre_kernel==true and
    # expect exactly 2. Swapping pre2_fn's and post_fn's SYS_INIT *levels*
    # (PRE_KERNEL_2 <-> POST_KERNEL, anchored on the unique ", 0);" call
    # tails rather than the bare level names — POST_KERNEL alone repeats
    # 8x in this file for unrelated DEVICE_DEFINE calls) moves post_fn to
    # run pre-kernel and pre2_fn to run post-kernel, while each function's
    # body keeps writing its OLD hardcoded pre_kernel flag. This breaks the
    # consecutive-count invariant: post_fn's record (still .pre_kernel=
    # false) now lands second in the array, truncating the "count records
    # while pre_kernel==true" loop at 1 instead of 2. A different
    # SYS_INIT-based variant of the same "reuse thread_priority_swap on a
    # non-thread ordering mechanism" trick as the constructor-priority
    # entry above. Mutated build deterministically fails
    # test_pre_kernel_detection ("bad pre-kernel count", 2/2 identical
    # runs); every other test in the device suite (22 pass, 2 skip) is
    # unaffected. Reverted build passes the full suite cleanly.
    {
        "id_suffix": "thread_priority_swap_sys_init_level",
        "category": "runtime_crash",
        "target_file": "tests/kernel/device/src/main.c",
        "operator": "thread_priority_swap:PRE_KERNEL_2, 0);:POST_KERNEL, 0);",
        "target_app": "tests/kernel/device",
        "board": "native_sim",
    },
    # Same test_app (tests/kernel/device), a different source file
    # (test_driver_init.c) and a different init-ordering mechanism:
    # DEVICE_DEFINE priority (not SYS_INIT level). my_driver_priority_1/2/3/4
    # each have their own init function that writes its OWN identifying
    # constant (PRIORITY_1..4) into init_priority_sequence[], and are
    # DEVICE_DEFINE'd with distinct POST_KERNEL priorities 1, 2, 3, and 20
    # respectively (20 chosen deliberately in the upstream code to catch
    # sorting bugs against literal "2"). test_device_init_priority expects
    # the sequence to read [1, 2, 3, 4] — i.e. actual init order sorted by
    # priority number. Swapped priority_1's and priority_3's numeric
    # priority arguments (anchored on "POST_KERNEL, 1," / "POST_KERNEL, 3,"
    # — each unique in the file since it's pinned to end-of-line before the
    # multi-line call wraps) so priority_3 now inits first (writing 3) and
    # priority_1 inits third (writing 1), while priority_2/4 stay put —
    # sequence becomes [3, 2, 1, 4]. Mutated build deterministically fails
    # test_device_init_priority ("init sequence is not correct", 2/2 runs);
    # every other test in the same 25-test suite (including
    # test_pre_kernel_detection above, unaffected since it's a different
    # file/mechanism) passes. Reverted build passes the full suite cleanly.
    {
        "id_suffix": "thread_priority_swap_device_init_priority",
        "category": "runtime_crash",
        "target_file": "tests/kernel/device/src/test_driver_init.c",
        "operator": "thread_priority_swap:POST_KERNEL, 1,:POST_KERNEL, 3,",
        "target_app": "tests/kernel/device",
        "board": "native_sim",
    },
    # A third, cleaner instance of the "ordering mechanism" vein, this time
    # outside tests/kernel entirely: tests/lib/devicetree/devices/src/main.c.
    # All 12 devices here share the SAME generic dev_init() function, which
    # just appends device_handle_get(dev) to a shared init_order[] array —
    # no per-device identifying constant needed, the array itself records
    # *which* device ran at each position. test_init_order asserts the
    # array matches DEVICE_DT_DEFINE priority order exactly, position by
    # position. Swapped TEST_I2C's and TEST_DEVA's POST_KERNEL priorities
    # (10 <-> 20, anchored on the unique full call tails "POST_KERNEL, 10,
    # NULL);" / "POST_KERNEL, 20, NULL);" since bare "10"/"20" would risk
    # matching other devices' priority values or unrelated numbers) so
    # TEST_DEVA now inits before TEST_I2C. Mutated build deterministically
    # fails test_init_order at the very first affected position
    # ("init_order[1] not equal to DEV_HDL(TEST_I2C)", 2/2 runs); the other
    # 5 tests in the suite (test_get_or_null, test_init_get, test_injected,
    # test_requires, test_supports) all pass. Reverted build passes the
    # full 6-test suite cleanly.
    {
        "id_suffix": "thread_priority_swap_devicetree_init_order",
        "category": "runtime_crash",
        "target_file": "tests/lib/devicetree/devices/src/main.c",
        "operator": "thread_priority_swap:POST_KERNEL, 10, NULL);:POST_KERNEL, 20, NULL);",
        "target_app": "tests/lib/devicetree/devices",
        "board": "native_sim",
    },
    # A fourth instance of the vein, back in test_driver_init.c (same file
    # as the DEVICE_DEFINE-priority target above) but targeting the sibling
    # test_device_init_level instead of test_device_init_priority.
    # my_driver_level_1/2/3 each write their own hardcoded
    # LEVEL_PRE_KERNEL_1/2/LEVEL_POST_KERNEL constant into
    # init_level_sequence[], DEVICE_DEFINE'd at PRE_KERNEL_1/PRE_KERNEL_2/
    # POST_KERNEL respectively; test_device_init_level expects [1,2,3].
    # Swapped level_1's and level_2's DEVICE_DEFINE *level* arguments
    # (anchored on "NULL, NULL, NULL, PRE_KERNEL_1," / "NULL, NULL, NULL,
    # PRE_KERNEL_2," — bare PRE_KERNEL_1/PRE_KERNEL_2 each appear 3x in
    # this file: once in a #define, once inside a LEVEL_PRE_KERNEL_N write,
    # once in the actual DEVICE_DEFINE call, so needed the longer
    # positional-argument prefix to disambiguate) so level_2 now inits
    # first (pre-kernel-1) and level_1 second (pre-kernel-2), giving
    # sequence [2, 1, 3]. Mutated build deterministically fails
    # test_device_init_level ("init sequence is not correct" at line 333,
    # 2/2 runs) while test_device_init_priority (the other mutation in this
    # same file, different DEVICE_DEFINE block) is unaffected — confirmed
    # the two catalog entries coexist independently. Reverted build passes
    # the full 25-test device suite cleanly.
    {
        "id_suffix": "thread_priority_swap_device_init_level",
        "category": "runtime_crash",
        "target_file": "tests/kernel/device/src/test_driver_init.c",
        "operator": "thread_priority_swap:NULL, NULL, NULL, PRE_KERNEL_1,:NULL, NULL, NULL, PRE_KERNEL_2,",
        "target_app": "tests/kernel/device",
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
