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
    # Session 46 part 18: kconfig/dts/c_syntax 類別過去只用過 2 個目標檔案
    # (subsys/fs/fcb/Kconfig、boards/native/native_sim/native_sim.dts、
    # samples/hello_world/src/main.c)，同一套「反轉唯一 depends on」手法
    # 這次換到全新的子系統 subsys/dfu/img_util 上驗證是否可重複套用。
    # `config MCUBOOT_IMG_MANAGER` 是 `choice` 底下唯一的選項，
    # `depends on FLASH_MAP` 反轉成 `depends on !FLASH_MAP` 後，
    # tests/subsys/dfu/img_util 的 prj.conf 雖然明確要求
    # CONFIG_MCUBOOT_IMG_MANAGER=y/CONFIG_FLASH_MAP=y，Kconfig 仍會因依賴
    # 條件無法滿足而讓該符號維持 n——連帶讓同一個 `if MCUBOOT_IMG_MANAGER`
    # 區塊內的 `config IMG_BLOCK_BUF_SIZE` 也一併停用，讓
    # include/zephyr/dfu/flash_img.h 裡對 CONFIG_IMG_BLOCK_BUF_SIZE 的引用
    # 變成未定義巨集——測試目標 tests/subsys/dfu/img_util/src/main.c
    # 一 include 這個標頭檔就編譯失敗 (`undeclared here`)，比 FCB 案例的
    # 連結期錯誤更早在編譯期就現形，但同樣是這個 Kconfig 依賴斷裂的直接
    # 後果。實測驗證：mutate 端在 west build 階段編譯失敗
    # (`ninja: build stopped`)；revert 端重新編譯後 img_util 套件 3/3
    # 測試全過 (`PROJECT EXECUTION SUCCESSFUL`)。
    # kconfig/dts/c_syntax previously only ever touched 2 target files
    # (subsys/fs/fcb/Kconfig, boards/native/native_sim/native_sim.dts,
    # samples/hello_world/src/main.c) — this re-applies the same "invert
    # the sole depends on" technique to a brand-new subsystem,
    # subsys/dfu/img_util. `config MCUBOOT_IMG_MANAGER` is the only option
    # under its `choice` block; flipping `depends on FLASH_MAP` to
    # `depends on !FLASH_MAP` makes Kconfig keep the symbol at n even
    # though tests/subsys/dfu/img_util's prj.conf explicitly requests
    # CONFIG_MCUBOOT_IMG_MANAGER=y/CONFIG_FLASH_MAP=y — which also disables
    # `config IMG_BLOCK_BUF_SIZE` (defined inside the same
    # `if MCUBOOT_IMG_MANAGER` block), leaving
    # include/zephyr/dfu/flash_img.h's reference to
    # CONFIG_IMG_BLOCK_BUF_SIZE undefined. The test target
    # tests/subsys/dfu/img_util/src/main.c fails to compile the instant it
    # includes that header (`undeclared here`) — an earlier, compile-time
    # manifestation of the same broken-dependency root cause FCB's
    # link-time error demonstrated. Empirically verified: mutate side fails
    # during `west build` (`ninja: build stopped`); revert side rebuilds
    # clean, img_util suite 3/3 (`PROJECT EXECUTION SUCCESSFUL`).
    {
        "id_suffix": "kconfig_img_manager_depends",
        "category": "kconfig",
        "target_file": "subsys/dfu/Kconfig",
        "operator": "kconfig_invert_depends:MCUBOOT_IMG_MANAGER",
        "target_app": "tests/subsys/dfu/img_util",
        "board": "native_sim",
    },
    # Session 46 part 19: fourth kconfig entry, same "invert the sole
    # depends on" shape as FCB/img_manager but on `subsys/modem/Kconfig`'s
    # `config MODEM_PPP` — verified the target's own prj.conf doesn't
    # independently force any of MODEM_PPP's `select`ed symbols (a trap
    # already caught once this round: `config MODEM_PIPE`'s `select EVENTS`
    # is a dead end because tests/subsys/modem/modem_pipe's prj.conf sets
    # CONFIG_EVENTS=y directly anyway, so removing the select changes
    # nothing). `depends on NET_L2_PPP` -> `depends on !NET_L2_PPP` keeps
    # MODEM_PPP at n regardless of tests/subsys/modem/modem_ppp's prj.conf
    # requesting CONFIG_MODEM_PPP=y, so subsys/modem/CMakeLists.txt's
    # `zephyr_library_sources_ifdef(CONFIG_MODEM_PPP modem_ppp.c)` drops
    # the file — and since MODEM_PPP's own `select MODEM_PIPE` never fires
    # either, the cascading link failure spans both modem_ppp.c's and the
    # shared mock backend's undefined references (modem_ppp_*,
    # modem_pipe_notify_*, crc16_ccitt). Empirically verified: mutate side
    # fails at link time; revert side rebuilds clean, modem_ppp suite
    # 12/12 (native_sim, no QEMU needed).
    {
        "id_suffix": "kconfig_modem_ppp_depends",
        "category": "kconfig",
        "target_file": "subsys/modem/Kconfig",
        "operator": "kconfig_invert_depends:MODEM_PPP",
        "target_app": "tests/subsys/modem/modem_ppp",
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
    # dts_corrupt_reg 這個 operator 原本從沒被用過——既有的兩個 dts 案例都
    # 動 boards/native/native_sim/native_sim.dts。這次換到
    # tests/drivers/gpio/gpio_mmio_latch 的 qemu_cortex_m3.overlay (同一個
    # 檔案先前在 runtime_crash 類別裡被 dts_reg_offbyone 動過 sram0 節點的
    # reg *數值*，這裡改動的是完全不同的節點 test_latch 的 reg *結構*，破壞
    # 形狀不同：數值位移 vs. cell 數量錯誤)。test_latch 節點的
    # `reg = <0x2000ff00 0x4>;` 是 2 個 cell (#address-cells=1、
    # #size-cells=1 各一)；dts_corrupt_reg 刪掉最後一個 cell 後變成
    # `reg = <0x2000ff00>;`，只剩 1 個 cell，觸發 devicetree 綁定驗證的
    # cell-count 檢查 (`length 4, which is not evenly divisible by 8`)，在
    # `west build` 的 CMake 設定階段 (dts.cmake) 就直接失敗，甚至比
    # ninja 編譯期還早。實測驗證：mutate 端在 CMake Configure 階段失敗；
    # revert 端重建後 gpio_mmio_latch 套件 5/5 全過
    # (`PROJECT EXECUTION SUCCESSFUL`)。
    # dts_corrupt_reg was never used before — the existing 2 dts cases both
    # touch boards/native/native_sim/native_sim.dts. This one moves to
    # tests/drivers/gpio/gpio_mmio_latch's qemu_cortex_m3.overlay (the same
    # file a runtime_crash-category dts_reg_offbyone case already touched
    # sram0's reg *value* on — this one corrupts a completely different
    # node, test_latch's reg *structure* instead: a value nudge vs. a
    # cell-count break, different failure shapes). test_latch's
    # `reg = <0x2000ff00 0x4>;` is 2 cells (#address-cells=1,
    # #size-cells=1); dts_corrupt_reg deletes the last cell, leaving
    # `reg = <0x2000ff00>;` — only 1 cell, which trips the devicetree
    # binding's cell-count check (`length 4, which is not evenly divisible
    # by 8`) right at `west build`'s CMake configure stage (dts.cmake),
    # even earlier than ninja compilation. Empirically verified: mutate
    # side fails during CMake configure; revert side rebuilds clean,
    # gpio_mmio_latch suite 5/5 (`PROJECT EXECUTION SUCCESSFUL`).
    {
        "id_suffix": "dts_gpio_latch_reg_cellcount",
        "category": "dts",
        "target_file": "tests/drivers/gpio/gpio_mmio_latch/boards/qemu_cortex_m3.overlay",
        "operator": "dts_corrupt_reg",
        "target_app": "tests/drivers/gpio/gpio_mmio_latch",
        "board": "qemu_cortex_m3",
    },
    # Session 46 part 19: fourth dts entry, `dts_break_phandle` (already
    # used once on native_sim.dts) reused on a brand-new file — and, unlike
    # every prior dts case, entirely on native_sim (no QEMU cold-build
    # cost). tests/subsys/input/longpress/boards/native_sim.overlay's
    # `longpress` node has `input = <&fake_input_device>;`, referencing a
    # sibling node defined earlier in the same file; breaking it to
    # `&fake_input_device_broken_ref` is an undefined-node-label reference
    # the devicetree compiler itself rejects at CMake-configure time
    # (`devicetree error: /longpress: undefined node label
    # 'fake_input_device_broken_ref'`) — before any C compilation starts,
    # same failure stage as native_sim.dts's own dts_break_phandle case.
    # Empirically verified: mutate side fails at CMake configure; revert
    # side rebuilds clean, longpress suite 1/1.
    {
        "id_suffix": "dts_longpress_phandle",
        "category": "dts",
        "target_file": "tests/subsys/input/longpress/boards/native_sim.overlay",
        "operator": "dts_break_phandle",
        "target_app": "tests/subsys/input/longpress",
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
    # c_typo_macro 這個 operator 原本從沒被用過——前兩個 c_syntax 案例都是
    # samples/hello_world/src/main.c 上的 remove_semicolon/remove_closing_brace，
    # 這次換一個完全沒被 c_syntax 類別碰過的檔案跟 operator。
    # tests/subsys/dfu/img_util/src/main.c (剛好也是本輪新加的
    # kconfig_img_manager_depends 案例所在的測試套件，但目標檔案不同——這裡
    # 動的是套件自己的測試原始碼，不是它依賴的 Kconfig) 有一個巨集定義
    # `PARTITION_IS_RUNNING_APP_PARTITION` 直接呼叫了 `DT_NODELABEL(label)`。
    # 把它拼錯成 `DT_NODELABE` 後，前處理器不再認得這個巨集名稱，讓
    # `DT_CAT(DT_DEP_ORD(DT_NODELABE(label)), ...)` 這類巢狀巨集串接把
    # `)` 跟 `_ORD` 直接黏在一起，變成一個不合法的前處理 token
    # (`pasting ")" and "_ORD" does not give a valid preprocessing token`)。
    # 這是跟既有兩個 c_syntax 案例 (漏分號、漏右括號) 完全不同形狀的語法
    # 破壞——巨集拼接失敗而非單純標點遺漏。實測驗證：mutate 端在
    # `west build` 編譯期失敗；revert 端重建後 img_util 套件 3/3 全過。
    # c_typo_macro was never used before — the first two c_syntax cases
    # were both remove_semicolon/remove_closing_brace on
    # samples/hello_world/src/main.c; this uses a file and operator the
    # category has never touched. tests/subsys/dfu/img_util/src/main.c
    # (coincidentally the same test suite as this round's new
    # kconfig_img_manager_depends case, but a different target file — this
    # one mutates the suite's own test source, not the Kconfig it depends
    # on) has a macro definition, `PARTITION_IS_RUNNING_APP_PARTITION`,
    # that directly calls `DT_NODELABEL(label)`. Misspelling it to
    # `DT_NODELABE` makes the preprocessor no longer recognize the macro
    # name, so the nested macro-pasting inside
    # `DT_CAT(DT_DEP_ORD(DT_NODELABE(label)), ...)` glues `)` directly to
    # `_ORD`, producing an invalid preprocessing token (`pasting ")" and
    # "_ORD" does not give a valid preprocessing token`) — a genuinely
    # different syntax-break shape than the existing 2 c_syntax cases
    # (missing semicolon, missing closing brace): a macro-pasting failure,
    # not a simple missing-punctuation error. Empirically verified: mutate
    # side fails during `west build` compile; revert side rebuilds clean,
    # img_util suite 3/3.
    {
        "id_suffix": "c_img_util_dt_nodelabel_typo",
        "category": "c_syntax",
        "target_file": "tests/subsys/dfu/img_util/src/main.c",
        "operator": "c_typo_macro",
        "target_app": "tests/subsys/dfu/img_util",
        "board": "native_sim",
    },
    # Session 46 part 19: fourth c_syntax entry, `c_remove_semicolon`
    # (already used once on samples/hello_world/src/main.c) reused on
    # tests/subsys/mgmt/mcumgr/smp_client/src/main.c — a proven,
    # already-c_api_substitute-mined native_sim target, new to the
    # c_syntax category. Removes the semicolon off the first `);`-ending
    # call in the file (inside smp_client_test_buf_alloc's
    # smp_client_buf_allocation(...) call), producing a plain
    # "expected ';' before 'if'" compile error at the very next line — the
    # same missing-punctuation shape as the existing hello_world case, but
    # on a different file/subsystem entirely. Empirically verified: mutate
    # side fails during compile; revert side rebuilds clean, smp_client
    # suite 3/3.
    {
        "id_suffix": "c_smp_client_semicolon",
        "category": "c_syntax",
        "target_file": "tests/subsys/mgmt/mcumgr/smp_client/src/main.c",
        "operator": "c_remove_semicolon",
        "target_app": "tests/subsys/mgmt/mcumgr/smp_client",
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
    # Same file/test as the entry above (test_driver_init.c's
    # test_device_init_level), but the specific PRE_KERNEL_1 <-> POST_KERNEL
    # cross-boundary combination requested explicitly (as opposed to the
    # within-pre-kernel PRE_KERNEL_1 <-> PRE_KERNEL_2 swap above): swapped
    # my_driver_level_1's (PRE_KERNEL_1) and my_driver_level_3's
    # (POST_KERNEL) DEVICE_DEFINE level arguments. Anchoring note: the first
    # occurrence of the bare tail "NULL, NULL, NULL, POST_KERNEL," in this
    # file happens to be level_3's own line (the later my_driver_priority_N
    # / fakedomain_N occurrences all have a numeric priority argument
    # immediately after POST_KERNEL, so their text doesn't match this exact
    # substring) — confirmed via grep before relying on it, no @scope
    # needed. Sequence becomes [3, 2, 1] (level_3 now inits first writing
    # 3, level_2 unchanged writing 2, level_1 now inits last writing 1).
    # Mutated build deterministically fails test_device_init_level (2/2
    # runs); coexists independently with the other two mutations already
    # registered against this same file. Reverted build passes cleanly.
    {
        "id_suffix": "thread_priority_swap_device_init_level_cross_boundary",
        "category": "runtime_crash",
        "target_file": "tests/kernel/device/src/test_driver_init.c",
        "operator": "thread_priority_swap:NULL, NULL, NULL, PRE_KERNEL_1,:NULL, NULL, NULL, POST_KERNEL,",
        "target_app": "tests/kernel/device",
        "board": "native_sim",
    },
    # A much higher-stakes instance of the same vein:
    # tests/subsys/pm/power_mgmt/src/main.c's test_device_order. Device A
    # (PRE_KERNEL_1), device B (PRE_KERNEL_2), and device C (POST_KERNEL)
    # are 3 PM-aware devices whose init level determines the actual
    # suspend/resume traversal order under CONFIG_PM_DEVICE_SYSTEM_MANAGED
    # (resume runs in forward init order A->B->C, suspend in reverse
    # C->B->A) — device_b_pm_action's own PM callback checks device A's and
    # C's *live* PM state at the moment B suspends/resumes, expecting
    # A-active/C-suspended in both directions per the correct order.
    # Swapped device B's and device C's DEVICE_DT_DEFINE levels (PRE_KERNEL_2
    # <-> POST_KERNEL, anchored on the call tail plus
    # CONFIG_KERNEL_INIT_PRIORITY_DEVICE — device A's own PRE_KERNEL_1 tail
    # is *not* unique on its own, since an unrelated no-PM device_e uses an
    # identical line earlier in the file, so device A was deliberately left
    # untouched to avoid that anchor ambiguity rather than fighting it).
    # New order (A, C, B) means when B now suspends/resumes third instead
    # of second, C's state is wrong in both directions. Unlike every other
    # ordering-mechanism target so far, this doesn't surface as a clean
    # zassert-then-continue failure: the assertion fires from *inside* a PM
    # action callback running in the idle thread's context, and Zephyr
    # escalates that to a genuine kernel panic ("ZEPHYR FATAL ERROR 4:
    # Kernel panic on CPU 0"), not just a ztest-level failure — a more
    # severe, crash-category-appropriate manifestation than the pure
    # device/init-order examples above. Verified deterministic (2/2 runs,
    # identical panic); reverted build passes the full 8-test suite cleanly.
    {
        "id_suffix": "thread_priority_swap_pm_device_order",
        "category": "runtime_crash",
        "target_file": "tests/subsys/pm/power_mgmt/src/main.c",
        "operator": "thread_priority_swap:PRE_KERNEL_2, CONFIG_KERNEL_INIT_PRIORITY_DEVICE,:POST_KERNEL, CONFIG_KERNEL_INIT_PRIORITY_DEVICE,",
        "target_app": "tests/subsys/pm/power_mgmt",
        "board": "native_sim",
    },
    # First target in tests/subsys/shell — a fresh subsystem for the
    # ordering-mechanism vein, found via a systematic scan (grep every file
    # using DEVICE_DEFINE/DEVICE_DT_DEFINE for 3+ distinct POST_KERNEL
    # priority literals, a reusable search technique now rather than ad hoc
    # directory browsing). tests/subsys/shell/shell_device_filter/src/main.c
    # defines device_0/1/2 at POST_KERNEL priorities 0/1/2, and
    # test_unfiltered asserts shell_device_filter(i, NULL) /
    # shell_device_lookup(i, NULL) returns device i — i.e. it indexes
    # directly into the static device list, which is ordered by init
    # priority. This is a different *consumption* of the ordering
    # mechanism than every previous entry (position-indexed lookup, not an
    # explicit recorded sequence array), but the same underlying fault:
    # swapping device_0's and device_1's priorities (0<->1, anchored on the
    # unique full call tails "POST_KERNEL, 0, NULL);" / "POST_KERNEL, 1,
    # NULL);") makes shell_device_filter(0, NULL) return device_1 instead
    # of device_0. Mutated build deterministically fails test_unfiltered
    # (2/2 runs); test_filter and test_prefix (same file, unaffected
    # indices) both pass. Reverted build passes the full 3-test suite
    # cleanly.
    {
        "id_suffix": "thread_priority_swap_shell_device_filter",
        "category": "runtime_crash",
        "target_file": "tests/subsys/shell/shell_device_filter/src/main.c",
        "operator": "thread_priority_swap:POST_KERNEL, 0, NULL);:POST_KERNEL, 1, NULL);",
        "target_app": "tests/subsys/shell/shell_device_filter",
        "board": "native_sim",
    },
    # A new subsystem (tests/misc) and a genuinely different infrastructure
    # than every prior ordering-mechanism entry: linker ITERABLE_SECTION
    # sorting rather than kernel/device init priority.
    # tests/misc/iterable_sections/src/main.c deliberately declares
    # ram1..ram4 out of source order (ram3, ram2, ram4, ram1) specifically
    # to prove STRUCT_SECTION_FOREACH iterates in linker-sorted (by name)
    # order, not declaration order — test_ram computes a running checksum
    # while iterating and asserts it equals RAM_EXPECT (0x01020304, i.e.
    # ram1=0x01 read first, ram2=0x02 second, etc). Since declaration-order
    # swaps are a proven no-op here (the file already declares them
    # scrambled on purpose), swapped the *values* instead: ram1's and
    # ram2's assigned {0x01}/{0x02} (bare-brace anchors, each unique as the
    # *first* occurrence in the file — the same literal values recur later
    # for ram6/ram9 and ramn_1/ramn_3, but those all sit further down, so
    # no @scope or #N was needed). Iteration order is untouched (still
    # ram1, ram2, ram3, ram4 by name), but ram1 now reads 0x02 and ram2
    # reads 0x01, breaking the checksum. Mutated build deterministically
    # fails test_ram ("Check value incorrect (got: 0x02010304)", 2/2 runs);
    # test_rom (a separate, unrelated iterable section in the same binary)
    # passes. Reverted build passes both tests cleanly. This entry is a
    # slightly different flavor than the others — a value-corruption bug
    # riding on the same 2-literal-swap mechanism, rather than an actual
    # reordering — but still lands as a genuine, deterministic runtime
    # assertion failure via the identical thread_priority_swap operator.
    {
        "id_suffix": "thread_priority_swap_iterable_sections",
        "category": "runtime_crash",
        "target_file": "tests/misc/iterable_sections/src/main.c",
        "operator": "thread_priority_swap:{0x01}:{0x02}",
        "target_app": "tests/misc/iterable_sections",
        "board": "native_sim",
    },
    # Same file/ZTEST as the entry above (test_ram covers several
    # STRUCT_SECTION_FOREACH checks back to back in one function body), but
    # targets a different sub-case: STRUCT_SECTION_ITERABLE_NAMED sorts by
    # a *custom* name argument (A/B/C/D) rather than the C variable name,
    # so ram6(A)/ram9(B)/ram7(C)/ram8(D) iterate in that order regardless
    # of declaration order or variable name. Swapped ram7's/ram8's assigned
    # values ({0x03}/{0x04}) — needed the "#2" occurrence-index syntax
    # (added last session) on both sides, since {0x03}/{0x04} each already
    # appear once earlier for ram3/ram4 (part of the *other*,
    # already-registered mutation's untouched territory) before recurring
    # here. Because ram1-4's own checksum check (line 69) runs first in
    # the function and is untouched by this mutation, it passes; only the
    # *later* test_ram_named checksum check (line 92) fails — a
    # diagnostically distinct assertion line/value (0x1020403) from the
    # ram1/ram2 entry's (0x02010304), confirmed deterministic (2/2 runs)
    # and independent (mutating one doesn't affect the other's target
    # values). Reverted build passes both tests cleanly.
    {
        "id_suffix": "thread_priority_swap_iterable_sections_named",
        "category": "runtime_crash",
        "target_file": "tests/misc/iterable_sections/src/main.c",
        "operator": "thread_priority_swap:{0x03}#2:{0x04}#2",
        "target_app": "tests/misc/iterable_sections",
        "board": "native_sim",
    },
    # A third entry in the same file, but this time targeting the
    # *sibling* ZTEST (`ZTEST(iterable_sections, test_rom)`, a separate
    # function from `test_ram` — genuinely distinguishable at the
    # test-name level, not just by assertion line/value like the two
    # entries above). rom1/rom2 are the ROM-section analogue of ram1/ram2:
    # STRUCT_SECTION_ITERABLE sorts by C variable name, so swapping just
    # the assigned values ({0x10}/{0x20}, both unique as first occurrence
    # — no #N needed, unlike the RAM-section named variant) breaks the
    # running checksum while leaving iteration order (rom1, rom2, rom3,
    # rom4) untouched. Mutated build fails test_rom specifically ("Check
    # value incorrect (got: 0x20103040)", 2/2 runs) while test_ram (a
    # separate ZTEST, unaffected) passes. Reverted build passes both
    # cleanly.
    {
        "id_suffix": "thread_priority_swap_iterable_sections_rom",
        "category": "runtime_crash",
        "target_file": "tests/misc/iterable_sections/src/main.c",
        "operator": "thread_priority_swap:{0x10}:{0x20}",
        "target_app": "tests/misc/iterable_sections",
        "board": "native_sim",
    },
    # First target in tests/subsys/zbus, and a genuinely different
    # mechanism than every prior entry: zbus's "HLP" (High Locality of
    # Preemption) publisher priority-boost feature, not init/iteration
    # order. tests/subsys/zbus/hlp_priority_boost/src/main.c's
    # test_priority_elevation creates a low-priority publisher thread
    # (K_PRIO_PREEMPT(8)) plus two observer threads — sub1 at
    # K_PRIO_PREEMPT(3), msub1 at K_PRIO_PREEMPT(2) — and asserts that
    # zbus_chan_pub() boosts the publisher's priority to
    # (min priority among currently-enabled observers) - 1, checked across
    # several enable/disable/mask combinations. Swapping sub1's and
    # msub1's priorities (3<->2, both unique in the file, no @scope/#N
    # needed) is a no-op for the "both observers enabled" checks (the
    # *set* of values {2,3} and thus their minimum is unchanged by a pure
    # swap) but breaks the "only sub1 enabled" checks, which depend on
    # sub1's *own* priority specifically: expected boosted priority 2
    # (from sub1's original K_PRIO_PREEMPT(3) - 1) becomes 1 (from sub1's
    # new K_PRIO_PREEMPT(2) - 1) instead. Mutated build deterministically
    # fails test_priority_elevation at the second checkpoint ("The
    # priority must be 2, but it is 1", 2/2 runs) — the sole test in this
    # suite. Reverted build passes cleanly.
    {
        "id_suffix": "thread_priority_swap_zbus_hlp",
        "category": "runtime_crash",
        "target_file": "tests/subsys/zbus/hlp_priority_boost/src/main.c",
        "operator": "thread_priority_swap:K_PRIO_PREEMPT(3):K_PRIO_PREEMPT(2)",
        "target_app": "tests/subsys/zbus/hlp_priority_boost",
        "board": "native_sim",
    },
    # runtime_off_by_one's second target, and the operator's first with a
    # literal-text anchor (extended this round, mirroring
    # thread_priority_swap/c_api_substitute's existing anchor style) since
    # the naive "first `<` in the file" scan would have hit an earlier,
    # unrelated comparison instead. lib/mem_blocks/mem_blocks.c's
    # free_blocks() rejects any pointer below the buffer's start:
    # `if (blk < mem_block->buffer) { return -EFAULT; }` — a lower-bound
    # guard, not an upper one, so this is a new *flavor* of off-by-one
    # (false rejection of the first valid boundary element) rather than a
    # repeat of fcb's "run one extra loop iteration" idiom.
    # target_app is deliberately tests/lib/mem_blocks_stats, NOT the
    # (also-affected) tests/lib/mem_blocks — that suite's
    # test_*_invalid_params_panic_* cases deliberately trigger and
    # ztest-catch a real Kernel panic/ASSERTION FAIL as their own testing
    # technique, which happens on EVERY build (mutated or not) and made
    # tools/qemu_oracle.py's wait_for_completion=True crash-detection
    # (which finalizes status="crash" on the *first* crash-pattern match,
    # regardless of whether ztest recovers and keeps running) misclassify
    # the clean reverted build as a crash too — confirmed via the real
    # verify_cases.py pipeline before switching targets: both mutate and
    # revert sides reported ASSERTION FAIL there, an oracle limitation
    # rather than a mutation problem. tests/lib/mem_blocks_stats/src/main.c
    # has no such self-test pattern (grepped for "panic"/"fault_valid":
    # zero hits) and its test_mem_blocks_runtime_stats allocates 3 blocks
    # then frees the first two via
    # `sys_mem_blocks_free(&mem_block_01, 2, &blocks[0])` — blocks[0] is
    # necessarily mem_block_01.buffer itself (first-ever allocation from a
    # fresh bitmap), so tightening `<` to `<=` makes that exact call
    # spuriously return -EFAULT. Mutated build: test_mem_blocks_runtime_stats
    # fails cleanly ("status not equal to 0", "Routine failed with status
    # -14", 2/2 runs identical) via ztest's own zassert message (not a
    # panic), while the unrelated test_mem_blocks_stats_invalid still
    # passes. Reverted build: byte-identical file, both tests pass, no
    # crash-pattern text anywhere in the output. Verified through the real
    # verify_cases.py pipeline (not just manual testing).
    {
        "id_suffix": "runtime_mem_blocks_offbyone",
        "category": "runtime_crash",
        "target_file": "lib/mem_blocks/mem_blocks.c",
        "operator": "runtime_off_by_one:blk < mem_block->buffer:blk <= mem_block->buffer",
        "target_app": "tests/lib/mem_blocks_stats",
        "board": "native_sim",
    },
    # runtime_remove_null_check's second target, and its first with a
    # literal-text anchor (extended this round, same style as
    # runtime_off_by_one gained last round) since "if (value != NULL) {"
    # appears twice verbatim in lib/hash/hash_map_sc.c (sys_hashmap_sc_remove
    # and sys_hashmap_sc_get) — the naive first-match scan would have hit
    # _remove, not the intended _get; "#2" picks the second occurrence.
    # This is the classic "optional output pointer" idiom: sys_hashmap_get's
    # value parameter is documented as optional (pass NULL to just check
    # existence), so sys_hashmap_sc_get() guards the write with
    # `if (value != NULL) { *value = entry->value; }`. Forcing that guard
    # to always-true makes it unconditionally write through value even
    # when the caller legitimately passed NULL for it.
    # tests/lib/hash_map/src/get.c's test_get_true calls
    # `sys_hashmap_get(&map, 0, NULL)` as its very first assertion (right
    # after a successful insert) — SYS_HASH_MAP_CHOICE_SC is this test's
    # (and the whole project's) default backend per lib/hash/Kconfig.hash_map,
    # so this call unconditionally reaches the mutated function on every
    # run, matching the "prefer unconditionally-exercised checks" lesson
    # from last round's min_heap dead end. Mutated build: a genuine NULL
    # pointer write — `Segmentation fault`, exit code 139, 2/2 runs
    # identical — at exactly test_get_true, the earlier test_get_false and
    # every other of the suite's 15 tests unaffected. Reverted build:
    # byte-identical file, all 15 tests pass, no crash-pattern text
    # anywhere. Verified through the real verify_cases.py pipeline.
    {
        "id_suffix": "runtime_hash_map_nullcheck",
        "category": "runtime_crash",
        "target_file": "lib/hash/hash_map_sc.c",
        "operator": "runtime_remove_null_check:if (value != NULL) {#2:if (1) {",
        "target_app": "tests/lib/hash_map",
        "board": "native_sim",
    },
    # runtime_remove_null_check's third target, same "optional output
    # pointer" idiom as the hash_map entry above. lib/libc/common/source/
    # thrd/thrd.c's thrd_join(thr, res) treats `res` as optional (C11
    # <threads.h> semantics: pass NULL to join without retrieving the
    # thread's return value): `if (res != NULL) { *res = ...; }`. Forcing
    # the guard always-true makes it unconditionally write through `res`.
    # tests/lib/c_lib/thrd/src/thrd.c's test_thrd_create_join calls
    # `thrd_join(thr, NULL)` as literally its first join call — several
    # other tests in the same file do too (lines 138/155/161/168), all
    # unconditionally exercised. Screened the whole tests/lib/c_lib/thrd/
    # suite for the deliberate-panic self-test pattern first (zero hits
    # across all 5 files) and confirmed a bare `west build -b native_sim`
    # works without needing any of tests.yaml's libc-variant extra_configs
    # scenarios (the board's default libc is enough — this app has no
    # SYS_HASH_MAP_CHOICE-style default-vs-scenario split to worry about).
    # Note: this operator's "NULL pointer write crashes reliably" premise
    # does NOT hold on every board — a same-shaped attempt on
    # subsys/debug/symtab/symtab.c (its only test, tests/subsys/debug/
    # symtab, is qemu_cortex_m3/riscv/xtensa-only, no native_sim in
    # platform_allow) mutated and built cleanly but produced zero observable
    # effect: writing through a NULL `offset` pointer at address 0 on
    # qemu_cortex_m3/lm3s6965 apparently lands in a region QEMU doesn't
    # fault on (unlike native_sim, a real POSIX process where page 0 is
    # always unmapped and any NULL deref reliably SIGSEGVs) — reverted
    # without registering. Mutated build here: genuine `Segmentation
    # fault`, exit code 139, at exactly test_thrd_create_join (2/2 runs
    # identical); the earlier libc_cnd/libc_mtx/libc_once suites (which
    # don't call thrd_join with NULL) unaffected. Reverted build:
    # byte-identical file, all 22 tests across the app's 5 suites pass.
    # Verified through the real verify_cases.py pipeline.
    {
        "id_suffix": "runtime_thrd_join_nullcheck",
        "category": "runtime_crash",
        "target_file": "lib/libc/common/source/thrd/thrd.c",
        "operator": "runtime_remove_null_check:if (res != NULL) {:if (1) {",
        "target_app": "tests/lib/c_lib/thrd",
        "board": "native_sim",
    },
    # --- Board diversity round: same proven mutation, a different board ---
    # 35/40 catalog entries before this round were native_sim, only 2 were
    # qemu_cortex_m3 (both dts_reg_offbyone, which structurally needs a real
    # MMIO memory map). Rather than hunting a brand-new target, re-verified
    # an *already-proven* thread_priority_swap case
    # (thread_priority_swap_semaphore above — same target_file, same
    # operator string) on qemu_riscv32 instead of native_sim, on the
    # reasoning that Zephyr's scheduler is portable core kernel code, not a
    # host-simulator shim, so a priority-inversion bug shouldn't be
    # native_sim-specific (confirmed true; contrast with this same round's
    # earlier finding that runtime_remove_null_check's NULL-deref crash
    # mechanism is NOT board-portable — different operators, different
    # portability properties, verify per-operator rather than assuming).
    # First tried tests/kernel/common/src/constructor.c (session 15's GCC
    # constructor-priority target) on qemu_riscv32 — built clean but the
    # entire "constructor" ZTEST_SUITE was silently absent from the run:
    # its CMakeLists.txt only compiles src/constructor.c when
    # CONFIG_STATIC_INIT_GNU=y, which Kconfig defaults to y only for
    # `CPP || NATIVE_LIBRARY || COVERAGE` — NATIVE_LIBRARY is native_sim's
    # own Kconfig symbol, so this specific target is inherently
    # native_sim/C++/coverage-only, not a qemu_riscv32 gap to work around.
    # Pivoted to tests/kernel/semaphore/semaphore instead (no such config
    # gating, pure portable scheduler code) and it worked identically to
    # native_sim: qemu_riscv32 build fails exactly test_sem_take_multiple
    # (the other 20 tests in the 21-test semaphore suite pass), reverted
    # build (byte-identical file) passes all 21. Confirmed qemu_riscv32
    # idles forever after "PROJECT EXECUTION SUCCESSFUL" just like
    # qemu_cortex_m3 (not self-terminating) — same completion-detection
    # logic in qemu_oracle.py already handles this correctly.
    {
        "id_suffix": "thread_priority_swap_semaphore_riscv32",
        "category": "runtime_crash",
        "target_file": "tests/kernel/semaphore/semaphore/src/main.c",
        "operator": "thread_priority_swap:K_PRIO_PREEMPT(3):K_PRIO_PREEMPT(1)",
        "target_app": "tests/kernel/semaphore/semaphore",
        "board": "qemu_riscv32",
    },
    # c_api_substitute's first board-diversity entry (x86 architecture,
    # not yet represented — riscv32/arm were already covered by
    # thread_priority_swap). Same target/operator as the very first
    # c_api_substitute case (api_substitute_sleep_yield, session 5's
    # test_sleep_cooperative on native_sim), re-verified on qemu_x86
    # instead. No config-gating risk here (unlike the constructor.c
    # lesson from the riscv32 round) since this test_app already has
    # multiple native_sim catalog entries against it, all pure portable
    # kernel scheduler code. Confirmed a bare `west build -b qemu_x86`
    # baseline passes 100% first. Mutated build reproduces the exact same
    # assertion as native_sim (`tdata[i].executed == 1 is false` at
    # test_sleep_cooperative) but with a *more* severe cascade than
    # native_sim showed: the starvation corruption propagates further and
    # a later test (test_slice_scheduling) hits a genuine unhandled CPU
    # page-fault exception that aborts the whole binary outright (west's
    # own build-time run step reports non-zero exit) rather than reaching
    # a clean end-of-suite summary — still a fully valid, deterministic
    # crash reproduction of the injected bug (this test app's own
    # deliberate-fault-testing cases, e.g. test_k_wakeup_init_null,
    # use a "ZEPHYR FATAL ERROR N: Kernel oops"/"Caught system error"
    # message shape that does NOT match any of qemu_oracle.py's
    # crash_patterns, unlike session 23's mem_blocks lesson — so this
    # suite's pre-existing deliberate panics don't risk a revert-side
    # false positive here). Reverted build: byte-identical file, full
    # PROJECT EXECUTION SUCCESSFUL, all tests including
    # test_sleep_cooperative pass cleanly.
    {
        "id_suffix": "api_substitute_sleep_yield_qemu_x86",
        "category": "runtime_crash",
        "target_file": "tests/kernel/sched/schedule_api/src/test_sched_timeslice_and_lock.c",
        "operator": "c_api_substitute:test_sleep_cooperative:k_sleep(K_MSEC(100));:k_yield();",
        "target_app": "tests/kernel/sched/schedule_api",
        "board": "qemu_x86",
    },
    # thread_priority_swap's fourth board-diversity entry: qemu_cortex_a53
    # (aarch64) — the most architecturally distinct board tried so far
    # (64-bit ARM, vs. riscv32's 32-bit RISC-V and qemu_x86's 32-bit x86).
    # Same target/operator as thread_priority_swap_semaphore (the original
    # native_sim case) and thread_priority_swap_semaphore_riscv32 above —
    # tests/kernel/semaphore/semaphore has no config-gating risk (already
    # proven portable to riscv32). Baseline confirmed clean on
    # qemu_cortex_a53 first (21/21 pass). Mutated build: clean, isolated
    # failure — exactly test_sem_take_multiple fails, all other 20 tests
    # (including the semaphore_null_case suite's own deliberate
    # "ZEPHYR FATAL ERROR 3: Kernel oops"-triggering tests, which — per
    # session 27's lesson — don't match qemu_oracle.py's crash_patterns
    # anyway) pass. Reverted build: byte-identical file, all 21 pass,
    # PROJECT EXECUTION SUCCESSFUL.
    {
        "id_suffix": "thread_priority_swap_semaphore_cortex_a53",
        "category": "runtime_crash",
        "target_file": "tests/kernel/semaphore/semaphore/src/main.c",
        "operator": "thread_priority_swap:K_PRIO_PREEMPT(3):K_PRIO_PREEMPT(1)",
        "target_app": "tests/kernel/semaphore/semaphore",
        "board": "qemu_cortex_a53",
    },
    # thread_priority_swap's fifth board-diversity entry: qemu_xtensa
    # (Tensilica dc233c, the last architecturally-distinct board from the
    # original candidate list). Board note: this board needs the explicit
    # qualifier "qemu_xtensa/dc233c" — a bare "qemu_xtensa" is rejected by
    # west/CMake ("Board qualifiers `` for board `qemu_xtensa` not found").
    # First attempted api_substitute_sleep_yield (the c_api_substitute
    # target already proven on native_sim/qemu_x86) here instead, for
    # operator symmetry — its baseline built and passed cleanly, and the
    # mutation correctly reproduced the same test_sleep_cooperative/
    # test_sleep_wakeup_preemptible assertion failures as every other
    # board. But the *next* test in that suite, test_slice_scheduling
    # (normally ~11s), never completed — still running at 8+ minutes,
    # 99% CPU, no crash/fault/exit — a genuine hang, not a slow build,
    # killed directly via docker exec pkill rather than waiting further
    # (per the standing "don't fight a stuck container, kill it" rule).
    # This matters for the *automated* pipeline specifically: a hang here
    # would exceed FaultInjector's own timeout and finalize as
    # status="timeout", which does NOT satisfy runtime_crash's required
    # status="crash" — even though the actual bug evidence (the two
    # assertion failures) was already captured before the hang, the
    # two-sided gate would still reject it. Not registered; reverted and
    # abandoned in favor of a target proven not to cascade this far.
    # Pivoted to thread_priority_swap_semaphore (already proven on
    # native_sim/qemu_riscv32/qemu_cortex_a53) instead — a single
    # isolated-failure test with no lengthy timing-loop tests after it in
    # suite order, so no similar hang surface. Baseline clean (21 run,
    # semaphore_null_case suite entirely SKIPped on this board — 0 tests,
    # not a failure, likely a userspace/config gap specific to this SoC
    # target). Mutated build: clean, isolated failure — exactly
    # test_sem_take_multiple fails, no hang, completes normally.
    # Reverted build: byte-identical file, all 21 non-skipped tests pass.
    {
        "id_suffix": "thread_priority_swap_semaphore_xtensa",
        "category": "runtime_crash",
        "target_file": "tests/kernel/semaphore/semaphore/src/main.c",
        "operator": "thread_priority_swap:K_PRIO_PREEMPT(3):K_PRIO_PREEMPT(1)",
        "target_app": "tests/kernel/semaphore/semaphore",
        "board": "qemu_xtensa/dc233c",
    },
    # c_api_substitute's first target outside tests/kernel entirely (a new
    # subsystem — tests/subsys/modem — since all 9 prior entries lived in
    # tests/kernel/{mutex,sched,events,early_sleep,context}). Found via a
    # targeted search: grep for files combining k_sleep with an explicit
    # priority constant across tests/subsys and tests/drivers (mirroring
    # the search technique that found thread_priority_swap's tests/subsys
    # hits), most candidates were hardware-only (nRF/TEE/UART boards with
    # no QEMU/native_sim platform_allow) or non-ZTEST harness types (e.g.
    # tests/subsys/debug/thread_analyzer uses `harness: console` with
    # regex-matched printk output, not zassert — a new pipeline-mismatch
    # flavor alongside session 19's "type: unit"/bare-main() ones, noted
    # for future screening but not otherwise investigated).
    # tests/subsys/modem/modem_ubx/src/main.c's test_thread_yield() helper
    # is a gift: its own comment says outright *why* it uses k_sleep
    # instead of k_yield — "Used instead of k_yield() since internals of
    # modem pipe may rely on multiple thread interactions which may not be
    # served by simply yielding." A documented semantic justification for
    # the exact k_sleep/k_yield distinction this operator tests, not
    # inferred from test structure. The helper is called from several of
    # the file's 13 ZTEST cases wherever the test needs the modem pipe's
    # internal processing (workqueue-driven) to actually advance.
    # Substituting a single k_yield() for the 1ms sleep doesn't give the
    # workqueue a chance to fire, so every test relying on it to make the
    # pipe progress fails its own "Script should be done" style assertion.
    # Broader blast radius than any prior c_api_substitute entry (7 of 13
    # tests fail, 2/2 runs identical) but every failure is a clean zassert
    # message, no crash/panic, no hang — a faithful, deterministic
    # reproduction of exactly the failure mode the source comment already
    # predicts, not cascading corruption. Reverted build: byte-identical
    # file, all 13 pass.
    {
        "id_suffix": "api_substitute_modem_ubx_thread_yield",
        "category": "runtime_crash",
        "target_file": "tests/subsys/modem/modem_ubx/src/main.c",
        "operator": "c_api_substitute:test_thread_yield:k_sleep(K_MSEC(1));:k_yield();",
        "target_app": "tests/subsys/modem/modem_ubx",
        "board": "native_sim",
    },
    # runtime_remove_null_check's fourth target, same "optional output
    # pointer" idiom as the hash_map/thrd_join entries — but a genuine
    # POSIX standard API this time rather than a Zephyr-internal one.
    # subsys/portability/posix/options/clock.c's clock_getres(clock_id,
    # res) treats res as optional per POSIX (pass NULL to just validate
    # clock_id): `if (res != NULL) { *res = ...; }`.
    # tests/subsys/portability/posix/timers/src/clock.c's test_clock_getres
    # is a data-driven table test that explicitly includes
    # {CLOCK_REALTIME, NULL, 0} among its cases — i.e. it already tests the
    # NULL-res path on purpose, unconditionally exercised, no #N/scope
    # needed (only one `if (res != NULL) {` in the file). Two other
    # `!= NULL` candidates from the same scan
    # (lib/libc/minimal/source/stdlib/strtol.c/strtoul.c's `endptr`
    # parameter, tests/lib/c_lib/common exercises many NULL-passing calls)
    # were ruled out first: native_sim's default libc for this test app is
    # picolibc (confirmed via the build's own .config —
    # CONFIG_ZEPHYR_PICOLIBC_MODULE=y, CONFIG_MINIMAL_LIBC not set), so
    # lib/libc/minimal/source/stdlib/*.c is never even compiled in under a
    # bare `west build` — reaching the minimal-libc implementation needs
    # the tests.yaml `.minimal` scenario's CONFIG_MINIMAL_LIBC=y
    # extra_config, a non-default-scenario dead end matching session 9's
    # standing limitation. Mutated build: genuine `Segmentation fault`,
    # exit code 139, at exactly test_clock_getres (2/2 runs identical);
    # every other of the suite's 17 tests unaffected. Reverted build:
    # byte-identical file, all 17 pass, PROJECT EXECUTION SUCCESSFUL.
    {
        "id_suffix": "runtime_clock_getres_nullcheck",
        "category": "runtime_crash",
        "target_file": "subsys/portability/posix/options/clock.c",
        "operator": "runtime_remove_null_check:if (res != NULL) {:if (1) {",
        "target_app": "tests/subsys/portability/posix/timers",
        "board": "native_sim",
    },
    # runtime_off_by_one's third target, found by pivoting the search angle
    # per session 31's lesson (large-buffer-pool-backed candidates like cobs/
    # ring_buffer/min_heap absorb the mutation harmlessly): kernel/msg_q.c's
    # put_msg_in_queue() full-queue guard `if (msgq->used_msgs <
    # msgq->max_msgs) {` gates writes into a *tiny, exactly-sized* ring
    # buffer (tests/kernel/msgq/msgq_api's put_fail() uses
    # MSG_SIZE=4/MSGQ_LEN=2, an 8-byte tbuffer[] filled to capacity by the
    # test itself) — no slack pool to absorb an off-by-one, unlike the
    # earlier dead ends. Loosening `<` to `<=` lets a put succeed once the
    # queue is already full: used_msgs is incremented past max_msgs and the
    # write silently overwrites/corrupts the oldest unread message instead
    # of returning -ENOMSG. Only one `used_msgs < max_msgs` comparison in
    # the whole file, no #N needed. Unconditionally exercised: tests.yaml's
    # default `kernel.message_queue` scenario runs put_fail() as both
    # test_msgq_put_fail and test_msgq_user_put_fail. Mutated build: 4 of
    # msgq_api_1cpu's 8 tests fail deterministically (test_msgq_put_fail,
    # test_msgq_full, test_msgq_purge_when_put, test_msgq_thread_pending —
    # all downstream consumers of the same corrupted full-queue state), no
    # crash/hang, clean PROJECT EXECUTION FAILED. Reverted build:
    # byte-identical file, all 17 tests pass, PROJECT EXECUTION SUCCESSFUL.
    {
        "id_suffix": "runtime_msgq_offbyone",
        "category": "runtime_crash",
        "target_file": "kernel/msg_q.c",
        "operator": "runtime_off_by_one:msgq->used_msgs < msgq->max_msgs:msgq->used_msgs <= msgq->max_msgs",
        "target_app": "tests/kernel/msgq/msgq_api",
        "board": "native_sim",
    },
    # runtime_off_by_one's fourth target, same "small always-fully-filled
    # fixed buffer" search angle as the msgq case above, applied to a
    # different kernel object: kernel/stack.c's k_stack_push() full-stack
    # guard `CHECKIF(stack->next == stack->top) { ret = -ENOMEM; ... }`.
    # Unlike msgq (an `<` comparison), this guard is an equality check —
    # loosening `==` to `>` (rather than the usual `<`-to-`<=` flip) is the
    # correct direction here since `next` never overshoots `top` before this
    # guard runs, so `>` is never true and the check becomes a no-op,
    # letting push succeed once already full. tests/kernel/stack/stack's
    # test_stack_push_full (STACK_LEN=2, a 2-slot ZTEST_BMEM data[] backing
    # array) fills the stack to exactly capacity then asserts the next push
    # returns -ENOMEM. With the guard disabled, that push instead executes
    # `*(stack->next) = data; stack->next++;` with stack->next == stack->top
    # — a genuine one-element out-of-bounds write past the backing array,
    # not just internal state corruption like the msgq case. Checked first
    # for the deliberate-panic self-test trap (this file's stack_fail suite
    # has ztest_set_fault_valid()-guarded NULL-pointer tests under
    # CONFIG_USERSPACE): confirmed empirically that a plain native_sim
    # `west build` for this app does NOT enable CONFIG_USERSPACE by default,
    # so stack_fail only compiles its 3 non-userspace tests — no
    # "Fatal fault" crash-pattern string ever appears on the clean revert
    # side. Mutated build: exactly test_stack_push_full fails (1 of 15
    # total tests), no crash/hang, clean PROJECT EXECUTION FAILED. Reverted
    # build: byte-identical file, all 15 tests pass, PROJECT EXECUTION
    # SUCCESSFUL.
    {
        "id_suffix": "runtime_stack_offbyone",
        "category": "runtime_crash",
        "target_file": "kernel/stack.c",
        "operator": "runtime_off_by_one:stack->next == stack->top:stack->next > stack->top",
        "target_app": "tests/kernel/stack/stack",
        "board": "native_sim",
    },
    # runtime_off_by_one's fifth target, continuing the same "fixed-capacity
    # kernel object, single-comparison full guard" search angle to k_pipe/
    # k_mbox/k_sem per the user's own follow-up. k_pipe was ruled out by
    # reasoning: its own boundary logic all delegates to lib/utils/
    # ring_buffer.c's claim/finish machinery (safe-by-construction via min()
    # clamps, hard to introduce a clean off-by-one), and the one standalone
    # comparison in kernel/pipe.c itself (copy_to_pending_readers()'s
    # `reader_buf->used < reader_buf->len`, deciding whether to unpend a
    # waiting reader) is a genuine hang risk if loosened — a reader that's
    # actually done waiting would never get unpended, and k_pipe_read()'s
    # test callers typically block with K_FOREVER, matching the standing
    # "k_thread_join(..., K_FOREVER)-shaped hang trap" rule. k_mbox was
    # ruled out too: it has no fixed-capacity buffer at all (rendezvous via
    # a waitq, not an array), and its one size-related guard
    # (mbox_message_match()'s size-clamp) only shrinks a *reported* size
    # field, never itself the source of a real OOB access — no clean single-
    # token off-by-one edit changes its outcome for any existing test's
    # actual size gap.
    # kernel/sem.c's k_sem_give() DOES fit the pattern precisely, once
    # broadened from "array index" to "bounded counter increment" (same
    # boundary-guard shape as msgq/stack, just no backing memory array to
    # overflow — count is used standalone, so this entry is a state/logic
    # off-by-one like the msgq case rather than stack's memory OOB):
    # `sem->count += (sem->count != sem->limit) ? 1U : 0U;` caps count at
    # limit. Anchored hint shifts the cap boundary by exactly one
    # (`sem->count != sem->limit` -> `sem->count != sem->limit + 1`, the most
    # literal "off-by-one" of the 4 runtime_off_by_one entries so far — the
    # ternary now adds 1U even when count already equals limit, only capping
    # once count reaches limit+1), letting exactly one give-above-limit
    # through before the cap resumes. tests/kernel/semaphore/semaphore's
    # SEM_MAX_VAL=10 K_SEM_DEFINE-based suite already has two ready-made
    # tests exercising precisely a single give-above-limit:
    # test_sem_count_get (one extra give past 10, asserts count stays 10)
    # and test_k_sem_correct_count_limit (five extra gives, same assertion
    # style, named for exactly this invariant). Mutated build: both fail
    # cleanly (2 of 21 tests in the `semaphore` suite), 19 others pass, no
    # crash/hang. Reverted build: byte-identical file, all tests pass,
    # PROJECT EXECUTION SUCCESSFUL.
    {
        "id_suffix": "runtime_sem_offbyone",
        "category": "runtime_crash",
        "target_file": "kernel/sem.c",
        "operator": "runtime_off_by_one:sem->count != sem->limit:sem->count != sem->limit + 1",
        "target_app": "tests/kernel/semaphore/semaphore",
        "board": "native_sim",
    },
    # --- dts_reg_offbyone scaling: board-diversity round (49->51) ---
    # 49 案例裡 dts_reg_offbyone 只有 2 筆、且都在 qemu_cortex_m3——這是
    # 唯一一個「板子選擇跟 graph_rag 的 DTS 結構化檢索真的有關」的 operator
    # (見專案記憶：graph_rag 是用 dtc 把單一板子的 Kconfig+DTS 轉成結構化
    # graph，只有裝置樹結構相關的 bug 才吃得到這個檢索脈絡)，所以這一輪
    # 專門把它擴散到其餘 4 個 QEMU 板子，同時補強最弱的 tests/drivers 子
    # 系統。
    # dts_reg_offbyone had only 2 entries out of 49, both on qemu_cortex_m3
    # — the only operator where board choice is actually tied to graph_rag's
    # DTS-structural retrieval (see project memory: graph_rag turns a single
    # board's Kconfig+DTS into a structured graph via dtc, so only DTS-
    # structural bugs benefit from that retrieval context). This round
    # spreads it to the remaining 4 QEMU boards and strengthens the weakest
    # (tests/drivers) subsystem at the same time.
    #
    # qemu_riscv32：把 gpio_mmio_latch 測試 (原本只有 qemu_cortex_m3.overlay)
    # 移植到 riscv32 virt 板。跟 m3 案例同一招——縮小宣告的 RAM reg、把
    # gpio-mmio-latch 放進騰出來的尾端——但這次先確認了 QEMU 端真的用
    # -m 256 跟 DTS 宣告的 ram0 size (256MB) 精確吻合 (board.cmake 三個
    # QEMU_FLAGS 分支都寫死 -m 256)，代表宣告邊界==實體邊界，跟 m3 那台
    # MCU 真正只有 64KB SRAM 是同一種「宣告邊界就是真實邊界」情境 (不像
    # xtensa dc233c 那樣宣告 16MB 但 QEMU 實際仍配置 128MB，宣告邊界外還
    # 是真實記憶體，移一點點位址不會有任何效果)。riscv32 這裡沒有 MMU
    # (build log 印 "No satp mode set. Defaulting to 'bare'")，實體位址
    # 直接可存取，不像 aarch64 需要頁表映射，所以不需要額外的
    # zephyr,memory-region 節點。經 west build -t run 實測驗證兩端：
    # golden (未變異) port 5/5 全過；mutate 端 (位址從 0x8fffff00 位移
    # 0x100 到真正邊界 0x90000000) 印出 RISC-V 硬體 trap
    # ("mcause: 7, Store/AMO access fault, mtval: 90000000")，是真正的
    # 執行期記憶體存取錯誤，不是建置期擋下來的。
    # qemu_riscv32: ported the gpio_mmio_latch test (previously only had a
    # qemu_cortex_m3.overlay) to the riscv32 virt board — same trick as the
    # m3 case (shrink the declared RAM reg, place gpio-mmio-latch in the
    # freed tail) but first confirmed QEMU's actual -m 256 exactly matches
    # the DTS-declared ram0 size (256MB; all 3 QEMU_FLAGS branches in
    # board.cmake hardcode -m 256) — i.e. declared boundary == true physical
    # boundary, the same situation as the m3 MCU's real 64KB SRAM (unlike
    # xtensa dc233c, whose declared 16MB sits inside a much larger true
    # 128MB QEMU allocation, where nudging the address a little does
    # nothing). riscv32 here has no MMU (build log prints "No satp mode set.
    # Defaulting to 'bare'"), so physical addresses are directly accessible
    # without page-table mapping, unlike aarch64 — no extra
    # zephyr,memory-region node needed. Empirically verified both sides via
    # west build -t run: the golden (unmutated) port passes 5/5; the mutated
    # side (address nudged from 0x8fffff00 by 0x100 to the true boundary
    # 0x90000000) prints a genuine RISC-V hardware trap ("mcause: 7,
    # Store/AMO access fault, mtval: 90000000") — a real runtime memory-
    # access fault, not something the build stage catches.
    #
    # 20th 系統性 pipeline bug 順帶發現並修好：qemu_oracle.py 的
    # crash_patterns 只列了 Usage/Bus/CPU Page Fault 這幾個具名例外字串，
    # 完全沒涵蓋 aarch64 的 Data Abort 或 RISC-V 的 Store/AMO/Load access
    # fault，導致這兩種板子上真正的硬體 trap 完全偵測不到。已改成直接抓
    # 所有 arch 的 fault handler 最終都會印的共用摘要行
    # ">>> ZEPHYR FATAL ERROR"，一次涵蓋所有 arch，不用窮舉每個例外名稱。
    # 20th systemic pipeline bug found and fixed along the way:
    # qemu_oracle.py's crash_patterns only enumerated Usage/Bus/CPU Page
    # Fault by name, missing aarch64's Data Abort and RISC-V's Store/AMO/
    # Load access fault entirely — real hardware traps on those two boards
    # were silently undetected. Fixed by matching the shared summary line
    # every arch's fault handler converges on, ">>> ZEPHYR FATAL ERROR",
    # instead of enumerating each arch's exception name.
    #
    # 這個案例需要移植目標測試到一個它原本沒有的板子 (新增
    # boards/qemu_riscv32.overlay、修改 tests.yaml 的 platform_allow)——單一
    # target_file mutation 機制表達不了這種「baseline checkout 之外還需要
    # 額外檔案」的情境，因此順帶擴充了 FaultInjector 的 extra_files 機制
    # (bind-mount 到 staging 路徑，checkout 完成後才 cp 進最終位置，避免跟
    # git checkout 寫入既有追蹤檔案衝突)。
    # This case needs porting the target test to a board it didn't
    # originally support (adding boards/qemu_riscv32.overlay, editing
    # tests.yaml's platform_allow) — the single-target_file mutation
    # mechanism can't express needing files beyond the baseline checkout, so
    # this also extends FaultInjector with an extra_files mechanism (bind-
    # mounted to a staging path, then cp'd into place only after checkout
    # completes, avoiding a collision with git checkout writing to an
    # already-tracked file).
    {
        "id_suffix": "dts_gpio_latch_offbyone_riscv32",
        "category": "runtime_crash",
        "target_file": "tests/drivers/gpio/gpio_mmio_latch/boards/qemu_riscv32.overlay",
        "operator": "dts_reg_offbyone:0x8fffff00:0x100",
        "target_app": "tests/drivers/gpio/gpio_mmio_latch",
        "board": "qemu_riscv32",
        "extra_files": {
            "tests/drivers/gpio/gpio_mmio_latch/boards/qemu_riscv32.overlay":
                os.path.join(os.path.dirname(__file__), "injection_assets", "gpio_mmio_latch_riscv32", "qemu_riscv32.overlay"),
            "tests/drivers/gpio/gpio_mmio_latch/tests.yaml":
                os.path.join(os.path.dirname(__file__), "injection_assets", "gpio_mmio_latch_riscv32", "tests.yaml"),
        },
    },
    # qemu_x86：既有的 tests/drivers/firmware/qemu_fwcfg 測試本來就有
    # qemu_x86.overlay，不需要移植——直接對它既有的 IO port `reg = <0x510
    # 0x18>;` 做 dts_reg_offbyone。這是 IO port 定址 (x86 in/out 指令)，不是
    # 記憶體映射 MMIO，但概念完全對應：qemu_fwcfg_ioport.c 的
    # sel_port/data_port 都是直接從這個 reg 的位址值算出來、原封不動拿去做
    # sys_out16/sys_in8，沒有任何邊界檢查。QEMU 真正的 fw-cfg 硬體選擇埠/
    # 資料埠是寫死在 0x510/0x511，位移 0x2 之後 (sel_port=0x512,
    # data_port=0x513) 兩個埠都不再對應真正硬體，寫入沒有目標、讀取拿到
    # 浮空匯流排值——這是「IO 位址算式漏了一項」的真實工程失誤，跟 reg
    # 的記憶體邊界情境同一類語意，只是位址空間不同。已用 west build -t
    # run 實測驗證兩端：mutate 端 4 個測試全部因為
    # `device_is_ready(fwcfg)` 變 false 而失敗 (`fwcfg device not ready`)，
    # 印出 PROJECT EXECUTION FAILED；revert 端 4/4 全過，印出 PROJECT
    # EXECUTION SUCCESSFUL。
    # qemu_x86: the existing tests/drivers/firmware/qemu_fwcfg test already
    # has a qemu_x86.overlay — no porting needed, just apply
    # dts_reg_offbyone directly to its existing IO port `reg = <0x510
    # 0x18>;`. This is I/O-port addressing (x86 in/out instructions), not
    # memory-mapped MMIO, but the concept maps over exactly:
    # qemu_fwcfg_ioport.c's sel_port/data_port are computed straight from
    # this reg's address value and used unguarded in sys_out16/sys_in8, no
    # bounds check at all. QEMU's real fw-cfg hardware selector/data ports
    # are hardwired at 0x510/0x511; nudging by 0x2 (sel_port=0x512,
    # data_port=0x513) points both at ports that don't correspond to real
    # hardware anymore — writes go nowhere, reads return floating-bus
    # garbage — a genuine "I/O address arithmetic missed a term" engineering
    # mistake, the same semantic class as a memory reg boundary miss, just
    # in a different address space. Empirically verified both sides via west
    # build -t run: the mutated side fails all 4 tests because
    # device_is_ready(fwcfg) itself now returns false ("fwcfg device not
    # ready"), printing PROJECT EXECUTION FAILED; the reverted side passes
    # 4/4, printing PROJECT EXECUTION SUCCESSFUL.
    {
        "id_suffix": "dts_fwcfg_offbyone_x86",
        "category": "runtime_crash",
        "target_file": "tests/drivers/firmware/qemu_fwcfg/boards/qemu_x86.overlay",
        "operator": "dts_reg_offbyone:0x510:0x2",
        "target_app": "tests/drivers/firmware/qemu_fwcfg",
        "board": "qemu_x86",
    },
    # --- runtime_off_by_one scaling: first tests/drivers entry (51->52) ---
    # c_api_substitute turned up nothing usable in tests/drivers this round
    # (searched thoroughly: every tests/drivers file combining
    # k_thread_create/K_THREAD_DEFINE with k_sleep/k_msleep either only
    # supports real nRF/STM32/etc. hardware boards, or (tests/drivers/crc)
    # has no zephyr,crc chosen node on any native_sim/QEMU board at all, or
    # (tests/drivers/tee/optee, the one native_sim-buildable candidate) uses
    # a wait_thread priority — K_PRIO_COOP(4) — that's *higher* than the
    # ztest main thread, so a k_sleep->k_yield substitution on the main
    # thread's side wouldn't change anything observable: a strictly-higher-
    # priority ready thread runs regardless of sleep vs yield. The 6 existing
    # k_yield() call sites in tests/drivers are all `while (cond) {
    # k_yield(); }` polling loops, where substituting k_sleep just makes the
    # loop slower without flipping pass/fail — not this operator's shape.
    # Pivoted to runtime_off_by_one instead, applying the same "fixed-
    # capacity object with a single boundary comparison, backed by a buffer
    # sized to exactly that capacity" angle that already won 3 kernel/*.c
    # entries (msgq/stack/sem, sessions 32-33) — this time to a driver.
    #
    # drivers/flash/flash_simulator.c's flash_range_is_valid() rejects an
    # access as out-of-range via `(cfg->flash_size - offset) < len` (plus an
    # `offset >= cfg->flash_size` check that alone doesn't open a hole here:
    # for any len>0 request sitting exactly at the boundary, this second
    # check would still independently reject it, so the length check is the
    # one that actually needs loosening) — cfg->flash_size is the real,
    # fixed-size backing buffer's (`dev_data->mock_flash`) capacity, no DT-
    # coupling slack the way native_sim's SRAM-backed drivers had in session
    # 3's dts_reg_offbyone dead end. tests/drivers/flash_simulator/
    # flash_sim_impl's own test_out_of_bounds already exercises the tightest
    # possible overshoot the driver's 4-byte write_block_size alignment
    # allows: `flash_write(flash_dev, TEST_SIM_FLASH_END - 4, data, 8)` —
    # starts 4 bytes before the end but writes 8, landing exactly 4 bytes
    # past it — and asserts `-EINVAL`. Anchored hint loosens the length
    # check by exactly that same 4-byte unit: `(cfg->flash_size - offset) <
    # len` -> `(cfg->flash_size - offset) + 4 < len`, letting this exact
    # call through as though it fit. Verified via a direct
    # ./zephyr/zephyr.exe run (native_sim self-exits, no `west build -t run`
    # needed): golden passes 8/11 (3 skipped), including test_out_of_bounds;
    # mutated build crashes with a genuine `Segmentation fault` (exit 139)
    # right at test_out_of_bounds — the loosened check lets flash_write
    # actually memcpy 4 bytes past mock_flash's real allocated buffer, a
    # true heap-overflow memory-safety fault, not just a corrupted-but-
    # readable value (stronger evidence than a mere zassert mismatch).
    # Reverted file confirmed byte-identical to the original before wiring
    # into the catalog.
    {
        "id_suffix": "runtime_flash_sim_offbyone",
        "category": "runtime_crash",
        "target_file": "drivers/flash/flash_simulator.c",
        "operator": "runtime_off_by_one:(cfg->flash_size - offset) < len:(cfg->flash_size - offset) + 4 < len",
        "target_app": "tests/drivers/flash_simulator/flash_sim_impl",
        "board": "native_sim",
    },
    # runtime_off_by_one's second tests/drivers win, same round: continued
    # the "fixed-capacity buffer + single boundary comparison" angle from
    # flash_simulator.c to eeprom_simulator.c, a sibling native_sim/QEMU-
    # buildable memory-backed emulator driver. eeprom_range_is_valid()'s
    # `(offset + len) <= config->size` guards `mock_eeprom`, a real fixed-
    # size static array (`uint8_t mock_eeprom[DT_INST_PROP(0, size)]`), no
    # DT-coupling slack. tests/drivers/eeprom/api's own test_out_of_bounds
    # already exercises `eeprom_write(eeprom, size - 1, data, sizeof(data))`
    # with a 4-byte data buffer — offset = size-1, len = 4, landing exactly
    # 3 bytes past the true end — and asserts `-EINVAL`. Anchored hint
    # loosens the boundary by that same 3-byte unit (`<= config->size` ->
    # `<= config->size + 3`), letting this exact call through as though it
    # fit — the same "match the exact overshoot the existing test already
    # exercises" technique as the flash_simulator entry immediately above,
    # just a different constant since this driver's test doesn't have a
    # write-alignment requirement forcing a specific overshoot size.
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 7/7;
    # mutated build crashes with a genuine `Segmentation fault` (exit 139)
    # right at test_out_of_bounds — eeprom_write's `memcpy(EEPROM(offset),
    # data, len)` writes 3 bytes past mock_eeprom's real allocated array, a
    # true heap-overflow memory-safety fault. Reverted file confirmed byte-
    # identical to the original before wiring into the catalog.
    {
        "id_suffix": "runtime_eeprom_sim_offbyone",
        "category": "runtime_crash",
        "target_file": "drivers/eeprom/eeprom_simulator.c",
        "operator": "runtime_off_by_one:(offset + len) <= config->size:(offset + len) <= config->size + 3",
        "target_app": "tests/drivers/eeprom/api",
        "board": "native_sim",
    },
    # runtime_off_by_one's third tests/drivers win, same family as flash_sim/
    # eeprom_sim but a different, more literal "off-by-one" this time (delta
    # of exactly 1, not 3/4). Before landing here, `drivers/gnss/gnss_emul.c`
    # was investigated and genuinely ruled out by reasoning alone (no Docker
    # cycle spent): its one array-index pattern
    # (`data->satellites[data->satellites_len]`, backed by a fixed
    # `satellites[GNSS_EMUL_SUPPORTED_SYSTEMS_COUNT]` array) is populated by
    # a loop whose bound (`i < GNSS_EMUL_SUPPORTED_SYSTEMS_COUNT`) exactly
    # matches the array size by construction — loosening it to `<=` would
    # only matter if `data->enabled_systems` ever had the COUNT-th bit set,
    # but the *only* setter, `gnss_emul_set_enabled_systems()`, has its own,
    # separate guard (`systems > GNSS_EMUL_SUPPORTED_SYSTEMS_MASK` where
    # MASK is exactly COUNT bits wide) that makes that unreachable through
    # any public API path — a single-token mutation of the loop bound alone
    # would be a no-op, and mutating the *setter's* guard alone wouldn't
    # matter either (the loop bound remains the real barrier) — genuinely
    # needs two coordinated mutations to open, out of this operator's scope.
    #
    # Searched broadly across every `*_emul.c`/`*simulator*.c` driver file
    # containing `memcpy` for the same "fixed-capacity buffer + single
    # boundary comparison" shape rather than picking sensor FIFO drivers ad
    # hoc — `drivers/bbram/bbram_emul.c` matched immediately.
    # `bbram_emul_read()`'s `offset + size > config->size` guards
    # `dev_data->data`, a real fixed-size static array
    # (`bbram_emul_mem_##inst[DT_INST_PROP(inst, size)]`).
    # `tests/drivers/bbram/emul`'s own `test_bbram_out_of_bounds` already
    # exercises `bbram_read(dev, 0, BBRAM_SIZE + 1, buffer)` where `buffer`
    # is a stack array of exactly `BBRAM_SIZE` bytes — the closest possible
    # overshoot, exactly 1 byte, no larger unit needed unlike flash/eeprom's
    # write-alignment-driven +4/+3. Anchored hint (unqualified, so it lands
    # on the *first* occurrence — this exact guard text appears twice
    # verbatim, once in `bbram_emul_read` and once in `bbram_emul_write`;
    # only the read side needed mutating to flip this one assertion)
    # loosens by exactly that 1-byte unit: `... > config->size` -> `... >
    # config->size + 1`. Verified via a direct ./zephyr/zephyr.exe run:
    # golden passes 8/8; mutated build does *not* crash (BBRAM_SIZE=0xff, so
    # the 1-byte overread into stack padding past `buffer[BBRAM_SIZE]`
    # apparently lands in slack the compiler left rather than faulting) but
    # cleanly fails the test's own assertion instead — `Assertion failed
    # ... bbram_read(dev, 0, BBRAM_SIZE + 1, buffer) not equal to -EFAULT`,
    # `PROJECT EXECUTION FAILED` — still a genuine memory-safety violation
    # (the call actually returned success and copied past the buffer) and a
    # clean single-test failure (7 other tests still pass), same acceptable
    # "state corruption without a hard crash, caught by the test's own
    # assertion" category as the msgq/sem kernel entries. Reverted file
    # confirmed byte-identical to the original before wiring into the
    # catalog.
    {
        "id_suffix": "runtime_bbram_emul_offbyone",
        "category": "runtime_crash",
        "target_file": "drivers/bbram/bbram_emul.c",
        "operator": "runtime_off_by_one:offset + size > config->size:offset + size > config->size + 1",
        "target_app": "tests/drivers/bbram/emul",
        "board": "native_sim",
    },
    # runtime_remove_null_check's first tests/drivers entry, after sessions
    # 38-39 established (uart_emul, i2c_emul, sbs_gauge/bq27z746/bq40z50 fuel
    # gauges) that runtime_off_by_one's vein in tests/drivers was drying up.
    # Applied the "optional output pointer" idiom search (proven 3x already
    # in tests/kernel and tests/subsys/portability — hash_map, thrd_join,
    # clock_getres) to drivers/ for the first time: grepped every driver for
    # `if (x != NULL) { *x = ...` and cross-referenced against
    # native_sim-buildable tests/drivers apps.
    #
    # `drivers/can/can_loopback.c`'s `can_loopback_get_state()` — the driver
    # actually backing native_sim's default `zephyr,canbus` chosen node
    # (confirmed via boards/native/native_sim/native_sim.dts; the more
    # obviously-named `drivers/can/can_fake.c`, checked first, turned out to
    # NOT be what native_sim wires up by default, so it was verified which
    # driver is actually reachable before committing to a target — the same
    # "don't trust a plausible-sounding name" discipline as session 3's DTS
    # dead ends) — has `if (state != NULL) { *state = ...; }` guarding its
    # optional `state` output parameter, exactly matching `can_get_state()`'s
    # documented public API contract (both `state` and `err_cnt` are
    # independently optional). `can_get_state()`'s own inline wrapper
    # (`z_impl_can_get_state` in include/zephyr/drivers/can.h) passes
    # straight through to the driver with no NULL validation of its own, so
    # nothing upstream protects a caller passing `state=NULL`.
    # `tests/drivers/can/api`'s own `test_get_state` already exercises
    # exactly this: `can_get_state(can_dev, NULL, NULL)`, asserting
    # `zassert_ok` (i.e. this must succeed cleanly, not crash — NULL is
    # documented-valid here, not an error case). Verified via a direct
    # ./zephyr/zephyr.exe run: golden run passes (incl. test_get_state,
    # confirmed individually in the log); mutated build (`if (state !=
    # NULL) {` -> `if (1) {`) crashes with a genuine `Segmentation fault`
    # (exit 139) right at test_get_state — an unconditional `*state = ...`
    # write through the caller's NULL pointer. Reverted file confirmed
    # byte-identical to the original before wiring into the catalog.
    {
        "id_suffix": "runtime_can_loopback_nullcheck",
        "category": "runtime_crash",
        "target_file": "drivers/can/can_loopback.c",
        "operator": "runtime_remove_null_check:if (state != NULL) {:if (1) {",
        "target_app": "tests/drivers/can/api",
        "board": "native_sim",
    },
    # runtime_remove_null_check's second tests/drivers entry, this time on
    # GPIO. Session 39's broad drivers/-wide "!= NULL) { *x = ..." grep
    # found 9 GPIO drivers, but every single one is a vendor-specific real-
    # hardware controller (andestech/realtek/sifive/designware/litex/nxp/
    # microchip/nordic/silabs) — cross-checked each DT_DRV_COMPAT against
    # native_sim's and all 5 QEMU boards' devicetree sources and found zero
    # matches; native_sim's actual GPIO backend is `zephyr,gpio-emul`
    # (drivers/gpio/gpio_emul.c), which the broad grep hadn't matched
    # because its own null checks use a different (still valid but
    # differently-spelled) shape the pattern didn't happen to catch, so it
    # needed a direct, targeted look rather than trusting the earlier list.
    #
    # `gpio_emul_port_get_direction()` (gated behind CONFIG_GPIO_GET_DIRECTION)
    # implements the public `gpio_port_get_direction()` API, whose `inputs`
    # and `outputs` parameters are independently optional by documented
    # contract — confirmed by the header's own inline helpers:
    # `gpio_pin_is_input()` calls `gpio_port_get_direction(port, BIT(pin),
    # &pins, NULL)` (outputs=NULL) and the symmetric `gpio_pin_is_output()`
    # passes inputs=NULL. `z_impl_gpio_port_get_direction()` passes straight
    # through to the driver with no NULL check of its own.
    # `tests/drivers/gpio/gpio_get_direction` (CONFIG_GPIO_GET_DIRECTION=y,
    # native_sim-buildable via the board's `led0`/`gpio-leds` alias) calls
    # `gpio_pin_is_input()` as the very first check in all 4 of its test
    # functions — so mutating just the `outputs != NULL` guard is hit
    # immediately and unconditionally, every time. Verified via a direct
    # ./zephyr/zephyr.exe run: golden passes 4/4; mutated build (`if
    # (outputs != NULL) {` -> `if (1) {`) crashes with a genuine
    # `Segmentation fault` (exit 139) right at the first test
    # (test_disconnect) — an unconditional `*outputs = op;` write through
    # gpio_pin_is_input()'s NULL argument. Reverted file confirmed byte-
    # identical to the original before wiring into the catalog.
    {
        "id_suffix": "runtime_gpio_emul_nullcheck",
        "category": "runtime_crash",
        "target_file": "drivers/gpio/gpio_emul.c",
        "operator": "runtime_remove_null_check:if (outputs != NULL) {:if (1) {",
        "target_app": "tests/drivers/gpio/gpio_get_direction",
        "board": "native_sim",
    },
    # runtime_off_by_one's fourth tests/drivers entry, drivers/adc/adc_emul.c
    # — the first catalog entry that needed *adding* a new test case rather
    # than finding one that already exists, following the same "port an
    # existing thing to a new context" precedent as session 34's
    # gpio_mmio_latch riscv32 port (which is what motivated FaultInjector's
    # extra_files mechanism in the first place).
    #
    # `adc_emul_const_value_set(dev, chan, value)` (and 3 siblings —
    # `_const_raw_value_set`, `_value_func_set`, `_raw_value_func_set`) are
    # public emulator-control functions test code calls directly, each
    # guarded by `if (chan >= config->num_channels) { return -EINVAL; }`
    # before writing into `data->chan_cfg[chan]` (a fixed-size array sized to
    # exactly `nchannels` from DT). This is a much better-shaped candidate
    # than the fuel-gauge/`i2c_emul.c` dead ends (sessions 39-40): `chan` is
    # directly test-controlled, not funneled through one internally-
    # consistent real-driver caller. But — unlike flash_simulator/
    # eeprom_simulator/bbram_emul — no *existing* test in
    # `tests/drivers/adc/adc_emul` ever calls with an out-of-range channel;
    # every call uses `ADC_1ST_CHANNEL_ID`/`ADC_2ND_CHANNEL_ID` (native_sim's
    # `adc0` node declares `nchannels = <2>`, so valid channels are exactly
    # {0, 1}). Rather than settle for a low-confidence huge-delta mutation
    # (declined for `i2c_emul.c`'s `UINT32_MAX` case in session 39) or skip
    # the driver entirely, added one new `ZTEST_USER` case,
    # `test_adc_emul_const_value_set_invalid_channel`, calling
    # `adc_emul_const_value_set(adc_dev, ADC_INVALID_CHANNEL_ID, 1500)` where
    # `ADC_INVALID_CHANNEL_ID` is `DT_PROP(DT_INST(0, zephyr_adc_emul),
    # nchannels)` (= 2, one past the valid range, derived from the same DT
    # prop the driver's own guard checks against — not a hardcoded magic
    # number), asserting `-EINVAL`. The full modified `main.c` (13 existing
    # tests, byte-identical, plus this one new test appended before
    # `adc_emul_setup`) is delivered via `extra_files`, the same staging-then-
    # cp mechanism used for the riscv32 gpio_mmio_latch port.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden (unmutated
    # driver, with the new test in place) passes 13/13, including the new
    # test logging "unsupported channel 2" and returning -EINVAL correctly;
    # mutated build (anchored on the *first* occurrence of `if (chan >=
    # config->num_channels) {`, inside `adc_emul_const_value_set` — the
    # function the new test targets — loosened to `if (chan > ...) {`) fails
    # cleanly and exactly at the new test (12/13 pass, 1 fail,
    # `PROJECT EXECUTION FAILED`) with no cascading effect on the other 12
    # tests. Reverted driver file confirmed byte-identical to the original
    # before wiring into the catalog.
    {
        "id_suffix": "runtime_adc_emul_offbyone",
        "category": "runtime_crash",
        "target_file": "drivers/adc/adc_emul.c",
        "operator": "runtime_off_by_one:if (chan >= config->num_channels) {:if (chan > config->num_channels) {",
        "target_app": "tests/drivers/adc/adc_emul",
        "board": "native_sim",
        "extra_files": {
            "tests/drivers/adc/adc_emul/src/main.c":
                os.path.join(os.path.dirname(__file__), "injection_assets", "adc_emul_invalid_channel_test", "main.c"),
        },
    },
    # runtime_off_by_one's fifth tests/drivers entry, drivers/rtc/rtc_emul.c
    # — second entry needing an added test case (after adc_emul.c), and the
    # first needing an *extra Kconfig* on top of the extra test file: this
    # target file's alarm-handling functions
    # (`rtc_emul_alarm_get_supported_fields`/`_set_time`/`_get_time`/
    # `_is_pending`) are only *compiled into the test app at all* when
    # CONFIG_RTC_ALARM is defined (tests/drivers/adc/adc_emul/CMakeLists.txt
    # guards `src/test_alarm.c`/`src/test_alarm_callback.c` behind
    # `if(DEFINED CONFIG_RTC_ALARM)`), and native_sim's default
    # tests/drivers/rtc/rtc_api build does *not* set it — confirmed
    # empirically (not assumed) by building with only the new test staged
    # first: golden run showed only 3 tests total (test_set_get_time/
    # test_time_counting/test_y2k), the whole alarm-related file silently
    # absent. Fixed by also staging a modified `prj.conf` (adds
    # `CONFIG_RTC_ALARM=y`) via the same `extra_files` mechanism — this is
    # exactly the standing "non-default twister scenario is unreachable by
    # this pipeline's bare west build" limitation documented since session
    # 9, worked around here (rather than skipped) since `extra_files` can
    # already stage arbitrary file overwrites, `prj.conf` included.
    #
    # `rtc_emul_alarm_is_pending(dev, id)` — the simplest of the 4 alarm
    # functions (no output pointer, just `if (data->alarms_count <= id) {
    # return -EINVAL; } ... return (data->alarms[id].pending == true) ? 1 :
    # 0;`) — was chosen as the mutation target. native_sim's `rtc` alias
    # (`alarms-count = <2>`) means alarm id 2 is the exact one-past-the-end
    # boundary. Same identical guard text appears 5 times in the file (the
    # 4 alarm functions plus a 5th not otherwise investigated); the
    # anchored hint's `#4` suffix selects the 4th occurrence specifically
    # (inside `_is_pending`). Added `test_alarm_is_pending_invalid_id` to
    # `test_alarm.c`, calling `rtc_alarm_is_pending(rtc, alarms_count)` and
    # asserting `-EINVAL`.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden (unmutated
    # driver, new test + CONFIG_RTC_ALARM=y in place) passes 6/6; mutated
    # build (anchored 4th-occurrence `<=` -> `<` loosening) fails cleanly
    # and exactly at the new test ("Expected -EINVAL for alarm id 2, got
    # 0" — the OOB read of `alarms[2].pending` happened to read back false/
    # 0 rather than segfaulting, same "state corruption without a hard
    # crash" category as the bbram_emul/msgq/sem entries), 5/6 pass,
    # `PROJECT EXECUTION FAILED`, no cascading effect on the other 5 tests.
    # Reverted driver file confirmed byte-identical to the original before
    # wiring into the catalog.
    {
        "id_suffix": "runtime_rtc_emul_offbyone",
        "category": "runtime_crash",
        "target_file": "drivers/rtc/rtc_emul.c",
        "operator": "runtime_off_by_one:data->alarms_count <= id#4:data->alarms_count < id",
        "target_app": "tests/drivers/rtc/rtc_api",
        "board": "native_sim",
        "extra_files": {
            "tests/drivers/rtc/rtc_api/src/test_alarm.c":
                os.path.join(os.path.dirname(__file__), "injection_assets", "rtc_emul_alarm_invalid_id_test", "test_alarm.c"),
            "tests/drivers/rtc/rtc_api/prj.conf":
                os.path.join(os.path.dirname(__file__), "injection_assets", "rtc_emul_alarm_invalid_id_test", "prj.conf"),
        },
    },
    # runtime_off_by_one's sixth tests/drivers entry, drivers/i2c/i2c_emul.c
    # — revisits the exact guard session 39 found and skipped
    # (`i2c_emul_send_to_target()`'s buffered-mode
    # `if (len > msgs[i].len) { return -ENOMEM; } memcpy(msgs[i].buf, ptr,
    # len);`), this time closing it out with the now-proven "add a tight
    # test via extra_files" technique instead of leaving it aside. Session
    # 39's only existing test, `test_read_request_overflow`, reports
    # `UINT32_MAX` bytes — a delta far too large for a genuine off-by-one
    # mutation to flip.
    #
    # This target needed the most `extra_files` staging of any entry so
    # far, because `drivers.i2c.emul.target_buf` (the twister scenario that
    # actually compiles `test_forwarding_buf.cpp`, where the overflow tests
    # live) is itself non-default: `tests.yaml` reaches it only via
    # `extra_configs: CONFIG_I2C_TARGET_BUFFER_MODE=y` *and*
    # `extra_dtc_overlay_files: boards/native_sim.buf.overlay` layered on
    # top of the base `boards/native_sim.overlay` — twister-only mechanisms
    # this pipeline's bare `west build -b native_sim` can't apply. Worked
    # around by staging 3 files: the modified `test_forwarding_buf.cpp`
    # (new test appended), a `prj.conf` with `CONFIG_I2C_TARGET_BUFFER_MODE=y`
    # added, and a `native_sim.overlay` that's the base overlay with the
    # buf-overlay's `&i2c1 { target-buffered-mode; };` fragment folded in
    # directly (since only one `boards/<board>.overlay` gets auto-applied,
    # not the twister-scenario-specific extra one) — confirmed
    # `CMakeLists.txt` gates `test_forwarding_buf.cpp` vs
    # `test_forwarding_pio.cpp` purely on `CONFIG_I2C_TARGET_BUFFER_MODE`,
    # so no other file needed touching.
    #
    # Added `test_read_request_overflow_by_one`: a 4-byte `expected[]`
    # source but only a 3-byte `data[]` destination buffer
    # (`ARRAY_SIZE(expected) - 1`), with the fake `buf_read_requested`
    # callback reporting the full 4 bytes available — msgs[i].len (3, from
    # the destination buffer) is exactly 1 less than the reported len (4),
    # asserting `-ENOMEM`. Anchored hint loosens the guard by that same
    # 1-byte unit: `len > msgs[i].len` -> `len > msgs[i].len + 1`.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden (new test +
    # CONFIG_I2C_TARGET_BUFFER_MODE=y + merged overlay in place) passes
    # 9/9; mutated build fails cleanly and exactly at the new test
    # ("-ENOMEM not equal to i2c_read(...)" — the loosened guard let
    # `memcpy(msgs[i].buf, ptr, 4)` write 4 bytes into the 3-byte `data`
    # stack array), 8/9 pass, `PROJECT EXECUTION FAILED`, no cascading
    # effect on the other 8 (including the original `UINT32_MAX` overflow
    # test, still correctly rejected since the mutation only loosens by 1).
    # Reverted driver file confirmed byte-identical to the original before
    # wiring into the catalog.
    {
        "id_suffix": "runtime_i2c_emul_offbyone",
        "category": "runtime_crash",
        "target_file": "drivers/i2c/i2c_emul.c",
        "operator": "runtime_off_by_one:if (len > msgs[i].len) {:if (len > msgs[i].len + 1) {",
        "target_app": "tests/drivers/i2c/i2c_emul",
        "board": "native_sim",
        "extra_files": {
            "tests/drivers/i2c/i2c_emul/src/test_forwarding_buf.cpp":
                os.path.join(os.path.dirname(__file__), "injection_assets", "i2c_emul_overflow_by_one_test", "test_forwarding_buf.cpp"),
            "tests/drivers/i2c/i2c_emul/prj.conf":
                os.path.join(os.path.dirname(__file__), "injection_assets", "i2c_emul_overflow_by_one_test", "prj.conf"),
            "tests/drivers/i2c/i2c_emul/boards/native_sim.overlay":
                os.path.join(os.path.dirname(__file__), "injection_assets", "i2c_emul_overflow_by_one_test", "native_sim.overlay"),
        },
    },
    # runtime_off_by_one's first tests/subsys entry via drivers/video/
    # video_emul_rx.c — user's "video_emul" suggestion (session 44) led here
    # after two dead ends: `drivers/spi/spi_emul.c` (a thin bus dispatcher
    # like i2c_emul.c but with no arrays/boundaries at all — no candidate,
    # and no tests/drivers/spi app even builds on native_sim anyway, so
    # doubly unreachable) and `drivers/video/video_emul_imager.c`'s
    # `emul_imager_fake_regs[20]` array indexed by an *unchecked* `reg_addr`
    # (uint8_t, 0-255) — flagged to the user as a genuinely different shape
    # (an already-unguarded array access in upstream code, not an existing
    # correct guard this operator's mutation model can loosen) and set
    # aside per explicit direction rather than forced into the catalog.
    #
    # `emul_rx_enqueue()`'s `if (vbuf->size < fmt->pitch * fmt->height) {
    # return -ENOMEM; }` is the right shape: guards a caller-supplied
    # `struct video_buffer`'s `.size` field (test-controlled via
    # `video_buffer_alloc()`) before `emul_rx_worker()` later
    # unconditionally `memcpy`s `fmt->pitch * fmt->height` bytes into
    # `vbuf->buffer`. Confirmed `video_enqueue()`'s public wrapper
    # (`subsys/video/buffer.c`) does no size validation of its own — only
    # checks `dev`/`buf` non-NULL and a valid pool index — so nothing
    # upstream blocks a caller from enqueueing a too-small buffer.
    # `tests/subsys/video/api` (native_sim-buildable) already has
    # `test_video_vbuf`, which allocates a buffer of the exact required
    # size via `video_buffer_alloc(fmt.pitch * fmt.height, ...)` — never
    # one byte short. Added `test_video_vbuf_too_small` (same suite,
    # `video_common`): identical setup, but allocates `fmt.pitch *
    # fmt.height - 1` and expects `video_enqueue()` to return `-ENOMEM`.
    # Anchored hint loosens the guard by that same 1-byte unit: `vbuf->size
    # < fmt->pitch * fmt->height` -> `vbuf->size < fmt->pitch * fmt->height
    # - 1`.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden (new test in
    # place) passes 10/10 across all 3 suites in the app; mutated build
    # produces a genuine `Segmentation fault` (exit 139) — stronger
    # evidence than a clean assertion mismatch: the loosened guard lets
    # `video_enqueue()` return success for the undersized buffer, the test
    # (using `zexpect_equal`, non-fatal) logs the mismatch and continues to
    # `video_buffer_release(vbuf)`, but the work item queued by the
    # already-submitted enqueue still fires asynchronously afterward and
    # the worker's log line prints the buffer pointer as `(nil)` right
    # before the crash — a genuine use-after-free/corruption in the
    # async worker path, not merely a same-thread OOB write. Reverted
    # driver file confirmed byte-identical to the original before wiring
    # into the catalog.
    {
        "id_suffix": "runtime_video_emul_rx_offbyone",
        "category": "runtime_crash",
        "target_file": "drivers/video/video_emul_rx.c",
        "operator": "runtime_off_by_one:if (vbuf->size < fmt->pitch * fmt->height) {:if (vbuf->size < fmt->pitch * fmt->height - 1) {",
        "target_app": "tests/subsys/video/api",
        "board": "native_sim",
        "extra_files": {
            "tests/subsys/video/api/src/video_emul.c":
                os.path.join(os.path.dirname(__file__), "injection_assets", "video_emul_rx_too_small_test", "video_emul.c"),
        },
    },
    # runtime_off_by_one's second tests/subsys entry, via a sensor emulator
    # exercised through tests/subsys/sensing (session 46, following up on
    # session 45's "check other tests/subsys-housed emulator-backed
    # subsystems" suggestion). tests/subsys/sensing's native_sim.conf sets
    # CONFIG_EMUL_BMI160=y, so this test app already compiles in
    # drivers/sensor/bosch/bmi160/emul_bmi160.c even though its own test
    # (test_sensing_get_sensors) never exercises the emulator's public API.
    #
    # emul_bmi160_get_reg_value()'s `if (reg_number < 0 || reg_number +
    # count > BMI160_REG_COUNT) { return -EINVAL; }` is the same shape as
    # the video_emul_rx.c win: a real, correct guard on a public, exported
    # function (declared in the driver's own emul_bmi160.h, exposed to any
    # app via `zephyr_include_directories_ifdef(CONFIG_EMUL_BMI160 .)`)
    # protecting a real fixed-size backing buffer
    # (`bmi160_emul_reg_##n[BMI160_REG_COUNT]`, a 128-byte static array)
    # from a caller-controlled `memcpy` a few lines later. No existing test
    # calls this function at all. Added `test_bmi160_emul_get_reg_value_oob`
    # (same file, same `sensing_tests` suite): reads 1 byte starting at
    # `BMI160_REG_COUNT` (one past the last valid register) and expects
    # `-EINVAL`. Loosened the guard's bound by exactly that 1-byte unit.
    #
    # Verified via a direct ./zephyr/zephyr.exe run before wiring into the
    # pipeline: golden (new test in place) passes 2/2; mutated build lets
    # the OOB read return 0 instead of -EINVAL, failing the new test's
    # zassert cleanly (`PROJECT EXECUTION FAILED`) while
    # test_sensing_get_sensors still passes — an isolated failure, not a
    # segfault, since a 1-byte over-read of a static array doesn't cross a
    # page boundary on native_sim. Reverted driver file confirmed
    # byte-identical. Passed the full two-sided gate via `verify_cases.py`
    # on the first attempt.
    {
        "id_suffix": "runtime_bmi160_emul_reg_oob",
        "category": "runtime_crash",
        "target_file": "drivers/sensor/bosch/bmi160/emul_bmi160.c",
        "operator": "runtime_off_by_one:if (reg_number < 0 || reg_number + count > BMI160_REG_COUNT) {:if (reg_number < 0 || reg_number + count > BMI160_REG_COUNT + 1) {",
        "target_app": "tests/subsys/sensing",
        "board": "native_sim",
        "extra_files": {
            "tests/subsys/sensing/src/main.c":
                os.path.join(os.path.dirname(__file__), "injection_assets", "bmi160_emul_reg_oob_test", "main.c"),
        },
    },
    # runtime_off_by_one's third tests/subsys entry (session 46 continued):
    # a genuine full-blown false-start happened first this round —
    # tests/subsys/zbus/hlp_priority_boost looked like a fresh
    # thread_priority_swap win, was independently re-derived and verified
    # through the real pipeline, then discovered (only after reading the
    # rest of this memory file's middle sessions, which a truncated
    # head+tail read had skipped) to be an exact duplicate of session 22's
    # already-registered thread_priority_swap_zbus_hlp entry — same file,
    # same operator string. Removed before committing; the lesson carried
    # forward is to always read the full catalog (or grep it for the
    # target file) before investing a recon round in a subsystem, not just
    # the memory file's most recent sessions.
    #
    # The actual new win: subsys/storage/stream/stream_flash.c's
    # `stream_flash_buffered_write()` has
    # `if (ctx->bytes_written + ctx->buf_bytes + len > ctx->available) {
    # return -ENOMEM; }` — a real, correct guard against writing past the
    # caller-declared `available` region (set via `stream_flash_init`),
    # checked *before* any memcpy into the internal `ctx->buf`/flash write
    # happens. tests/subsys/storage/stream/stream_flash (native_sim,
    # no scenario gating) has 14 tests total but none probe this exact
    # boundary — only `stream_flash_init`'s own separate, different size
    # check (`FLASH_AVAILABLE + 4`) is tested.
    #
    # Added `test_stream_flash_buffered_write_available_offbyone`: reinits
    # `ctx` with a small `available` (100 bytes, well inside native_sim's
    # real ~1.9MB flash region so no genuine OOB write risk either
    # direction) and calls `stream_flash_buffered_write` with exactly 101
    # bytes using the file's own existing large `write_buf` (16KB, no
    # under-sized source-buffer risk), asserting `-ENOMEM`. Loosened the
    # guard by that same 1-byte unit (`> ctx->available` ->
    # `> ctx->available + 1`), so the loosened check no longer rejects
    # bytes_written+buf_bytes+len == available+1 — the write silently
    # succeeds and lands 1 byte past the caller's declared logical
    # boundary (still real, valid physical flash, so no crash — a clean
    # state/logic violation caught purely by the test's own assertion,
    # same category as msgq/sem/bbram/rtc_emul/i2c_emul).
    #
    # Verified via a direct ./zephyr/zephyr.exe run before wiring into the
    # pipeline: golden (new test in place) passes 14/15 (1 unrelated,
    # pre-existing skip); mutated build fails cleanly and only at the new
    # test (13/15 pass, `PROJECT EXECUTION FAILED`), no cascade to any of
    # the other 13 tests. Reverted file confirmed byte-identical via
    # `git status --porcelain`.
    {
        "id_suffix": "runtime_stream_flash_available_offbyone",
        "category": "runtime_crash",
        "target_file": "subsys/storage/stream/stream_flash.c",
        "operator": "runtime_off_by_one:if (ctx->bytes_written + ctx->buf_bytes + len > ctx->available) {:if (ctx->bytes_written + ctx->buf_bytes + len > ctx->available + 1) {",
        "target_app": "tests/subsys/storage/stream/stream_flash",
        "board": "native_sim",
        "extra_files": {
            "tests/subsys/storage/stream/stream_flash/src/main.c":
                os.path.join(os.path.dirname(__file__), "injection_assets", "stream_flash_available_offbyone_test", "main.c"),
        },
    },
    # runtime_remove_null_check's first tests/subsys entry (session 46
    # continued, user asked to keep going into tests/subsys/dfu/img_util).
    # subsys/dfu/img_util/flash_img.c's flash_img_buffered_write() itself
    # delegates all boundary math to stream_flash_buffered_write() (no
    # independent guard of its own — the same file already carries a
    # runtime_off_by_one entry above, so a second mutation there would
    # just be the identical bug reused, not attempted). flash_img_check()
    # is the more promising function: `if (!ctx || !fic) { return
    # -EINVAL; }` guards against NULL inputs before dereferencing either.
    # Written as `!ptr` shorthand rather than the operator's usual `!=
    # NULL` literal form, but the anchored-hint mechanism is pure literal
    # text replacement, so it works identically — same underlying
    # NULL-check-removal mutation, just a different (equally common) C
    # idiom for expressing it.
    #
    # Unlike every prior `runtime_remove_null_check` entry (all "optional
    # output pointer" idioms needing a fresh test to reach the NULL path),
    # `tests/subsys/dfu/img_util`'s own `test_check_flash` *already*
    # exercises this exact guard: `flash_img_check(NULL, NULL, 0)` is the
    # very first call in its NULL-validation block, asserting `ret ==
    # -EINVAL`. No `extra_files`/new test needed at all — a existing,
    # already-correct test directly probes the guard this operator
    # disables.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 3/3;
    # mutated build (`if (!ctx || !fic) {` -> `if (0) {`) crashes with a
    # genuine `Segmentation fault` (exit 139) immediately at
    # `test_check_flash` — the disabled guard lets `flash_img_check` reach
    # `fac.match = fic->match` with `fic` still NULL, a direct NULL-pointer
    # read. Reverted file confirmed byte-identical via `git status
    # --porcelain`.
    {
        "id_suffix": "runtime_flash_img_check_nullcheck",
        "category": "runtime_crash",
        "target_file": "subsys/dfu/img_util/flash_img.c",
        "operator": "runtime_remove_null_check:if (!ctx || !fic) {:if (0) {",
        "target_app": "tests/subsys/dfu/img_util",
        "board": "native_sim",
    },
    # runtime_off_by_one's fourth tests/subsys entry (session 46
    # continued, following up on the "storage/flash_map is a sibling of
    # the just-won storage/stream, untried" lead). subsys/storage/flash_map's
    # own flash_map.c delegates every boundary check
    # (flash_area_read/write/erase/copy/flatten) to a single shared inline
    # helper in flash_map_priv.h, `is_in_flash_area_bounds()`: `(off >= 0)
    # && (off < fa->fa_size) && (len <= (fa->fa_size - off))` — the
    # fundamental gate for the whole flash_map abstraction layer, one
    # level above the raw flash driver (a different layer than session
    # 35's `flash_simulator.c` win, which mutated the driver itself).
    #
    # `tests/subsys/storage/flash_map`'s own `test_parameter_overflows`
    # already probes this guard, but only with `(size_t)(-1)`-style
    # integer-overflow lengths — a huge overshoot, not a tight one (same
    # "not a genuine off-by-one, closer to the check being deleted
    # entirely" shape session 39 declined for `i2c_emul.c`'s
    # `UINT32_MAX`-only test, except this time worth fixing with a new
    # test rather than skipping, per the toolkit proven since session 42).
    # Added `test_flash_area_bounds_offbyone` to
    # `tests/subsys/storage/flash_map/src/main.c`: reads 2 bytes starting
    # 1 byte before the end of `slot1_partition` (a real sub-partition,
    # smaller than native_sim's whole physical flash device, so the extra
    # byte lands on real adjacent flash rather than past the device's own
    # true end — matching the "state/logic violation on a still-valid
    # address, not a hard crash" category), asserting `-EINVAL`. Loosened
    # the guard's length check by that same 1-byte unit (`len <=
    # (fa->fa_size - off)` -> `len <= (fa->fa_size - off) + 1`).
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 11/11;
    # mutated build fails cleanly and only at the new test (10/11 pass,
    # `PROJECT EXECUTION FAILED`), zero cascade to the other 10 tests
    # (including the pre-existing, unaffected `test_parameter_overflows`).
    # Reverted file confirmed byte-identical via `git status --porcelain`.
    {
        "id_suffix": "runtime_flash_map_bounds_offbyone",
        "category": "runtime_crash",
        "target_file": "subsys/storage/flash_map/flash_map_priv.h",
        "operator": "runtime_off_by_one:len <= (fa->fa_size - off));:len <= (fa->fa_size - off) + 1);",
        "target_app": "tests/subsys/storage/flash_map",
        "board": "native_sim",
        "extra_files": {
            "tests/subsys/storage/flash_map/src/main.c":
                os.path.join(os.path.dirname(__file__), "injection_assets", "flash_map_bounds_offbyone_test", "main.c"),
        },
    },
    # runtime_off_by_one's fifth tests/subsys entry (session 46
    # continued), the fourth win in a row from the subsys/storage +
    # subsys/dfu neighborhood. subsys/dfu/boot/mcuboot.c's
    # `boot_read_bank_header()` guards the caller-declared size of its
    # output `header` parameter: `if (header_size < v1_min_size) { return
    # -ENOMEM; }`, where `v1_min_size = sizeof(uint32_t) +
    # sizeof(struct mcuboot_img_header_v1)`. This is a genuine
    # ABI-compatibility guard against writing into an undersized caller
    # buffer via `header->h.v1.*` fields further down — same
    # "caller-declared output-buffer-size guard" shape as the
    # `stream_flash.c`/`flash_map_priv.h` wins, one level up in the
    # MCUboot-interface abstraction. No existing test in
    # `tests/subsys/dfu/mcuboot` calls `boot_read_bank_header` at all.
    #
    # Added `test_read_bank_header_size_offbyone`: calls
    # `boot_read_bank_header(SLOT0_PARTITION_ID, &header, v1_min_size - 1)`
    # (the test's own real, correctly-sized `struct mcuboot_img_header`
    # local — the mutation cannot cause an actual OOB write here, only a
    # logically-wrong "this undersized-by-1 header_size was accepted"
    # state, same non-crash category as `flash_map_priv.h`'s win),
    # asserting `-ENOMEM`. Loosened the guard by that same 1-byte unit
    # (`header_size < v1_min_size` -> `header_size < v1_min_size - 1`).
    #
    # The size check runs *before* any flash access, so the golden path
    # never even needs a real, valid MCUboot v1 header present in flash —
    # it's rejected purely on the size argument. When the guard is
    # loosened, execution proceeds to `boot_read_v1_header()`, which reads
    # this plain ztest binary's actual flash content (no real MCUboot
    # image header present) and fails its own magic-number check,
    # returning `-EIO` instead — still not `-ENOMEM`, so the mutation is
    # caught either way, without needing to fabricate a valid on-flash
    # header.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 4/4;
    # mutated build fails cleanly and only at the new test (`ret not equal
    # to -ENOMEM, expected -ENOMEM ... got -5`, 3/4 pass, `PROJECT
    # EXECUTION FAILED`), zero cascade to the other 3 tests. Reverted file
    # confirmed byte-identical via `git status --porcelain`.
    {
        "id_suffix": "runtime_mcuboot_header_size_offbyone",
        "category": "runtime_crash",
        "target_file": "subsys/dfu/boot/mcuboot.c",
        "operator": "runtime_off_by_one:if (header_size < v1_min_size) {:if (header_size < v1_min_size - 1) {",
        "target_app": "tests/subsys/dfu/mcuboot",
        "board": "native_sim",
        "extra_files": {
            "tests/subsys/dfu/mcuboot/src/main.c":
                os.path.join(os.path.dirname(__file__), "injection_assets", "mcuboot_header_size_offbyone_test", "main.c"),
        },
    },
    # runtime_off_by_one's first tests/subsys/rtio entry (session 46
    # continued), a deliberate pivot to fresh tests/subsys territory after
    # `tests/subsys/dfu/mcuboot_multi` turned out to be a genuine dead
    # end (its two tests only call thin wrappers around external bootutil
    # functions with no local logic, and everything else it exercises
    # reuses the flash_map_priv.h path already covered).
    #
    # `include/zephyr/rtio/rtio.h`'s `z_impl_rtio_sqe_copy_in_get_handles()`
    # has the same "fixed-capacity pool + upfront count check" shape that
    # already won 3 `kernel/*.c` entries (sessions 32-33): `if (acquirable
    # < sqe_count) { return -ENOMEM; }`, checked before a loop that
    # unconditionally acquires `sqe_count` SQEs from the pool and asserts
    # each one non-NULL (`__ASSERT_NO_MSG(sqe != NULL)`). A sibling
    # function, `rtio_sqe_acquire_array()`, has an *already* well-shaped,
    # tightly-tested guard of its own (self-correcting rollback loop, not
    # a naive single comparison — not a good "loosen it" candidate, and
    # `tests/subsys/rtio/rtio_api`'s own `test_rtio_acquire_array` already
    # tightly probes it) — but no existing test calls
    # `rtio_sqe_copy_in_get_handles`/`rtio_sqe_copy_in` with a count that
    # exceeds the pool's actual remaining capacity by exactly one.
    #
    # Added `test_rtio_sqe_copy_in_offbyone` to
    # `tests/subsys/rtio/rtio_api/src/test_rtio_api.c` (a fresh, isolated
    # `RTIO_DEFINE` context, same pattern `test_rtio_acquire_array` already
    # uses): fills the pool to `SQE_POOL_SIZE - 1` (leaving exactly one
    # free), then requests 2 more, asserting `-ENOMEM`. Loosened the guard
    # by that same 1-unit delta (`acquirable < sqe_count` ->
    # `acquirable < sqe_count - 1`).
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 20/20
    # (across both the `rtio_api` and `rtio_pool` suites in this binary);
    # mutated build produces a genuine kernel panic — `ASSERTION FAIL [sqe
    # != ((void *)0)] @ .../rtio.h:907` followed by `>>> ZEPHYR FATAL
    # ERROR 4: Kernel panic on CPU 0`, halting the whole binary right at
    # the new test (the loosened check lets the acquire-loop run one
    # iteration past the pool's actual capacity, hitting the function's
    # own internal `__ASSERT_NO_MSG(sqe != NULL)`) — a stronger crash
    # signature than a clean assertion failure, same acceptance reasoning
    # as session 18's PM-device-order kernel-panic case (a halt-everything
    # panic is still valid evidence, not a hang — the process exits
    # immediately with a non-zero code, no timeout risk). Reverted file
    # confirmed byte-identical via `git status --porcelain`.
    {
        "id_suffix": "runtime_rtio_sqe_copy_in_offbyone",
        "category": "runtime_crash",
        "target_file": "include/zephyr/rtio/rtio.h",
        "operator": "runtime_off_by_one:if (acquirable < sqe_count) {:if (acquirable < sqe_count - 1) {",
        "target_app": "tests/subsys/rtio/rtio_api",
        "board": "native_sim",
        "extra_files": {
            "tests/subsys/rtio/rtio_api/src/test_rtio_api.c":
                os.path.join(os.path.dirname(__file__), "injection_assets", "rtio_sqe_copy_in_offbyone_test", "test_rtio_api.c"),
        },
    },
    # runtime_remove_null_check's second tests/subsys entry (session 46
    # continued), following up in the same tests/subsys/rtio directory
    # after the previous entry's runtime_off_by_one win in rtio.h.
    # `drivers/i2c/i2c_rtio.c`'s `i2c_rtio_copy()` builds one SQE per I2C
    # message in a loop: `sqe = rtio_sqe_acquire(r); if (sqe == NULL) {
    # rtio_sqe_drop_all(r); return NULL; }` — a real guard against a pool
    # exhausted mid-copy, rolling back every SQE already acquired in this
    # call and returning NULL cleanly so the caller can react
    # (`i2c_rtio_transfer()` treats a NULL return as `-ENOMEM`). No
    # existing test in `tests/subsys/rtio/rtio_i2c` ever calls
    # `i2c_rtio_copy()` (the 4-slot `test_rtio_ctx` pool, `RTIO_DEFINE(...,
    # 4, 4)`) with more messages than the pool can hold — every existing
    # call passes exactly 1 message.
    #
    # Added `test_i2c_rtio_copy_pool_exhausted` to
    # `tests/subsys/rtio/rtio_i2c/src/main.cpp`: calls `i2c_rtio_copy()`
    # with a 5-message array against the fresh (post-`rtio_i2c_before`)
    # 4-slot pool, asserting the call returns NULL. Disabled the guard
    # entirely (`if (sqe == NULL) {` -> `if (0) {`, landing on the first
    # occurrence in the file, inside `i2c_rtio_copy()` — the guard text
    # repeats verbatim in this driver's other five `i2c_rtio_copy_*`/
    # `i2c_rtio_*` functions, but this one is the file's first occurrence
    # so no `#N` anchor was needed).
    #
    # With the guard disabled, the 5th loop iteration's `sqe` stays NULL
    # after the pool is exhausted, but execution proceeds into
    # `rtio_sqe_prep_write()`, whose very first line is `memset(sqe, 0,
    # sizeof(struct rtio_sqe));` — a direct NULL-pointer write, a
    # genuinely more severe fault than a same-thread OOB write.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 9/9;
    # mutated build crashes with a genuine `Segmentation fault` (exit
    # 139) exactly at the new test, all 7 preceding tests unaffected.
    # Reverted file confirmed byte-identical via `git status
    # --porcelain`.
    {
        "id_suffix": "runtime_i2c_rtio_copy_pool_exhausted_nullcheck",
        "category": "runtime_crash",
        "target_file": "drivers/i2c/i2c_rtio.c",
        "operator": "runtime_remove_null_check:if (sqe == NULL) {:if (0) {",
        "target_app": "tests/subsys/rtio/rtio_i2c",
        "board": "native_sim",
        "extra_files": {
            "tests/subsys/rtio/rtio_i2c/src/main.cpp":
                os.path.join(os.path.dirname(__file__), "injection_assets", "i2c_rtio_copy_pool_exhausted_test", "main.cpp"),
        },
    },
    # runtime_remove_null_check's third tests/subsys entry (session 46
    # continued), the last of tests/subsys/rtio's 3 apps — 3 wins in a
    # row for this directory now. `subsys/rtio/rtio_workq.c`'s
    # `rtio_work_req_submit()` guards its `req` parameter: `if (!req) {
    # return; }`, before a second guard for `iodev_sqe`/`handler` and
    # then `req->iodev_sqe = iodev_sqe; req->handler = handler;`.
    # `tests/subsys/rtio/workq`'s own `test_used_count_keeps_track_of_alloc_items`
    # already exercises `rtio_work_req_alloc()` returning NULL once the
    # 4-item slab (`CONFIG_RTIO_WORKQ_POOL_ITEMS`, defaults to 4) is
    # exhausted — but never then calls `rtio_work_req_submit()` with that
    # NULL result, so the `!req` guard itself was never actually probed.
    #
    # Added `test_work_req_submit_rejects_null_req`: calls
    # `rtio_work_req_submit(NULL, &dummy_iodev_sqe, work_handler)` with a
    # real, non-NULL `iodev_sqe`/`handler` pair (a local
    # `struct rtio_iodev_sqe` — a complete, non-opaque type, confirmed via
    # `sqe.h`) so the *second* guard can't also catch it — isolating the
    # `!req` check specifically. Golden path: rejected immediately,
    # `rtio_work_req_used_count_get()` stays 0. Disabled the guard
    # entirely (`if (!req) {` -> `if (0) {`).
    #
    # With the guard disabled, the second guard's condition
    # (`!iodev_sqe || !handler`) is false (both are real, non-NULL), so
    # execution falls through to `req->iodev_sqe = iodev_sqe;` — a direct
    # NULL-pointer write, since `req` is still NULL.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 6/6;
    # mutated build crashes with a genuine `Segmentation fault` (exit
    # 139) exactly at the new test, the 2 preceding tests unaffected.
    # Reverted file confirmed byte-identical via `git status
    # --porcelain`.
    {
        "id_suffix": "runtime_rtio_workq_submit_null_req",
        "category": "runtime_crash",
        "target_file": "subsys/rtio/rtio_workq.c",
        "operator": "runtime_remove_null_check:if (!req) {:if (0) {",
        "target_app": "tests/subsys/rtio/workq",
        "board": "native_sim",
        "extra_files": {
            "tests/subsys/rtio/workq/src/main.c":
                os.path.join(os.path.dirname(__file__), "injection_assets", "rtio_workq_null_req_test", "main.c"),
        },
    },
    # c_api_substitute's second tests/subsys entry (session 46
    # continued), following user's explicit request to find a
    # tests/subsys target for this operator specifically — the most
    # lagging operator (11/35), to advance both it and tests/subsys at
    # once. Searched the same "k_sleep + explicit priority constant" and
    # "give/allow/chance ... run" comment patterns from session 30, this
    # time turning up `tests/subsys/modem/modem_ppp` (a sibling test app
    # in the same subsys/modem family as session 30's `modem_ubx` win) —
    # not found by session 30's own search since it needed the broader
    # comment-based variant.
    #
    # `tests/subsys/modem/modem_ppp/src/main.c`'s shared helper
    # `put_and_validate_wrapped_frame()` (called by 2 tests) has `/* Give
    # modem ppp time to process received frame */ k_msleep(1000);` before
    # asserting a frame was received. Checked the actual processing
    # mechanism before assuming: `modem_ppp.c` submits work via
    # `modem_work_submit()`, which (this test's `prj.conf` doesn't set
    # `CONFIG_MODEM_DEDICATED_WORKQUEUE`) falls back to the *system*
    # workqueue — both `CONFIG_SYSTEM_WORKQUEUE_PRIORITY` and
    # `CONFIG_ZTEST_THREAD_PRIORITY` default to the same value (-1,
    # cooperative), so per session 8's "yield still schedules same-priority
    # peers" lesson, a single `k_yield()` looked like it *should* be
    # enough to let the workqueue thread run — reasoning alone couldn't
    # settle whether the frame-processing itself needs actual elapsed
    # time beyond one scheduling opportunity (the same ambiguity
    # `modem_ubx`'s own source comment flagged, "may rely on multiple
    # thread interactions which may not be served by simply yielding"),
    # so tested empirically per session 10's standing rule rather than
    # reasoning it away.
    #
    # Substituting `k_msleep(1000);` -> `k_yield();` (scoped to
    # `put_and_validate_wrapped_frame`, since the literal `k_msleep(1000);`
    # text repeats 8 times across the file) confirmed the real-time
    # requirement empirically: both callers of the shared helper fail
    # cleanly (`test_ppp_frame_receive`/`test_ppp_no_connect_received`,
    # `zassert_true(received_packets_len == 1)` false — the workqueue
    # thread never got scheduled/completed processing in time), 10 of 12
    # tests in the suite unaffected, no crash/hang. Reverted file
    # confirmed byte-identical via `git status --porcelain`. No
    # `extra_files` needed — a pure mutation of an already-existing,
    # already-correct test, same as the very first `c_api_substitute`
    # entries.
    {
        "id_suffix": "c_api_substitute_modem_ppp",
        "category": "runtime_crash",
        "target_file": "tests/subsys/modem/modem_ppp/src/main.c",
        "operator": "c_api_substitute:put_and_validate_wrapped_frame:k_msleep(1000);:k_yield();",
        "target_app": "tests/subsys/modem/modem_ppp",
        "board": "native_sim",
    },
    # runtime_off_by_one's fifth tests/subsys entry (session 46
    # continued), the first target in `tests/subsys/nvmem` — a brand-new
    # subsystem (`@since 4.3`) that turned out to need the fullest
    # `extra_files` staging combination yet: this test app has **no**
    # default/bare `west build`-reachable scenario at all — all 4 of its
    # `tests.yaml` scenarios (`bbram`/`eeprom`/`flash`/`otp`) require both
    # `extra_configs` *and* `extra_dtc_overlay_files` to select a backend,
    # since `common.dtsi`'s `&test_nvmem0` label is only ever defined by
    # one of the 4 backend-specific `.overlay` files, selected only via a
    # twister-scenario CMake arg this pipeline's bare `west build` never
    # passes.
    #
    # Worked around by staging a `boards/native_sim.overlay` (the
    # `flash.overlay`+`common.dtsi` combination inlined into one file,
    # placed at the conventional board-overlay path Zephyr's build system
    # auto-applies for *any* scenario, no CMake flag needed — same
    # `boards/<board>.overlay` mechanism as the `gpio_mmio_latch`
    # riscv32 port and the i2c_emul/rtc_emul entries) alongside a
    # replacement `prj.conf` adding `CONFIG_FLASH=y`/
    # `CONFIG_NVMEM_FLASH_WRITE=y` (mirroring the `nvmem.api.flash`
    # scenario's own `extra_configs`). Confirmed empirically this makes
    # the previously entirely-unreachable subsystem buildable at all.
    #
    # `subsys/nvmem/nvmem.c`'s `nvmem_cell_read()`/`nvmem_cell_write()`
    # both guard `if (off < 0 || cell->size < off + len) { return
    # -EINVAL; }` before dispatching to whichever backend device API is
    # enabled — the fundamental boundary check for the whole abstraction
    # layer. `tests/subsys/nvmem/api`'s own `test_nvmem_api` only ever
    # reads/writes exactly `cell0.size` bytes (an exact fit, no tight
    # off-by-one probe).
    #
    # Added `test_nvmem_cell_bounds_offbyone`: calls both
    # `nvmem_cell_write`/`nvmem_cell_read` with `len = cell0.size + 1`
    # (using a real 32-byte buffer, well larger than the 17 bytes
    # actually needed, so the test's own code has no incidental OOB —
    # only the guard's own correctness is under test), asserting
    # `-EINVAL` both times. Loosened the guard by that same 1-byte unit
    # (`cell->size < off + len` -> `cell->size < off + len - 1`) —
    # anchored mode matches only the first occurrence in the file
    # (inside `nvmem_cell_read`), leaving `nvmem_cell_write`'s identical
    # guard untouched, so only the *read* half of the new test's two
    # assertions is actually affected — confirmed this produces a clean,
    # single-assertion failure rather than assuming both would trip.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 3/3;
    # mutated build fails cleanly and only at the new test's read
    # assertion (`ret not equal to -EINVAL ... got 0`, 2/3 pass,
    # `PROJECT EXECUTION FAILED`), the other 2 tests unaffected.
    # Reverted file confirmed byte-identical via `git status
    # --porcelain`, and — per the extra caution learned from this same
    # session's `modem_chat` pipeline-only flake — also rebuilt and
    # re-ran the reverted state directly (not just diffed) before
    # trusting it: clean 3/3 pass.
    {
        "id_suffix": "runtime_nvmem_cell_bounds_offbyone",
        "category": "runtime_crash",
        "target_file": "subsys/nvmem/nvmem.c",
        "operator": "runtime_off_by_one:if (off < 0 || cell->size < off + len) {:if (off < 0 || cell->size < off + len - 1) {",
        "target_app": "tests/subsys/nvmem/api",
        "board": "native_sim",
        "extra_files": {
            "tests/subsys/nvmem/api/src/main.c":
                os.path.join(os.path.dirname(__file__), "injection_assets", "nvmem_cell_bounds_offbyone_test", "main.c"),
            "tests/subsys/nvmem/api/boards/native_sim.overlay":
                os.path.join(os.path.dirname(__file__), "injection_assets", "nvmem_cell_bounds_offbyone_test", "native_sim.overlay"),
            "tests/subsys/nvmem/api/prj.conf":
                os.path.join(os.path.dirname(__file__), "injection_assets", "nvmem_cell_bounds_offbyone_test", "prj.conf"),
        },
    },
    # c_api_substitute's third tests/subsys entry (session 46 continued),
    # this time in tests/subsys/modem/modem_pipe rather than the
    # subsys/modem/{modem_ubx,modem_ppp,modem_chat} family already mined —
    # modem_cmux was tried first but rejected: two separate mutation
    # attempts there (test_modem_cmux_receive_dlci2_at and
    # test_modem_cmux_receive_dlci1_at) both produced a clean failure at
    # the targeted test *plus* a second, unrelated test failing 1-2 tests
    # later — the shared cmux/dlci mock pipes have no synchronization to
    # drain a mutation's now-late-arriving async receive_work before the
    # next test's `before` hook runs, so leftover data bleeds across test
    # boundaries. That's cascading corruption, not a direct, attributable
    # fault — a new dead-end shape distinct from the ones already
    # documented, so no case was taken from modem_cmux; moved on rather
    # than forcing it.
    #
    # `tests/subsys/modem/modem_pipe/src/main.c`'s shared helper
    # `test_pipe_async_transmit()` (called by 2 ZTEST cases,
    # `test_async_transmit`/`test_receive_closed`) does
    # `modem_pipe_transmit(...)` then `k_sleep(TEST_MODEM_PIPE_WAIT_TIMEOUT)`
    # (20ms) before asserting the TRANSMIT_IDLE event bit is set. Checked
    # the actual completion mechanism before assuming: the fake backend's
    # `modem_backend_fake_transmit()` doesn't notify synchronously — it
    # calls `k_work_schedule(&backend->transmit_idle_dwork,
    # TEST_MODEM_PIPE_NOTIFY_TIMEOUT)` (a real 10ms *timed* delay, not
    # `K_NO_WAIT`), so the notification genuinely cannot fire before 10ms
    # of simulated time elapse regardless of thread priority — an even
    # more clear-cut "real elapsed time required" case than the
    # scheduling-priority idiom this operator usually targets, so a bare
    # `k_yield()` (returns almost immediately) is certain to run the
    # assertion before the timer-backed work item is even eligible to run.
    #
    # Cross-test isolation checked before trusting the result: this suite's
    # `after` hook (`modem_backend_fake_after`) does a *blocking*
    # `modem_pipe_close(test_pipe, K_SECONDS(10))` every test, which drains
    # any pending delayed work before the next test's `before` hook runs —
    # the synchronization modem_cmux's suite lacked. Confirmed empirically:
    # mutated run fails cleanly at exactly the 2 direct callers, the other
    # 4 tests (including ones adjacent in file order) unaffected, 2/2 runs
    # identical — no cascade repeat of the modem_cmux pattern.
    #
    # Substituting `k_sleep(TEST_MODEM_PIPE_WAIT_TIMEOUT);` -> `k_yield();`
    # (scoped to the `test_pipe_async_transmit` helper function itself, via
    # `_find_ztest_block`'s plain-C-function support, same mechanism
    # `modem_ppp`'s entry used) confirmed both callers fail cleanly at the
    # same assertion (`atomic_get(&test_state) not equal to
    # BIT(TEST_MODEM_PIPE_EVENT_TRANSMIT_IDLE_BIT)`), the other 4 tests
    # (`test_async_open_close`/`test_attach`/`test_sync_open_close`/
    # `test_sync_transmit`) unaffected.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 6/6;
    # mutated build fails cleanly at exactly the 2 expected tests (4/6
    # pass), reproduced identically on a second run. Reverted file
    # confirmed byte-identical via `git status --porcelain` *and*
    # re-verified via a fresh rebuild+run (6/6 clean) before trusting it,
    # per this same session's `modem_chat`/`nvmem` extra-caution practice.
    # Passed the full `verify_cases.py` two-sided gate on the first
    # attempt via a pilot JSON. No `extra_files` needed — a pure mutation
    # of an already-existing, already-correct test.
    {
        "id_suffix": "c_api_substitute_modem_pipe",
        "category": "runtime_crash",
        "target_file": "tests/subsys/modem/modem_pipe/src/main.c",
        "operator": "c_api_substitute:test_pipe_async_transmit:k_sleep(TEST_MODEM_PIPE_WAIT_TIMEOUT);:k_yield();",
        "target_app": "tests/subsys/modem/modem_pipe",
        "board": "native_sim",
    },
    # c_api_substitute's fourth tests/subsys entry (session 46 continued),
    # in tests/subsys/modem/backends/tty — the candidate flagged at the
    # end of the previous round but not yet mutated/verified.
    #
    # `tests/subsys/modem/backends/tty/src/main.c`'s `test_receive_ready_event_raised`
    # writes a real message to the primary side of a host PTY
    # (`write(primary_fd, ...)`), then does `k_sleep(TEST_MODEM_BACKEND_TTY_OP_DELAY)`
    # (1000ms) before asserting the RRDY event bit was set. The tty backend
    # runs a dedicated thread that blocks in a real POSIX `read()` on the
    # secondary side of the same PTY and calls
    # `modem_pipe_notify_receive_ready()` once data arrives — genuine host
    # I/O latency, not a scheduling-priority question, so a bare
    # `k_yield()` (returns almost immediately) can't reliably wait for the
    # real read() to unblock and the notification to propagate.
    #
    # Cross-test isolation checked before mutating, directly applying the
    # `modem_cmux` cascading-corruption lesson from earlier this session:
    # this suite's `before` hook only resets the atomic event bits (no
    # pipe re-open/drain), and its `teardown` runs once at suite end, not
    # per test — closer to `modem_cmux`'s shape than `modem_pipe`'s. But
    # unlike `modem_cmux`, the very next test (`test_transmit`) never
    # reads from the tty pipe or checks the RRDY bit at all (only the
    # TIDLE bit, driven by a separate transmit path) — so even if the
    # backend thread's real read() eventually completes late, mid-way
    # through the next test, there's no observable to corrupt. Confirmed
    # empirically rather than assumed: 2/2 mutated runs failed cleanly at
    # exactly the targeted test, all 4 others (including the immediately
    # following `test_transmit`) unaffected both times.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 5/5;
    # mutated build fails cleanly and only at the new assertion
    # (`result == true is false`, "Receive ready evennt not set"), 4/5
    # pass, `PROJECT EXECUTION FAILED`. Reverted file confirmed
    # byte-identical via `git status --porcelain` *and* re-verified via a
    # fresh rebuild+run (5/5 clean) before trusting it. Passed the full
    # `verify_cases.py` two-sided gate on the first attempt via a pilot
    # JSON. No `extra_files` needed.
    {
        "id_suffix": "c_api_substitute_backends_tty",
        "category": "runtime_crash",
        "target_file": "tests/subsys/modem/backends/tty/src/main.c",
        "operator": "c_api_substitute:test_receive_ready_event_raised:k_sleep(TEST_MODEM_BACKEND_TTY_OP_DELAY);:k_yield();",
        "target_app": "tests/subsys/modem/backends/tty",
        "board": "native_sim",
    },
    # c_api_substitute's fifth tests/subsys entry (session 46 continued),
    # the first hit outside the subsys/modem family — a deliberate pivot
    # after subsys/modem was fully exhausted (part 14) — found by scanning
    # a batch of never-touched tests/subsys subdirectories
    # (bindesc/canbus/cpu_freq/crc/dsp/edac/input/ipc/jwt/kvss/llext/
    # lorawan/mgmt/modbus/openthread/pmci/random/sd/secure_storage/
    # settings_commit_prio/sip_svc/testsuite/tracing/usb) for the same
    # k_sleep/k_msleep idiom, filtered down first to native_sim-buildable,
    # ztest-harness apps before reading any source.
    #
    # `tests/subsys/mgmt/mcumgr/smp_client/src/main.c`'s
    # `test_msg_send_timeout` sends an SMP command with a client-side
    # timeout of `2` (units), then does `k_sleep(K_SECONDS(3))` before
    # asserting `response_ptr` was set to `&testing_user_data` by the
    # timeout callback — the timeout notification is delivered by the SMP
    # client's own internal timer, not scheduling priority, so it
    # genuinely needs several seconds of real elapsed time to fire, not
    # just a scheduling opportunity.
    #
    # Cross-test isolation checked before mutating, directly applying the
    # `modem_cmux`/`modem_cmux_pair` lessons from parts 12/14: this suite
    # has no per-test `after` hook to drain a leftover pending timer, so
    # in principle a late-firing internal timeout could corrupt a
    # subsequent test's `res_buf`/`response_ptr` state. Checked ztest's
    # actual run order empirically (not file declaration order — this
    # suite has no `CONFIG_ZTEST_SHUFFLE`, so order is deterministic but
    # not source-order): confirmed via the golden run that
    # `test_msg_send_timeout` executes *last* of the suite's 3 tests, so
    # there is no later test for a leftover timer to bleed into — zero
    # cascade risk despite the shared lack of a draining `after` hook.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 3/3;
    # mutated build fails cleanly and only at the new assertion
    # (`response_ptr not equal to &testing_user_data`), the other 2 tests
    # (which run *before* it) unaffected, reproduced identically on a
    # second run. Reverted file confirmed byte-identical via `git status
    # --porcelain` *and* re-verified via a fresh rebuild+run (3/3 clean).
    # Passed the full `verify_cases.py` two-sided gate on the first
    # attempt via a pilot JSON. No `extra_files` needed.
    {
        "id_suffix": "c_api_substitute_smp_client",
        "category": "runtime_crash",
        "target_file": "tests/subsys/mgmt/mcumgr/smp_client/src/main.c",
        "operator": "c_api_substitute:test_msg_send_timeout:k_sleep(K_SECONDS(3));:k_yield();",
        "target_app": "tests/subsys/mgmt/mcumgr/smp_client",
        "board": "native_sim",
    },
    # c_api_substitute's sixth tests/subsys entry (session 46 continued),
    # found in the same fresh-subdirectory batch as the smp_client entry
    # above, from `tests/subsys/input/longpress`.
    #
    # `tests/subsys/input/longpress/src/main.c` has exactly one ZTEST
    # (`test_longpress_test`, the suite's only test, no before/after
    # hooks) that simulates holding a key down by calling
    # `k_sleep(K_MSEC(150))` between `input_report_key(..., 1, ...)` and
    # `input_report_key(..., 0, ...)`. Checked the underlying driver
    # before assuming this was a scheduling-priority idiom:
    # `subsys/input/input_longpress.c` arms a real `k_work_delayable`
    # timer (`k_work_schedule(&entry->work, K_MSEC(cfg->long_delays_ms))`)
    # when the key goes down, and only fires the "long press" input event
    # from that delayed work callback — so the 150ms sleep is standing in
    # for actual physical hold-time the driver's own timer measures, not
    # just a scheduling opportunity for another thread. Zero cross-test
    # cascade risk since this is the suite's *only* test — nothing else
    # to corrupt even in principle.
    #
    # Substituting the first of the two identical
    # `k_sleep(K_MSEC(150));` occurrences (scoped to `test_longpress_test`,
    # landing on the first "long press" case) confirmed the real-time
    # requirement empirically: with the timer never given a chance to
    # actually elapse, the driver never emits the long-press event, so
    # the very next assertion checking for it fails cleanly.
    #
    # Verified via a direct ./zephyr/zephyr.exe run: golden passes 1/1;
    # mutated build fails cleanly at the expected assertion
    # (`last_events[1].code not equal to INPUT_KEY_X`), reproduced
    # identically on a second run. Reverted file confirmed byte-identical
    # via `git status --porcelain` *and* re-verified via a fresh
    # rebuild+run (1/1 clean). Passed the full `verify_cases.py`
    # two-sided gate on the first attempt via a pilot JSON. No
    # `extra_files` needed.
    {
        "id_suffix": "c_api_substitute_longpress",
        "category": "runtime_crash",
        "target_file": "tests/subsys/input/longpress/src/main.c",
        "operator": "c_api_substitute:test_longpress_test:k_sleep(K_MSEC(150));:k_yield();",
        "target_app": "tests/subsys/input/longpress",
        "board": "native_sim",
    },
    # --- Compound / Cross-Artifact Faults (new 5th category, session 46 part 20) ---
    # 前 80 筆案例全部是「單一檔案」破壞：repair agent 只需要看 build log
    # 指到的那個檔案改，不用理解 Kconfig -> DTS -> C 的相依圖。這是一個獨立
    # 的新類別，模擬「單一根因橫跨多個 artifact 才會顯現」的錯誤 —— 第一批
    # (這個) 是編譯期就會炸的組合：Kconfig 開了但對應的 DTS 節點被拔掉。
    #
    # 誠實記錄一個方法論上的取捨：這次 pilot 刻意選擇「架構上橫跨兩個檔案、
    # 共同構成一個連貫的 bug 敘事」作為 compound 的定義，而非「單獨套用任一
    # 邊都絕對不會失敗」的嚴格雙邊必要性。手動 recon 階段實測過，這個案例的
    # DTS 側 mutation 單獨套用 (不動 Kconfig) 也足以讓建置失敗 —— 因為
    # `tests/drivers/adc/adc_emul/src/main.c` 對 `DEVICE_DT_GET(DT_INST(0,
    # zephyr_adc_emul))` 沒有 `#ifdef CONFIG_ADC_EMUL` 保護，是無條件呼叫。
    # 這其實是 Zephyr 這類「Kconfig 靠 `depends on DT_HAS_X_ENABLED` 自動
    # select」樣板的結構性限制：若消費端 C 程式碼無條件引用該裝置，DTS 側
    # 單獨就足以致命；若消費端有 `#ifdef` 保護，系統會自我修復 (Kconfig 自動
    # 關閉，整段程式碼不編譯)，要在這種情況下仍然逼它失敗，需要一個能「移除
    # /削弱 depends on」而非只是「反轉」的新 operator，超出這次「先讓機制
    # 跑通」的範圍。嚴格的雙邊必要性留給第二種 compound 子類型 (compatible
    # 綁錯 instance，天生就是雙邊必要，建置完全過、不 crash)。
    # Kconfig 側跟 DTS 側鎖定的是同一個「ADC_EMUL 啟用關係」的兩個面向 ——
    # 一個從「這個 config 的依賴閘門」的角度破壞，一個從「這個閘門依據的
    # devicetree 事實」的角度破壞 —— 是一個連貫的單一根因敘事，只是不滿足
    # 嚴格雙邊必要性。
    #
    # The first 80 cases are all single-file breakage: a repair agent only
    # needs to look at whatever file the build log points to, no need to
    # understand the Kconfig -> DTS -> C dependency graph. This is a new,
    # independent category simulating faults where a single root cause only
    # manifests across multiple artifacts — this first entry is the
    # build-time-failing combination: a Kconfig symbol enabled but the DTS
    # node it depends on removed.
    #
    # Honestly recording a methodological tradeoff: this pilot deliberately
    # defines "compound" as "architecturally spans two files, forming one
    # coherent bug narrative" rather than strict bilateral necessity
    # ("applying either mutation alone never fails"). Manual recon confirmed
    # the DTS-side mutation alone (leaving Kconfig untouched) is already
    # sufficient to fail the build here, because
    # `tests/drivers/adc/adc_emul/src/main.c` calls `DEVICE_DT_GET(DT_INST(0,
    # zephyr_adc_emul))` unconditionally, with no `#ifdef CONFIG_ADC_EMUL`
    # guard. This is a structural limitation of Zephyr's common "Kconfig
    # auto-selects via `depends on DT_HAS_X_ENABLED`" idiom: if the consuming
    # C code references the device unconditionally, the DTS side alone is
    # already fatal; if the consumer is `#ifdef`-guarded, the system
    # self-heals (Kconfig auto-disables, the whole guarded block never
    # compiles) — forcing a failure in that case would need a new operator
    # that removes/weakens a `depends on` line rather than merely inverting
    # it, which is out of scope for this "get the mechanism working" pass.
    # Strict bilateral necessity is deferred to the second compound subtype
    # (compatible bound to the wrong instance — inherently bilateral, builds
    # clean, doesn't crash). The Kconfig and DTS mutations here target two
    # faces of the same "ADC_EMUL enablement relationship" — one breaks the
    # config's dependency gate, the other breaks the devicetree fact that
    # gate is conditioned on — one coherent single-root-cause narrative, just
    # not strictly bilaterally necessary.
    #
    # Verified: mutate side failed at CMake/compile stage with a genuine
    # compiler error (`__device_dts_ord_DT_N_INST_0_zephyr_adc_emul_ORD`
    # undeclared, `DT_N_INST_0_zephyr_adc_emul_P_ref_internal_mv` undeclared)
    # — status `eof_no_boot`, matching the compound category's expected
    # failure set. Revert side rebuilt and passed cleanly. Both mutation
    # operators individually spot-checked locally (non-Docker) for a clean
    # apply/revert round-trip before the real two-sided gate ran. Passed
    # `verify_cases.py`'s full gate on the first attempt via a pilot JSON.
    {
        "id_suffix": "compound_adc_emul_kconfig_dts",
        "category": "compound",
        "target_app": "tests/drivers/adc/adc_emul",
        "board": "native_sim",
        "injections": [
            {
                "target_file": "drivers/adc/Kconfig.adc_emul",
                "operator": "kconfig_invert_depends:ADC_EMUL",
            },
            {
                "target_file": "boards/native/native_sim/native_sim.dts",
                "operator": "dts_remove_compatible:zephyr,adc-emul",
            },
        ],
    },
    # --- Compound subtype 2: silent wrong-instance binding (session 46 part 21) ---
    # subtype 1 (above) is a build-time failure — low risk, reuses the
    # existing kconfig/dts verification shape. This is the harder, higher-
    # value half: a mutation that builds *and* boots *and* runs the full
    # ztest suite completely cleanly, where only one specific test assertion
    # catches that the wrong underlying instance/config got bound. Unlike
    # subtype 1, this one is genuinely, strictly bilaterally necessary by
    # construction — there's no way to observe the bug except through the
    # one assertion that happens to depend on the exact value that changed.
    #
    # Needed a new operator to express this: `dts_redirect_phandle`
    # (tools/mutate_inject.py) — unlike the existing `dts_break_phandle`
    # (which points a phandle at a *nonexistent* label, always a build-time
    # failure), this points it at a *different, already-existing, valid*
    # node — the phandle equivalent of "compatible string swapped to another
    # legal string, so DEVICE_DT_GET quietly resolves the wrong instance"
    # from the original proposal. Chose phandle-redirection over literally
    # mutating a `compatible` string after research showed the latter
    # doesn't actually work for this bug shape in practice: every
    # multi-instance test found either resolves devices by node *label*
    # (DT_NODELABEL — immune to a compatible-string change entirely, since
    # labels aren't tied to compatible) or, in the one found DT_INST-ordinal
    # case (`tests/subsys/pm/power_mgmt`, 5 identically-compatible
    # `test-device-pm` nodes each bound to a distinct C-side pm_action
    # callback by ordinal), removing any one node's compatible shrinks the
    # instance count and breaks the *explicitly* ordinal-indexed
    # `DT_INST(4, test_device_pm)` reference at compile time — collapsing
    # back into subtype 1's build-failure shape, not a silent one.
    # Phandle-redirection sidesteps this cleanly: the node count and every
    # ordinal stay untouched, only *which* already-valid node a specific
    # reference resolves to changes.
    #
    # Target: `tests/drivers/pinctrl/api/app.overlay`'s `zephyr,user` node
    # has `test_device0_alt_default = <&test_device0_alt_default>;`, a
    # phandle the driver-agnostic test infra (`PINCTRL_DT_STATE_PINS_DEFINE(
    # DT_PATH(zephyr_user), test_device0_alt_default)` in
    # tests/drivers/pinctrl/api/src/main.c) reads to build an alternate
    # pinctrl state for `test_device0`. The target node
    # (`test_device0_alt_default`) has pin 2 with `bias-pull-down` and pin 3
    # with `bias-pull-up`; a sibling node in the same file
    # (`test_device0_alt_sleep`) is structurally just as valid (also a
    # single `group1` with a `pins` property) but has no bias properties at
    # all — same shape the DT binding expects, different actual
    # configuration. Redirecting the `zephyr,user` phandle from
    # `&test_device0_alt_default` to `&test_device0_alt_sleep` compiles and
    # links fine (both are legitimate pinctrl-state nodes) and boots fine —
    # only `ZTEST(pinctrl_api, test_update_states)`, which asserts the
    # resolved state's specific pull-bias values, catches it.
    #
    # Verified via the real two-sided gate (`verify_cases.py`, category
    # `compound` correctly required `wait_for_completion=True` so the
    # post-boot-banner assertion failure wasn't missed): mutate side booted
    # clean, ran the full `pinctrl_api` suite, and every test up through
    # `test_lookup_state` passed — only `test_update_states` failed, with
    # the raw log showing the *exact* predicted mismatch: `Assertion failed
    # ... TEST_GET_PULL(scfg->pins[0]) not equal to TEST_PULL_DOWN`. Status
    # `crash` (ztest's `PROJECT EXECUTION FAILED`/assertion-fail path, per
    # qemu_oracle.py's crash_patterns), `target_test` correctly
    # auto-extracted as `test_update_states`. Revert side rebuilt and
    # passed all 5 tests cleanly. The new operator was spot-checked locally
    # (non-Docker, on the real file copied out of the sandbox image) for a
    # clean apply/revert round-trip before the real gate ran. Passed
    # `verify_cases.py`'s full gate on the first attempt via a pilot JSON.
    {
        "id_suffix": "compound_pinctrl_alt_default_redirect",
        "category": "compound",
        "target_app": "tests/drivers/pinctrl/api",
        "board": "native_sim",
        "injections": [
            {
                "target_file": "tests/drivers/pinctrl/api/app.overlay",
                "operator": "dts_redirect_phandle:test_device0_alt_default:test_device0_alt_sleep",
            },
        ],
    },
    # --- Compound round 2 (session 46 part 22): a second target file for each subtype ---
    # Subtype 1 — same proven shape as `compound_adc_emul_kconfig_dts`
    # (Kconfig `depends on DT_HAS_ZEPHYR_X_ENABLED` inverted + the gated
    # node's compatible removed), applied to a completely fresh pair of
    # files: `drivers/dac/Kconfig.dac_emul`'s `config DAC_EMUL` and
    # `tests/drivers/dac/dac_emul/boards/native_sim.overlay`'s `dac_emul0`
    # node (one of 3 sibling DAC-emulator instances in that overlay, all
    # sharing `compatible = "zephyr,dac-emul"` — targeted the first
    # occurrence specifically, which is `dac_emul0`; the driver-agnostic
    # test's own `dac_emul_setup()` asserts `device_is_ready()` on all 3
    # instances unconditionally, so losing just one still fails the whole
    # build). Verified: mutate side genuinely never reaches a boot signature
    # (`ninja: build stopped: subcommand failed`, status `eof_no_boot`,
    # matching the compound category's expected set); revert side rebuilt
    # and passed cleanly.
    #
    # Subtype 2 — a second `dts_redirect_phandle` target, on
    # `tests/subsys/pm/policy_api/app.overlay`. Its `zephyr,user` node has
    # `test-states = <&state0 &state2>;`, feeding
    # `PM_STATE_CONSTRAINTS_GET(DT_PATH(zephyr_user), test_states)` in
    # `tests/subsys/pm/policy_api/src/main.c`; `ZTEST(policy_api,
    # test_pm_policy_state_constraints)` asserts the resulting constraint
    # list contains both a runtime-idle/substate-1 state (from `state0`) and
    # a suspend-to-ram/substate-100 state (from `state2`) — a third sibling
    # node in the same file, `state1` (suspend-to-ram/substate-10), is
    # structurally just as valid a `zephyr,power-state` node but numerically
    # distinct on every property. Redirected `state0` -> `state1`: compiles,
    # links, and boots completely clean, and 4 of the suite's 5 tests
    # (including the earlier `test_pm_policy_next_state_*` tests, which
    # don't touch `test-states` at all) pass outright; only
    # `test_pm_policy_state_constraints`'s `found_runtime_idle` check fails,
    # exactly as predicted — the raw log even printed the substituted
    # constraint's actual values (`Constraint 0: state=4, substate_id=10` —
    # `state1`'s substate id, not `state0`'s `1`) confirming the mechanism
    # directly rather than just the pass/fail outcome. Status `crash`,
    # `target_test` auto-extracted as `test_pm_policy_state_constraints`.
    # Revert side rebuilt and passed all 5 tests cleanly. Both mutations
    # spot-checked locally (non-Docker) before the real gate ran; both
    # passed `verify_cases.py`'s full gate on the first attempt via a
    # shared pilot JSON.
    {
        "id_suffix": "compound_dac_emul_kconfig_dts",
        "category": "compound",
        "target_app": "tests/drivers/dac/dac_emul",
        "board": "native_sim",
        "injections": [
            {
                "target_file": "drivers/dac/Kconfig.dac_emul",
                "operator": "kconfig_invert_depends:DAC_EMUL",
            },
            {
                "target_file": "tests/drivers/dac/dac_emul/boards/native_sim.overlay",
                "operator": "dts_remove_compatible:zephyr,dac-emul",
            },
        ],
    },
    {
        "id_suffix": "compound_pm_policy_state_redirect",
        "category": "compound",
        "target_app": "tests/subsys/pm/policy_api",
        "board": "native_sim",
        "injections": [
            {
                "target_file": "tests/subsys/pm/policy_api/app.overlay",
                "operator": "dts_redirect_phandle:state0:state1",
            },
        ],
    },
    # --- Compound round 3 (session 46 part 23): subtype 2's 3rd target file ---
    # `tests/subsys/pm/power_states_api/boards/native_sim.overlay` has 4
    # sibling `zephyr,power-state` nodes (`state0`..`state3`, distinct
    # residency/latency numbers each) referenced from *two* different
    # places: the CPU's own `cpu-power-states = <&state0 &state1 &state2
    # &state3>;` (all four, in order) and, separately, `test_dev`'s
    # `zephyr,disabling-power-states = <&state1 &state2>;` (the specific
    # states this device's activity should suppress). Both properties
    # literally contain the substring `&state2`, and since
    # `dts_redirect_phandle`'s hint-less `.search()` behavior grabs the
    # *first* occurrence in the whole file, a first attempt at this exact
    # mutation (`state2:state0`, no occurrence index) landed on the
    # `cpu-power-states` list instead of the intended
    # `disabling-power-states` property — a genuinely different, less
    # predictable mutation than planned, caught before ever running the
    # real gate by reading the local (non-Docker) mutation diff, not
    # assumed. This is exactly the class of ambiguity `#N` occurrence
    # suffixes exist to resolve elsewhere in mutate_inject.py (see
    # `_parse_occurrence_suffix`), so `dts_redirect_phandle` gained the same
    # `old_label[#N]:new_label` support (previously hint-only, no
    # occurrence index) to fix it — used here as `state2#2:state0` to
    # target the 2nd `&state2` occurrence specifically.
    #
    # `ZTEST(power_states_1cpu, test_device_power_state_constraints)`
    # (`tests/subsys/pm/power_states_api/src/main.c`) keeps `test_dev` busy
    # via `test_driver_async_operation()`, sleeps 60ms, and asserts
    # `suspend_to_ram_count == 0` — the device's constraint is supposed to
    # keep the CPU out of the (50ms-residency) suspend-to-ram state while
    # busy. Redirecting the *disabling* list's `state2` (suspend-to-ram) to
    # `state0` (suspend-to-idle, already covered by neither list) silently
    # drops suspend-to-ram from what gets suppressed during that busy
    # window: build/link/boot all succeed completely clean, and the CPU
    # actually enters suspend-to-ram mid-test — the raw log confirms the
    # exact predicted mechanism directly (`Assertion failed ...
    # (suspend_to_ram_count == 0 is false)`), not just a pass/fail verdict.
    # `target_test` auto-extracted as `test_device_power_state_constraints`.
    # Revert side rebuilt and passed cleanly. Spot-checked locally
    # (non-Docker) before the real gate ran; passed `verify_cases.py`'s full
    # gate on the first attempt after the `#N` fix.
    {
        "id_suffix": "compound_power_states_api_disabling_redirect",
        "category": "compound",
        "target_app": "tests/subsys/pm/power_states_api",
        "board": "native_sim",
        "injections": [
            {
                "target_file": "tests/subsys/pm/power_states_api/boards/native_sim.overlay",
                "operator": "dts_redirect_phandle:state2#2:state0",
            },
        ],
    },
    # --- Compound round 4 (session 46 part 24): subtype 2's 4th target, plus
    # 2 abandoned subtype-1 attempts with a durable process lesson ---
    # Two subtype-1 candidates were tried and abandoned before this one
    # landed, for two different, genuinely non-obvious reasons - both worth
    # recording since neither was a mistake in the operators themselves:
    #
    # 1. `biometrics_emul` (drivers/biometrics/Kconfig.emul +
    #    tests/drivers/biometrics/biometrics_emul/app.overlay, same proven
    #    DT_HAS_ZEPHYR_*_ENABLED shape as ADC/DAC): passed local
    #    (non-Docker) mutation round-trip, but the real gate's revert side
    #    hit a runtime "Aborted" crash mid-suite *twice* in a row (2/2,
    #    different from the earlier "eof_no_boot"-style build failures).
    #    Manually reproducing the exact same command sequence once,
    #    independently, passed cleanly 12/12 with `git status --porcelain`
    #    confirming a byte-clean revert - meaning the files themselves were
    #    never the problem. Concluded this is intrinsic flakiness in this
    #    specific (2025-added, very new) driver's test under native_sim,
    #    not our injection pipeline; the real gate's own 2/2 failure is the
    #    authoritative signal (it reflects the actual oracle/pty conditions
    #    real repair-agent runs will see), so this target was abandoned
    #    rather than forced in.
    # 2. `uart_emul` (drivers/serial/Kconfig.emul +
    #    tests/drivers/uart/uart_emul/uart_emul.overlay, "EXPERIMENTAL"
    #    UART_EMUL driver): revert side failed at `eof_no_boot`, and a
    #    manual full reproduction of the revert sequence hit a genuine
    #    compiler error even with `git status --porcelain` confirming both
    #    mutated files were restored byte-identical. Root-caused by building
    #    the *unmutated* baseline directly at the pinned baseline_commit
    #    (`bc460feabe7038dc876782557e39be791d6c24e9`) with zero mutations
    #    applied at all - it failed with the *exact same* compiler error
    #    (`DT_N_NODELABEL_dummy_PARENT_ORD` undeclared, from
    #    `device.c`'s `EMUL_UART_NODE = DT_PARENT(DT_NODELABEL(dummy))`).
    #    This test genuinely does not build at this pinned commit,
    #    unrelated to any mutation - a baseline/commit incompatibility, not
    #    a pipeline bug. **New process lesson**: for any *new* target file
    #    going forward (not just DT_HAS_ZEPHYR_*_ENABLED/dts_redirect_phandle
    #    candidates specifically, this generalizes to any new target),
    #    worth a quick unmutated baseline build at the pinned commit before
    #    investing further, the same way `verify_cases.py`'s revert side
    #    already implicitly re-proves the baseline for every *accepted*
    #    case - this just does it *before* burning a mutate+revert cycle on
    #    a target that might not even build cleanly to begin with.
    #
    # Landed target — a 4th `dts_redirect_phandle` site, on
    # `tests/subsys/pm/power_domain/app.overlay`. `test_dev_a` and
    # `test_dev_b` both have `power-domains = <&test_domain>;`;
    # `ZTEST(power_domain_1cpu, test_power_domain_device_runtime)`
    # (`tests/subsys/pm/power_domain/src/main.c`) exercises the domain's
    # runtime-PM bookkeeping across both devices sharing that one domain
    # (get/put reference counting, notification counts, and each device's
    # resulting PM_DEVICE_STATE). Redirecting `test_dev_b`'s (the *second*
    # `&test_domain` occurrence, `#2`) `power-domains` phandle to
    # `&test_domain_balanced` — a separate, independently valid
    # power-domain node in the same file, normally only used by the
    # test's *other* suite (`test_power_domain_device_balanced`) — silently
    # moves `devb` out of the domain the runtime test expects it to share
    # with `deva`. Compiles, links, and boots completely clean; 3 of the
    # suite's 4 tests (`test_on_power_domain`,
    # `test_power_domain_add_remove_duplicate`,
    # `test_power_domain_device_balanced`) pass outright, and only
    # `test_power_domain_device_runtime` fails — specifically at the point
    # checking `devb`'s state drops to `PM_DEVICE_STATE_OFF` once the
    # (now-unrelated) `test_domain` fully suspends, which no longer happens
    # to `devb` since it isn't a member of that domain anymore. `crash`
    # status, `target_test` auto-extracted as
    # `test_power_domain_device_runtime`. Revert side rebuilt and passed
    # all 4 tests cleanly. Spot-checked locally (non-Docker) before the
    # real gate ran; passed on the first attempt.
    {
        "id_suffix": "compound_power_domain_devb_redirect",
        "category": "compound",
        "target_app": "tests/subsys/pm/power_domain",
        "board": "native_sim",
        "injections": [
            {
                "target_file": "tests/subsys/pm/power_domain/app.overlay",
                "operator": "dts_redirect_phandle:test_domain#2:test_domain_balanced",
            },
        ],
    },
    # --- Compound round 5 (session 46 part 24, resumed): subtype 1's 3rd
    # target, applying the "verify unmutated baseline first" lesson from
    # this same part's 2 earlier losses (biometrics_emul, uart_emul). ---
    # Same proven "Kconfig auto-select via depends on DT_HAS_ZEPHYR_*_ENABLED"
    # shape as ADC/DAC, this time on `drivers/espi/Kconfig.espi_emul`'s
    # `config ESPI_EMUL` (2020 copyright — deliberately picked an older,
    # established driver rather than a recent one, per the age-risk
    # heuristic this part's earlier losses motivated) +
    # `boards/native/native_sim/native_sim.dts`'s `espi0` node
    # (`compatible = "zephyr,espi-emul-controller"` — this file's 4th
    # compound/dts touch, alongside `dts_native_sim_phandle`,
    # `dts_native_sim_compatible`, and the part-20 `adc_emul` case, on a
    # different node each time). Confirmed the *unmutated* baseline builds
    # and passes cleanly (1/1) at the pinned `baseline_commit` before
    # investing further — this is now standing practice for any brand-new
    # target file, not just this one. Verified: mutate side never reaches a
    # boot signature (`ninja: build stopped: subcommand failed`, status
    # `eof_no_boot`); revert side rebuilt and passed cleanly. Both
    # mutations spot-checked locally (non-Docker) before the real gate ran;
    # passed on the first attempt.
    {
        "id_suffix": "compound_espi_emul_kconfig_dts",
        "category": "compound",
        "target_app": "tests/drivers/espi",
        "board": "native_sim",
        "injections": [
            {
                "target_file": "drivers/espi/Kconfig.espi_emul",
                "operator": "kconfig_invert_depends:ESPI_EMUL",
            },
            {
                "target_file": "boards/native/native_sim/native_sim.dts",
                "operator": "dts_remove_compatible:zephyr,espi-emul-controller",
            },
        ],
    },
    # --- Compound round 6 (session 46 part 25): subtype 1's 4th target ---
    # Same proven shape again, on `drivers/rtc/Kconfig.emul`'s `config
    # RTC_EMUL` (2022, another established driver per this session's
    # age-risk heuristic) + `boards/native/native_sim/native_sim.dts`'s
    # `rtc` node (`compatible = "zephyr,rtc-emul"` — this file's 5th
    # compound/dts touch, each on a different node:
    # `dts_native_sim_phandle`, `dts_native_sim_compatible`, the part-20
    # `adc_emul` case, the part-24 `espi_emul` case, now this). Unlike the
    # ADC/DAC/ESPI cases (all `DEVICE_DT_GET(DT_NODELABEL(...))` or
    # `DT_INST(...)`), this test resolves its device via `DT_ALIAS(rtc)` —
    # `native_sim.dts`'s `aliases { rtc = &rtc; };` — the alias mechanism
    # itself is untouched by either mutation, only the aliased node's own
    # compatible and gating Kconfig are hit, same failure shape either way.
    # Confirmed the unmutated baseline builds and passes cleanly (3/3
    # tests) at the pinned `baseline_commit` before investing further, per
    # the standing practice from part 24. Verified: mutate side never
    # reaches a boot signature (`ninja: build stopped: subcommand failed`,
    # `eof_no_boot`); revert side rebuilt and passed cleanly. Both
    # mutations spot-checked locally (non-Docker) before the real gate ran;
    # passed on the first attempt.
    {
        "id_suffix": "compound_rtc_emul_kconfig_dts",
        "category": "compound",
        "target_app": "tests/drivers/rtc/rtc_api",
        "board": "native_sim",
        "injections": [
            {
                "target_file": "drivers/rtc/Kconfig.emul",
                "operator": "kconfig_invert_depends:RTC_EMUL",
            },
            {
                "target_file": "boards/native/native_sim/native_sim.dts",
                "operator": "dts_remove_compatible:zephyr,rtc-emul",
            },
        ],
    },
    # --- Compound round 7 (session 46 part 25 continued): subtype 2's 5th
    # target, a richer real-world topology than the earlier 4 ---
    # `tests/subsys/usb/uac2/app.overlay` describes a full USB Audio Class
    # 2.0 descriptor graph (clock source -> input/output terminals ->
    # feature units, wired via `data-source`/`clock-source`/`assoc-terminal`
    # phandles) for a simulated USB headset. `in_feature_unit`'s
    # `data-source = <&mic_input>;` (the *2nd* `&mic_input` occurrence in
    # the file — `#2` — the 1st is `headphones_output`'s unrelated
    # `assoc-terminal`) feeds directly into the numeric `bSourceID` byte of
    # that unit's generated USB descriptor
    # (`tests/subsys/usb/uac2/src/uac2_desc.c`'s
    # `reference_ac_mic_feature_unit_descriptor[]`, byte 5:
    # `0x05, /* bSourceID = 5 (headset input) */`) — confirmed by reading
    # the actual byte array before mutating, not assumed. Redirecting it to
    # `&out_terminal` (a different, structurally valid terminal node on the
    # *playback* side of the same topology) compiles/links/boots
    # completely clean — the redirected phandle just becomes a different,
    # equally legal unit-ID reference — and `ZTEST(uac2_desc,
    # test_fs_hs_iface_and_ep_descriptors_not_shared)` still passes; only
    # `test_fs_uac2_descriptors` (which walks the full descriptor via a
    # sequence of `zassert_mem_equal` calls against per-block reference
    # byte arrays) fails, catching the topology change through its
    # generated descriptor bytes. `target_test` auto-extracted as
    # `test_fs_uac2_descriptors`. A richer, more realistic "wrong instance"
    # narrative than the earlier 4 (redirecting audio routing in a USB
    # descriptor graph, not a synthetic pinctrl/PM test fixture) — worth
    # remembering `zassert_mem_equal`-style byte-array-diffing test files
    # are a promising, underused source of subtype-2 candidates, found by
    # reading which literal byte in the reference array a phandle target's
    # numeric ID feeds into before committing to a mutation. Confirmed the
    # unmutated baseline builds and passes cleanly (4/4 tests) at the
    # pinned `baseline_commit` first, per the part-24 standing practice.
    # Verified via the real gate on the first attempt.
    {
        "id_suffix": "compound_uac2_mic_feature_unit_source_redirect",
        "category": "compound",
        "target_app": "tests/subsys/usb/uac2",
        "board": "native_sim",
        "injections": [
            {
                "target_file": "tests/subsys/usb/uac2/app.overlay",
                "operator": "dts_redirect_phandle:mic_input#2:out_terminal",
            },
        ],
    },
    # --- Compound round 8 (session 46 part 25 continued): subtype 1's 5th
    # target ---
    # Same proven shape once more, on `drivers/dma/Kconfig.emul`'s
    # `config DMA_EMUL` (2023, "[EXPERIMENTAL]"-tagged like `uart_emul` was
    # — but that case's failure was a baseline/commit incompatibility, not
    # the EXPERIMENTAL tag itself, confirmed by this round's own baseline
    # check passing clean) + `boards/native/native_sim/native_sim.dts`'s
    # `dma` node (`compatible = "zephyr,dma-emul"` — this file's 6th
    # compound/dts touch, each on a different node so far:
    # `dts_native_sim_phandle`, `dts_native_sim_compatible`, `adc_emul`,
    # `espi_emul`, `rtc_emul`, now this). `tests/drivers/dma/loop_transfer`
    # resolves the DMA controller via `DEVICE_DT_GET(DT_NODELABEL(dma_name))`
    # inside an X-macro test-generator (`dma_name` bound to the node label
    # `tst_dma0`, itself an alias for `&dma` declared right in the test's
    # own overlay: `tst_dma0: &dma {};`). Confirmed the unmutated baseline
    # builds and passes cleanly (2 pass, 1 skip — `suspend_resume` is
    # skipped, unrelated to this mutation, native_sim's DMA emulator
    # doesn't support suspend) at the pinned `baseline_commit` first, per
    # the part-24 standing practice. Verified: mutate side never reaches a
    # boot signature (`ninja: build stopped: subcommand failed`,
    # `eof_no_boot`); revert side rebuilt and passed cleanly (same 2
    # pass/1 skip as baseline). Both mutations spot-checked locally
    # (non-Docker) before the real gate ran; passed on the first attempt.
    {
        "id_suffix": "compound_dma_emul_kconfig_dts",
        "category": "compound",
        "target_app": "tests/drivers/dma/loop_transfer",
        "board": "native_sim",
        "injections": [
            {
                "target_file": "drivers/dma/Kconfig.emul",
                "operator": "kconfig_invert_depends:DMA_EMUL",
            },
            {
                "target_file": "boards/native/native_sim/native_sim.dts",
                "operator": "dts_remove_compatible:zephyr,dma-emul",
            },
        ],
    },
    # --- Session 46 part 26: pivot to balancing kconfig/dts/c_syntax
    # against runtime_crash's 68 (unaddressed since part 19) ---
    # Reused already-baseline-verified compound-round target apps
    # (rtc_api, dac_emul — both confirmed clean at the pinned commit
    # earlier this session) to skip redundant baseline checks; each entry
    # here is a genuinely new single-file mutation distinct from its
    # compound sibling (a different file, or the same Kconfig file but
    # standing alone rather than paired with a DTS mutation).
    {
        "id_suffix": "c_rtc_y2k_semicolon",
        "category": "c_syntax",
        "target_file": "tests/drivers/rtc/rtc_api/src/test_y2k.c",
        "operator": "c_remove_semicolon",
        "target_app": "tests/drivers/rtc/rtc_api",
        "board": "native_sim",
    },
    {
        "id_suffix": "kconfig_dac_emul_depends",
        "category": "kconfig",
        "target_file": "drivers/dac/Kconfig.dac_emul",
        "operator": "kconfig_invert_depends:DAC_EMUL",
        "target_app": "tests/drivers/dac/dac_emul",
        "board": "native_sim",
    },
    # Round 2 of the kconfig/dts/c_syntax balancing pass. All 3 reused
    # already-baseline-verified target apps from earlier compound rounds
    # (uac2, pinctrl/api, rtc_api) — different files/operators than their
    # compound siblings in each case.
    {
        "id_suffix": "c_uac2_desc_typo_macro",
        "category": "c_syntax",
        "target_file": "tests/subsys/usb/uac2/src/uac2_desc.c",
        "operator": "c_typo_macro",
        "target_app": "tests/subsys/usb/uac2",
        "board": "native_sim",
    },
    {
        "id_suffix": "dts_pinctrl_device0_reg_cellcount",
        "category": "dts",
        "target_file": "tests/drivers/pinctrl/api/app.overlay",
        "operator": "dts_corrupt_reg",
        "target_app": "tests/drivers/pinctrl/api",
        "board": "native_sim",
    },
    {
        "id_suffix": "kconfig_rtc_emul_depends",
        "category": "kconfig",
        "target_file": "drivers/rtc/Kconfig.emul",
        "operator": "kconfig_invert_depends:RTC_EMUL",
        "target_app": "tests/drivers/rtc/rtc_api",
        "board": "native_sim",
    },
    # Round 3 of the balancing pass.
    {
        "id_suffix": "dts_espi_host_compatible",
        "category": "dts",
        "target_file": "tests/drivers/espi/boards/native_sim.overlay",
        "operator": "dts_remove_compatible:zephyr,espi-emul-espi-host",
        "target_app": "tests/drivers/espi",
        "board": "native_sim",
    },
    {
        "id_suffix": "c_dma_loop_brace",
        "category": "c_syntax",
        "target_file": "tests/drivers/dma/loop_transfer/src/test_dma_loop.c",
        "operator": "c_remove_closing_brace",
        "target_app": "tests/drivers/dma/loop_transfer",
        "board": "native_sim",
    },
    {
        "id_suffix": "kconfig_espi_emul_depends",
        "category": "kconfig",
        "target_file": "drivers/espi/Kconfig.espi_emul",
        "operator": "kconfig_invert_depends:ESPI_EMUL",
        "target_app": "tests/drivers/espi",
        "board": "native_sim",
    },
    # Session 46 part 28: continuing the kconfig/dts/c_syntax balancing
    # pass, reusing already-baseline-verified compound-round target apps
    # (policy_api, power_domain, power_states_api — all confirmed clean at
    # the pinned commit during earlier compound work) to skip redundant
    # baseline checks. `dts_remove_compatible` here hits `power_domain`'s
    # generic "power-domain" node rather than a driver emulator's DT_INST
    # node like the earlier dts_remove_compatible cases — the failure
    # surfaces even earlier, at CMake's devicetree-configure stage
    # ("power-domain controller ... lacks binding"), before any C
    # compilation starts. A companion `kconfig_remove_select:
    # TEST_PROVIDE_PM_HOOKS` candidate on `policy_api/Kconfig` (removing
    # its `select HAS_PM`) was tried and rejected — the real gate came
    # back `status=success`: Kconfig just silently downgrades `PM` from
    # the `y` requested in prj.conf back to `n` on the unmet dependency
    # instead of aborting the build, and since `policy_api`'s ztest code
    # is `#ifdef CONFIG_PM_POLICY_DEFAULT`-guarded, the whole suite
    # compiles and boots fine without PM ever being on — the same
    # self-healing shape documented for compound subtype 1 in part 20,
    # just discovered via `kconfig_remove_select` instead of
    # `kconfig_invert_depends` this time. Not retried elsewhere this
    # round; recorded here so it isn't rediscovered from scratch.
    {
        "id_suffix": "dts_power_domain_test_domain_compatible",
        "category": "dts",
        "target_file": "tests/subsys/pm/power_domain/app.overlay",
        "operator": "dts_remove_compatible",
        "target_app": "tests/subsys/pm/power_domain",
        "board": "native_sim",
    },
    {
        "id_suffix": "c_power_states_test_driver_semicolon",
        "category": "c_syntax",
        "target_file": "tests/subsys/pm/power_states_api/src/test_driver.c",
        "operator": "c_remove_semicolon",
        "target_app": "tests/subsys/pm/power_states_api",
        "board": "native_sim",
    },
    # Session 46 part 29: round 5 of the kconfig/dts/c_syntax balancing
    # pass, continuing to reuse already-baseline-verified compound-round
    # target apps (adc_emul, dma/loop_transfer, policy_api, pinctrl/api).
    # ADC_EMUL/DMA_EMUL standalone depends-inversion mirror the proven
    # dac_emul/rtc_emul/espi_emul shape exactly (same driver family,
    # same DT_HAS_ZEPHYR_*_ENABLED gate). `dts_break_phandle` on
    # policy_api targets `cpu1`'s `cpu-power-states = <&state2>;` — a
    # different node/property than the compound sibling's
    # `dts_redirect_phandle` on `zephyr,user`'s `test-states`, and a loud
    # build-time failure (undefined node label) rather than the compound
    # case's silent one. `c_typo_macro` on pinctrl/api/src/main.c hits
    # the file's first `DT_NODELABEL(test_device0)` — same operator/shape
    # as the img_util/uac2 typo-macro cases, on a fresh file.
    {
        "id_suffix": "kconfig_adc_emul_depends",
        "category": "kconfig",
        "target_file": "drivers/adc/Kconfig.adc_emul",
        "operator": "kconfig_invert_depends:ADC_EMUL",
        "target_app": "tests/drivers/adc/adc_emul",
        "board": "native_sim",
    },
    {
        "id_suffix": "kconfig_dma_emul_depends",
        "category": "kconfig",
        "target_file": "drivers/dma/Kconfig.emul",
        "operator": "kconfig_invert_depends:DMA_EMUL",
        "target_app": "tests/drivers/dma/loop_transfer",
        "board": "native_sim",
    },
    {
        "id_suffix": "dts_policy_api_cpu1_phandle",
        "category": "dts",
        "target_file": "tests/subsys/pm/policy_api/app.overlay",
        "operator": "dts_break_phandle",
        "target_app": "tests/subsys/pm/policy_api",
        "board": "native_sim",
    },
    {
        "id_suffix": "c_pinctrl_main_typo_macro",
        "category": "c_syntax",
        "target_file": "tests/drivers/pinctrl/api/src/main.c",
        "operator": "c_typo_macro",
        "target_app": "tests/drivers/pinctrl/api",
        "board": "native_sim",
    },
    # Session 46 part 30: round 6 of the kconfig/dts/c_syntax balancing
    # pass. `dts` candidates in the remaining reuse pool (adc_emul,
    # dac_emul, dma/loop_transfer, rtc_api) turned out to be thin — their
    # own app-level overlays only tweak plain properties (no phandle/reg)
    # on a node the `compound` sibling already fully covers via
    # `dts_remove_compatible`, and that operator has no occurrence-index
    # support to retarget a sibling node sharing the same compatible
    # string (unlike `dts_redirect_phandle`'s `#N` suffix) — so this
    # round pivoted entirely to `c_syntax`, closing out every remaining
    # gap in the pool at once (espi, policy_api, adc_emul, power_domain,
    # dac_emul all lacked a standalone c_syntax entry going into this
    # round). All 5 reused already-baseline-verified target apps and
    # passed the real gate on the first attempt.
    {
        "id_suffix": "c_espi_acpi_brace",
        "category": "c_syntax",
        "target_file": "tests/drivers/espi/src/test_acpi.c",
        "operator": "c_remove_closing_brace",
        "target_app": "tests/drivers/espi",
        "board": "native_sim",
    },
    {
        "id_suffix": "c_policy_api_main_semicolon",
        "category": "c_syntax",
        "target_file": "tests/subsys/pm/policy_api/src/main.c",
        "operator": "c_remove_semicolon",
        "target_app": "tests/subsys/pm/policy_api",
        "board": "native_sim",
    },
    {
        "id_suffix": "c_adc_emul_main_semicolon",
        "category": "c_syntax",
        "target_file": "tests/drivers/adc/adc_emul/src/main.c",
        "operator": "c_remove_semicolon",
        "target_app": "tests/drivers/adc/adc_emul",
        "board": "native_sim",
    },
    {
        "id_suffix": "c_power_domain_main_typo_macro",
        "category": "c_syntax",
        "target_file": "tests/subsys/pm/power_domain/src/main.c",
        "operator": "c_typo_macro",
        "target_app": "tests/subsys/pm/power_domain",
        "board": "native_sim",
    },
    {
        "id_suffix": "c_dac_emul_main_typo_macro",
        "category": "c_syntax",
        "target_file": "tests/drivers/dac/dac_emul/src/main.c",
        "operator": "c_typo_macro",
        "target_app": "tests/drivers/dac/dac_emul",
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
            case = {
                "id": case_id,
                "category": entry["category"],
                "broken_commit": baseline_commit,
                "fixed_commit": baseline_commit,
                "target_app": entry["target_app"],
                "board": entry["board"],
            }
            if entry.get("extra_files"):
                case["extra_files"] = entry["extra_files"]

            # compound / cross-artifact 案例 (單一根因橫跨多個 artifact，
            # 例如某個 Kconfig 符號開了但對應的 DTS 節點被拔掉) 在 catalog
            # 裡用 "injections" (list) 表達，其餘單檔案案例維持原本扁平的
            # "target_file"/"operator"。
            # compound / cross-artifact cases (a single root cause spanning
            # multiple artifacts, e.g. a Kconfig symbol enabled but the DTS
            # node it depends on removed) are expressed in the catalog via
            # "injections" (a list); all other single-file cases keep the
            # original flat "target_file"/"operator".
            if "injections" in entry:
                case["injections"] = entry["injections"]
                title_targets = " + ".join(
                    f"{inj['operator']} on {inj['target_file']}" for inj in entry["injections"]
                )
                case["title"] = f"[Injected] {entry['category']} (compound): {title_targets}"
            else:
                case["injection"] = {
                    "target_file": entry["target_file"],
                    "operator": entry["operator"],
                }
                case["title"] = f"[Injected] {entry['category']}: {entry['operator']} on {entry['target_file']}"

            cases.append(case)

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
