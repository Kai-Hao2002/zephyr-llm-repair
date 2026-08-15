# tools/qemu_oracle.py
import pexpect
import logging
import re
import subprocess
from typing import Dict, Any, Optional

# 從一段失敗的 ztest 日誌裡，推斷「是哪一個測試案例失敗了」。用在資料集
# 驗證階段：把注入的 bug 對應到一個具體的測試名稱 (target_test)，這樣之後
# 評分修復結果時，才能確認「當初被注入 bug 打壞的那個測試」有真的被執行
# 且通過，而不是只看「整個套件有沒有印出成功總結」——後者可以被一個把
# 該測試刪掉/跳過的投機 patch 輕易騙過。
#
# 三種來源依序嘗試 (愈前面愈可靠)：
# 1. ztest 每個案例執行完會印的 "FAIL - <test> in X seconds" 行。
# 2. TESTSUITE SUMMARY 區塊裡的 "- FAIL - [<suite>.<test>]" 行 (壓縮後的
#    日誌有時只保留這個摘要區塊)。
# 3. 上述兩者都沒有時 (例如程序被訊號直接殺死，ztest 自己的收尾邏輯根本
#    來不及印出 FAIL 行)，退而求其次抓最後一個 "START - <test>" 行——
#    崩潰前正在執行的那個測試，幾乎必然就是被注入的 bug 打中的那個。
#
# Infers which specific ztest test case failed from a failure log. Used at
# dataset-verification time to pin the injected bug to a concrete test name
# (target_test), so that later, when grading a repair attempt, we can
# confirm the test the injection specifically broke was actually exercised
# and passed — not just that the suite printed some success summary, which
# a shortcut patch that deletes/skips that one test could trivially fake.
#
# Tries three sources in order of reliability:
# 1. ztest's own per-test "FAIL - <test> in X seconds" line.
# 2. The TESTSUITE SUMMARY block's "- FAIL - [<suite>.<test>]" line (the
#    only thing left in some compressed/truncated logs).
# 3. If neither is present (e.g. the process was killed by a signal before
#    ztest's own teardown could print a FAIL line), fall back to the last
#    "START - <test>" line — the test that was running when it crashed is
#    almost certainly the one the injection hit.
_FAIL_LINE_RE = re.compile(r"FAIL - (\S+) in [\d.]+ seconds")
_FAIL_SUMMARY_RE = re.compile(r"FAIL - \[[\w.]+\.(\w+)\]")
_START_LINE_RE = re.compile(r"START - (\S+)")


def extract_failing_test_name(log: str) -> Optional[str]:
    """從一段失敗日誌推斷失敗的 ztest 測試名稱；找不到就回傳 None。
    Infers the failing ztest test name from a failure log; None if not found."""
    m = _FAIL_LINE_RE.search(log)
    if m:
        return m.group(1)
    m = _FAIL_SUMMARY_RE.search(log)
    if m:
        return m.group(1)
    start_matches = _START_LINE_RE.findall(log)
    if start_matches:
        return start_matches[-1]
    return None


def check_required_test_passed(log: str, test_name: str) -> bool:
    """檢查某個特定測試名稱是否在日誌裡明確地以 PASS 結尾——用來把關修復
    結果評分：只有「套件整體成功」還不夠，被注入 bug 打壞的那個測試必須
    真的存在且真的通過，這樣才能排除「把該測試刪掉/註解掉/跳過」這類投機
    patch。同時比對兩種格式 (逐案例的 "PASS - <test> in X seconds" 行，以及
    TESTSUITE SUMMARY 摘要區塊的 "- PASS - [<suite>.<test>]" 行)。

    Checks whether a specific test name shows an explicit PASS in the log —
    used to gate repair grading: "the whole suite reported success" isn't
    enough on its own, the specific test the injection broke must still
    exist and genuinely pass, ruling out a shortcut patch that deletes/
    comments out/skips that test. Matches both per-test ("PASS - <test> in
    X seconds") and TESTSUITE SUMMARY ("- PASS - [<suite>.<test>]") line
    formats.
    """
    if not test_name:
        return True
    pattern = re.compile(
        rf"PASS - (?:\[[\w.]+\.)?{re.escape(test_name)}\b"
    )
    return bool(pattern.search(log))


class QemuOracle:
    """
    監聽 QEMU 執行期輸出，判斷系統是否成功啟動或發生崩潰。
    Monitors QEMU runtime output to determine if the system booted successfully or crashed.
    """
    def __init__(self, timeout: int = 15):
        """
        :param timeout: 啟動 QEMU 後等待輸出的最大秒數 (Maximum seconds to wait for output)
        """
        self.timeout = timeout
        logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
        self.logger = logging.getLogger(__name__)

        # 定義預期的成功字串 (Zephyr 啟動特徵)
        # Expected success signatures (Zephyr boot signatures)
        # "Hello World" 這個 pattern 錨定在行首：main.c 編譯失敗時，gcc 的
        # 診斷輸出常常會原樣印出出錯的那一行原始碼 (例如
        # `11 | printf("Hello World! %s\n", ...)`)，這行文字裡也包含
        # "Hello World" 字樣，但那是編譯器診斷、不是程式真的執行後印出來的
        # stdout。沒錨定行首的話，這種編譯錯誤反而會被誤判為「成功啟動」，
        # 而且是真正的錯誤剛好被自己的錯誤訊息蓋掉，非常隱蔽。真正的
        # printf() 輸出是完全獨立、沒有行號/管線符號前綴的一行。
        # Anchored at line start: when main.c fails to compile, gcc's
        # diagnostic often echoes back the offending source line verbatim
        # (e.g. `11 | printf("Hello World! %s\n", ...)`), which also
        # contains the text "Hello World" — but that's a compiler
        # diagnostic, not genuine program stdout. Without the anchor, this
        # exact kind of compile failure gets misclassified as "successful
        # boot" — the real failure gets masked by its own error message.
        # Genuine printf() output is its own standalone line with no line
        # number/pipe-character prefix.
        self.success_patterns = [
            r"\*\*\* Booting Zephyr OS",
            r"Booting Zephyr OS",
            r"^Hello World!? " # 針對 Hello World 範例的額外檢查
        ]

        # 定義 Zephyr 常見的 Fatal Error 特徵
        # Common Zephyr Fatal Error signatures
        # 前六個是「真的模擬硬體」(QEMU 上的 ARM/x86 等) 才會出現的、由 Zephyr
        # 自己的 fault handler 印出來的字串。但 native_sim 是把 Zephyr 應用
        # 編譯成一支 host 原生執行檔直接跑在 Linux 上，記憶體錯誤是由「host
        # 的 kernel 對這個 process 送出真正的 POSIX 訊號」造成，殼層印出來的
        # 是 "Segmentation fault"/"Aborted"/"Illegal instruction" 這種訊息，
        # 完全不會出現任何一個上面的字串——沒有這幾個 pattern，native_sim 上
        # 的任何一種真實記憶體錯誤都會被監控迴圈直接放過。
        # The first six only appear on *actual emulated hardware* (ARM/x86 on
        # QEMU etc.) via Zephyr's own fault handler. native_sim, though,
        # compiles the Zephyr app into a native host executable that runs
        # directly on Linux — a memory error there kills the process via a
        # real POSIX signal from the host kernel, and the shell reports it as
        # "Segmentation fault"/"Aborted"/"Illegal instruction", none of which
        # match any of the patterns above. Without these, any genuine memory
        # error on native_sim slips straight past the monitoring loop.
        # "PROJECT EXECUTION FAILED" 是 ztest 測試套件真的跑到最後、但有測試
        # 案例斷言失敗時印出的總結——這不是記憶體錯誤/程序被訊號殺死，而是
        # 一個乾淨結束但结果不對的程序，所以既不會出現在上面任何一個訊號類
        # pattern，也不是 build 失敗。對 runtime_crash 類別來說，這種「測試
        # 套件真的執行了、但行為不對」跟真的當機一樣是「這個 commit 真的有
        # 問題」的有效證據，甚至更精確 (有明確是哪個測試案例失敗)。
        # "PROJECT EXECUTION FAILED" is ztest's own summary line when the
        # suite ran to completion but one or more assertions failed — not a
        # memory error/signal-killed process, just a process that exited
        # cleanly with the wrong outcome. It won't match any signal-based
        # pattern above, nor is it a build failure. For the runtime_crash
        # category, a ztest suite that genuinely ran and misbehaved is just
        # as valid evidence that "this commit is genuinely broken" as an
        # actual crash — arguably more precise, since it names which test
        # case failed.
        # 19th systemic bug found in this project's fault-injection pipeline:
        # ZEPHYR FATAL ERROR handler prints "Kernel panic" (lowercase "p"),
        # but this pattern was written as "Kernel Panic" (capital "P") and
        # compiled case-sensitively — so a genuine kernel panic (triggered
        # by a zassert failing inside a PM device-action callback running
        # in idle-thread/ISR-like context, escalating past a normal ztest
        # failure) slipped through unmatched, the process hit a clean EOF,
        # and wait_for_completion=True's fallback logic mis-classified a
        # real crash as "success". Fixed by compiling crash_regex with
        # re.IGNORECASE below instead of only fixing this one pattern's
        # casing, since any future pattern is equally exposed to the same
        # class of case-mismatch.
        self.crash_patterns = [
            r"Kernel Panic",
            r"Fatal fault",
            r"ASSERTION FAIL",
            r"Usage Fault",
            r"Bus Fault",
            r"CPU Page Fault",
            r"Segmentation fault",
            r"Aborted",
            r"Illegal instruction",
            r"PROJECT EXECUTION FAILED",
            # 20th systemic pipeline bug: 上面六個具名硬體錯誤字串
            # (Usage/Bus/CPU Page Fault 等) 只涵蓋 Zephyr fault handler
            # 在特定幾種例外型態下印出的訊息，但 aarch64 的 Data Abort
            # (印 "EC: 0x25 ... Data Abort") 跟 RISC-V 的 Store/AMO/Load
            # access fault (印 "mcause: 7, Store/AMO access fault") 都不
            # 屬於這幾種，導致 dts_reg_offbyone 在 qemu_riscv32/
            # qemu_cortex_a53 上真正打中邊界外位址、CPU 確實 trap 時，
            # 這個 crash 被完全漏接。所有 arch 的 fault handler 在印完
            # 各自的暫存器/例外原因後，最終都會收斂到同一行
            # ">>> ZEPHYR FATAL ERROR" 摘要 (k_fatal_halt 的共用出口)，
            # 所以直接抓這一行比窮舉每個 arch 專屬的例外名稱更通用、更不會
            # 漏接未來新增的板子。
            # The six named hardware-fault strings above only cover the
            # specific exception spellings Zephyr's fault handler happens to
            # print for some exception types, but aarch64's Data Abort
            # (prints "EC: 0x25 ... Data Abort") and RISC-V's Store/AMO/Load
            # access fault (prints "mcause: 7, Store/AMO access fault") are
            # neither of those — so a genuine CPU trap from dts_reg_offbyone
            # landing past a real physical boundary on qemu_riscv32/
            # qemu_cortex_a53 was silently missed. Every arch's fault handler
            # converges on the same ">>> ZEPHYR FATAL ERROR" summary line
            # after printing its own registers/exception cause (the shared
            # exit path through k_fatal_halt), so matching that line directly
            # is more general than enumerating each arch's exception name and
            # won't miss whatever a future board spells differently.
            r"ZEPHYR FATAL ERROR",
        ]

        # ztest 套件真正跑完、且全部通過時印出的總結行——跟上面的
        # "PROJECT EXECUTION FAILED" 是同一段 ztest 收尾邏輯印出的一對訊息，
        # 一個代表「跑完了、失敗」，一個代表「跑完了、成功」。native_sim 上
        # ztest 應用程式跑完後行程會自然結束 (native_sim 的
        # exit-on-completion 行為)，所以 wait_for_completion=True 時單靠
        # pexpect.EOF 就能正確判斷 "success"；但在真正的 QEMU SoC 板子
        # (例如 qemu_cortex_m3) 上，west build -t run 就算 ztest 套件已經跑
        # 完並印出這行總結，QEMU 進程本身並不會自動退出，只會停在那裡直到
        # 外部逾時砍掉它——若沒有這個 pattern，wait_for_completion=True 的
        # 監控迴圈永遠等不到 EOF，只能一路等到 timeout，把一個真正成功的
        # revert 端誤判成 "timeout"。
        # ztest's own summary line printed when the suite ran to completion
        # AND every test passed — the positive counterpart to
        # "PROJECT EXECUTION FAILED" above, both printed by the same ztest
        # teardown code. On native_sim, a ztest binary's process exits on
        # its own once the suite finishes (native_sim's exit-on-completion
        # behavior), so wait_for_completion=True can correctly land on
        # "success" via plain pexpect.EOF. On a real QEMU SoC board (e.g.
        # qemu_cortex_m3), though, `west build -t run` never terminates
        # QEMU on its own after the suite finishes — it just sits there
        # until an external timeout kills it — so without this pattern the
        # wait_for_completion=True loop can never reach EOF and a genuinely
        # successful revert-side run gets misclassified as "timeout".
        self.completion_success_patterns = [
            r"PROJECT EXECUTION SUCCESSFUL",
        ]

        # 某些真實硬體板子不支援 QEMU/native 模擬，west 會印出這句話然後直接
        # 結束，跟目標 commit 是否有 bug 完全無關。若不特別排除，這種情況會
        # 落入 EOF 分支被誤判為「重現成功」。
        # Some real hardware boards don't support QEMU/native emulation — west
        # just prints this and exits, regardless of whether the target commit
        # has a bug. Without excluding it explicitly, this falls into the EOF
        # branch and gets misclassified as a successful repro.
        self.unsupported_patterns = [
            r"Emulation/Simulation not supported with this board",
        ]

        # Docker daemon 本身斷線/崩潰時，docker CLI 會印出這類訊息，容器連線
        # 被意外切斷，pexpect 會收到 EOF，很容易被誤判為「建置失敗、正常結束」。
        # 這是基礎設施本身出問題，跟目標 commit 有沒有 bug 完全無關，必須排除。
        # 這個檢查不受 in_build_phase 限制，因為 daemon 隨時可能斷線。
        # docker CLI prints messages like these when the Docker daemon itself
        # disconnects/crashes, cutting the container connection and giving
        # pexpect an EOF that's easily misread as "build failed, exited
        # normally". This is an infrastructure failure unrelated to whether
        # the target commit has a bug, and must be excluded. Not gated behind
        # in_build_phase since the daemon can drop the connection at any time.
        self.docker_infra_error_patterns = [
            r"error waiting for container",
            r"Cannot connect to the Docker daemon",
            r"Error response from daemon",
            r"context deadline exceeded",
        ]
        self.docker_infra_error_regex = [re.compile(p) for p in self.docker_infra_error_patterns]

        # `west update` 抓取模組時若網路故障 (例如某個 hal module 的 git
        # remote 暫時連不上)，west/git 會印出這類錯誤然後結束，行程收到
        # EOF——由於這發生在 `west build: generating a build system` 標記
        # 之前，尚未進入 in_build_phase，之前完全沒有專門的分類，只能落入
        # EOF 分支被歸類成 "eof_no_boot"，而 "eof_no_boot" 正好是
        # verify_cases.py 的 ACCEPTED_FAILURE_STATUSES 之一，等於把一次
        # 純網路失敗誤判成「成功重現了目標 commit 的 bug」。
        # 2026-08-12 挖礦驗證 (bug_111542/llext) 就是這樣被誤判成假陽性
        # 的，`initial_error_log` 存的其實是 west update 過程的雜訊
        # (`ERROR: update failed for project hal_bouffalolab`)，跟 PR 真正
        # 修的 bug 完全無關；隔天人工重跑才發現這是暫時性網路問題，跟
        # docker_infra_error 是同一類「基礎設施本身出錯，與目標 commit 是
        # 否有 bug 無關」，必須明確排除，不能靠 eof_no_boot 這個通用桶子
        # 蒙混過去。同樣不受 in_build_phase 限制 (west update 一定發生在
        # build 開始之前)，且 pattern 錨定行首，避免比對到歷史 commit
        # 訊息裡剛好包含類似字樣的文字 (跟下面 build_start_marker 的說明
        # 是同一種風險，但 anchor-at-line-start 已經足以避免——west/git
        # 自己印的錯誤訊息一定是獨立、無縮排的一行，嵌在某個 commit
        # subject 裡的引用文字幾乎不會剛好整行都是這個格式)。
        #
        # If `west update` hits a network failure fetching a module (e.g. a
        # hal module's git remote is briefly unreachable), west/git prints
        # an error like this and exits, giving pexpect an EOF. Because this
        # happens before the `west build: generating a build system` marker
        # (still pre-in_build_phase), there was previously no dedicated
        # classification for it — it fell through to the EOF branch and got
        # labeled "eof_no_boot", which happens to be one of
        # verify_cases.py's ACCEPTED_FAILURE_STATUSES — silently turning a
        # pure network failure into a false "successfully reproduced the
        # target commit's bug". This is exactly what happened during the
        # 2026-08-12 mining verification of bug_111542 (llext): the stored
        # `initial_error_log` was actually west-update noise (`ERROR:
        # update failed for project hal_bouffalolab`), unrelated to the
        # PR's real bug; a manual re-run the next day is what caught it.
        # Same class of problem as docker_infra_error above — an
        # infrastructure failure unrelated to whether the target commit has
        # a bug — and must be excluded the same way rather than falling
        # into the generic eof_no_boot bucket. Also not gated behind
        # in_build_phase (west update always happens before the build
        # starts), and patterns are anchored at line start to avoid
        # matching a historical commit message that happens to quote
        # similar text (same class of risk the build_start_marker comment
        # below describes, but anchoring is enough here — west/git's own
        # error output is always its own unindented line, not something a
        # commit subject would happen to reproduce verbatim).
        self.west_update_error_patterns = [
            r"^ERROR: update failed for project",
            r"^fatal: unable to access '",
            r"^fatal: unable to connect to",
            r"^fatal: could not read from remote repository",
            r"^fatal: the remote end hung up unexpectedly",
            r"^fatal: early EOF",
            r"^fatal: index-pack failed",
            r"^error: RPC failed; curl",
            r"Could not resolve host:",
        ]
        self.west_update_error_regex = [re.compile(p, re.IGNORECASE) for p in self.west_update_error_patterns]

        # west/git 在抓取模組時 (git fetch/checkout/west update) 印出的歷史 commit
        # 訊息，內容完全不受我們控制，字面上可能剛好包含 "Bus Fault"、"Hello World"
        # 等字樣 (例如某個歷史 commit 的 subject 是 "Fix Bus Fault during raw TX")。
        # 在真正進入 west build 之前比對 crash/success 特徵，會被這種雜訊誤觸發。
        # 所以只在看到這個 west build 啟動的標記行之後，才開始比對特徵。
        # west/git print historical commit messages while fetching modules
        # (git fetch/checkout/west update), content we don't control that can
        # literally contain "Bus Fault", "Hello World", etc. (e.g. a historical
        # commit subject like "Fix Bus Fault during raw TX"). Matching crash/
        # success patterns before west build actually starts gets false-triggered
        # by this noise. So only start matching after seeing this build-start marker.
        self.build_start_marker = re.compile(r"west build: generating a build system")

        # 將上述字串編譯為正規表示式以加速匹配
        # Compile patterns to regex for faster matching
        self.success_regex = [re.compile(p) for p in self.success_patterns]
        self.crash_regex = [re.compile(p, re.IGNORECASE) for p in self.crash_patterns]
        self.unsupported_regex = [re.compile(p) for p in self.unsupported_patterns]
        self.completion_success_regex = [re.compile(p) for p in self.completion_success_patterns]

    def evaluate(self, command: str, container_name: Optional[str] = None,
                 wait_for_completion: bool = False,
                 required_pass_test: Optional[str] = None) -> Dict[str, Any]:
        """
        執行指令並監聽 QEMU 輸出。

        :param required_pass_test: 若提供，即使日誌整體判定為 "success"
            (套件印出成功總結、或看到成功啟動特徵)，也還要額外確認這個
            特定測試名稱在日誌裡有明確的 PASS 紀錄，否則把狀態降級為
            "missing_required_test"。用在評分「修復後的 patch」時：防堵
            投機的修復方式 (刪掉/註解掉/跳過原本被注入 bug 打壞的那個
            測試) 也被誤判為修復成功。設定這個參數時會強制以
            wait_for_completion=True 的方式監控，否則在看到開機橫幅就
            提早停止監控，根本沒機會觀察到目標測試的 PASS/FAIL 行。
        :param container_name: 若指令有帶 `--name <container_name>` 啟動 Docker 容器，
            傳入這個名稱可以確保 timeout/例外發生時真的把容器殺掉，而不是只依賴
            `child.close(force=True)` 終止本地 pexpect 子進程 (docker run 這個 CLI
            進程收到 SIGKILL 時不一定來得及通知 daemon 停掉容器，導致殭屍容器持續
            佔用 CPU/網路，拖垮後續所有驗證直到全部逾時)。
        :param wait_for_completion: 像 samples/hello_world 這種印一次訊息就進入
            idle loop、永遠不會自己結束的 app，一看到成功特徵就必須馬上停止監控
            (否則會一路空等到 timeout)。但 ztest 類的 app 在印出開機橫幅
            "Booting Zephyr OS" 之後才會真正開始跑測試——那正是 runtime_crash
            類別注入的錯誤預期會被觸發的地方。如果看到開機橫幅就馬上停止監控，
            等於保證永遠觀察不到「開機後、測試執行期間」才發生的崩潰，
            runtime_crash 類別就變成不可能被偵測到的類別。設成 True 時，看到
            成功特徵只會先暫定為 "success"，繼續監控到 EOF/timeout 或真的等到
            crash 特徵出現為止。
        Executes the command and listens to the QEMU output.

        :param container_name: If the command starts a Docker container with
            `--name <container_name>`, pass it here to guarantee the container
            is actually killed on timeout/exception — not just relying on
            `child.close(force=True)` to terminate the local pexpect child
            (the `docker run` CLI process may not have time to tell the daemon
            to stop the container before it's SIGKILLed, leaking a zombie
            container that keeps eating CPU/network and stalls every
            subsequent verification until they all time out).
        :param wait_for_completion: Apps like samples/hello_world print once
            and then sit in an idle loop forever, so monitoring must stop the
            instant a success signature is seen (otherwise we'd just wait out
            the full timeout for nothing). ztest-based apps, though, only
            start actually running tests *after* printing the "Booting Zephyr
            OS" boot banner — which is exactly where a runtime_crash
            injection is expected to fire. Stopping at the boot banner makes
            it structurally impossible to ever observe a crash that happens
            during test execution, i.e. the runtime_crash category could
            never be detected. When True, a success signature is only
            tentatively recorded; monitoring continues until EOF/timeout or
            an actual crash signature appears.
        :param required_pass_test: If given, even a log that would
            otherwise be classified "success" (a success summary line, or
            a success boot signature) must additionally show an explicit
            PASS record for this specific test name, or the status is
            downgraded to "missing_required_test". Used when grading a
            repaired patch: guards against a shortcut "fix" that deletes/
            comments out/skips the exact test the injected bug broke from
            also being misclassified as a successful repair. Setting this
            forces wait_for_completion=True internally — otherwise
            monitoring could stop at the boot banner before the target
            test's PASS/FAIL line is ever seen.
        """
        if required_pass_test:
            wait_for_completion = True

        self.logger.info("啟動 Test Oracle 並監控 QEMU 輸出... (Starting Test Oracle to monitor QEMU...)")

        # 由於我們需要監測 Docker 的輸出，使用 pexpect.spawn
        # Using pexpect.spawn to monitor Docker output interactively
        # encoding='utf-8' 確保我們處理的是字串而不是 Bytes
        try:
            child = pexpect.spawn(command, encoding='utf-8', timeout=self.timeout)
            
            captured_log = ""
            result = {"status": "unknown", "log": "", "error_signature": None}
            in_build_phase = False

            # 持續讀取每一行 (Iterate line by line)
            while True:
                try:
                    # 每次讀取一行 (\r\n 處理跨平台換行)
                    child.expect(r'\r?\n')
                    line = child.before.strip()
                    if line:
                        captured_log += line + "\n"
                        # print(f"[QEMU] {line}") # 測試時可以解開註解以觀察即時輸出

                        # -1. 檢查 Docker daemon 本身是否斷線/崩潰 (不受 build phase 限制)
                        for pattern in self.docker_infra_error_regex:
                            if pattern.search(line):
                                self.logger.error(f"偵測到 Docker 基礎設施錯誤，與目標 commit 無關 (Docker infrastructure error detected, unrelated to the target commit): {pattern.pattern}")
                                result["status"] = "docker_infra_error"
                                result["error_signature"] = pattern.pattern
                                break

                        if result["status"] == "docker_infra_error":
                            break

                        # -0.5. 檢查 west update 抓取模組是否網路故障
                        # (同樣不受 in_build_phase 限制，見上方註解)
                        # Check for a west-update module-fetch network
                        # failure (also not gated by in_build_phase, see
                        # comment above)
                        for pattern in self.west_update_error_regex:
                            if pattern.search(line):
                                self.logger.error(f"偵測到 west update 模組抓取失敗，與目標 commit 無關 (west update module-fetch failure detected, unrelated to the target commit): {pattern.pattern}")
                                result["status"] = "west_update_error"
                                result["error_signature"] = pattern.pattern
                                break

                        if result["status"] == "west_update_error":
                            break

                        if not in_build_phase:
                            if self.build_start_marker.search(line):
                                in_build_phase = True
                            else:
                                # 還在 git fetch/west update 階段，跳過特徵比對，
                                # 避免被歷史 commit 訊息誤觸發。
                                # Still in the git fetch/west update phase — skip
                                # pattern matching to avoid false triggers from
                                # historical commit messages.
                                continue

                        # 0. 檢查該板子是否根本不支援模擬 (Check for an unsupported board)
                        for pattern in self.unsupported_regex:
                            if pattern.search(line):
                                self.logger.warning(f"此開發板不支援模擬，與目標 commit 無關 (Board doesn't support emulation, unrelated to the target commit): {pattern.pattern}")
                                result["status"] = "unsupported_board"
                                result["error_signature"] = pattern.pattern
                                break

                        if result["status"] == "unsupported_board":
                            break

                        # 1. 檢查是否發生崩潰 (Check for crash)
                        for pattern in self.crash_regex:
                            if pattern.search(line):
                                self.logger.error(f"偵測到執行期崩潰 (Runtime crash detected): {pattern.pattern}")
                                result["status"] = "crash"
                                result["error_signature"] = pattern.pattern
                                break
                        
                        # 如果已經崩潰，跳出迴圈
                        if result["status"] == "crash":
                            break

                        # 1.5 檢查 ztest 是否已經跑完且全數通過 (Check for a
                        # definitive "ztest suite completed and passed"
                        # signal). 這比單純的開機橫幅更強的證據：它代表
                        # wait_for_completion=True 想等待的「套件真的執行
                        # 完畢」已經確定發生，不需要再靠行程自然退出
                        # (EOF) 才能判定 success——在 QEMU SoC 板子上，行程
                        # 完成測試後往往不會自己結束。
                        suite_completed = False
                        for pattern in self.completion_success_regex:
                            if pattern.search(line):
                                self.logger.info("偵測到 ztest 套件執行完畢且全數通過！ (ztest suite completed and passed!)")
                                result["status"] = "success"
                                suite_completed = True
                                break

                        if suite_completed:
                            break

                        # 2. 檢查是否成功啟動 (Check for success)
                        if result["status"] != "success":
                            for pattern in self.success_regex:
                                if pattern.search(line):
                                    self.logger.info("偵測到成功啟動特徵！ (Successful boot signature detected!)")
                                    result["status"] = "success"
                                    break

                        if result["status"] == "success" and not wait_for_completion:
                            break

                except pexpect.TIMEOUT:
                    # 如果 QEMU 卡住且超過指定時間沒有新輸出
                    self.logger.warning(f"QEMU 執行超時 ({self.timeout}s). (QEMU execution timed out.)")
                    result["status"] = "timeout"
                    break
                
                except pexpect.EOF:
                    # 程式自然結束。若先前已經看到開機成功特徵、且一路監控到
                    # 現在都沒再出現 crash 特徵 (wait_for_completion=True 的
                    # ztest 情境)，代表整個流程真的順利跑完，維持 "success"；
                    # 否則就是建置失敗根本沒啟動 QEMU，才是 "eof_no_boot"。
                    # Process exited normally. If we'd already seen a boot
                    # success signature and no crash appeared while we kept
                    # watching (the wait_for_completion=True ztest case), the
                    # whole run genuinely completed cleanly — keep "success".
                    # Otherwise this is the build-failed-before-QEMU-ever-
                    # started case, i.e. "eof_no_boot".
                    self.logger.info("進程已結束 (Process exited).")
                    if result["status"] != "success":
                        result["status"] = "eof_no_boot"
                    break

        finally:
            # 確保強制關閉子進程，避免殭屍容器殘留
            # Ensure the child process is forcefully closed to prevent zombie containers
            if child and child.isalive():
                child.close(force=True)

            # 保險機制：直接對 Docker daemon 下達 kill，不管本地子進程有沒有
            # 成功終止容器都確保清乾淨 (--rm 容器被 kill 後會自動移除)。
            # Safety net: kill the container directly via the Docker daemon
            # regardless of whether the local child process managed to stop it
            # (a `--rm` container is auto-removed once killed).
            if container_name:
                try:
                    subprocess.run(
                        ["docker", "kill", container_name],
                        capture_output=True, timeout=10
                    )
                except Exception:
                    pass

            result["log"] = captured_log

            # 就算前面判定為 "success"，只要指定了 required_pass_test，
            # 就再確認那個特定測試真的以 PASS 收尾——防止「刪掉/跳過該
            # 測試」這種投機 patch 也被誤判為修復成功。
            # Even if the status above is "success", if required_pass_test
            # was given, verify that specific test genuinely ended in PASS
            # — guards against a shortcut patch (deleting/skipping that
            # test) also being misclassified as a successful repair.
            if required_pass_test and result["status"] == "success":
                if not check_required_test_passed(captured_log, required_pass_test):
                    self.logger.warning(
                        f"套件回報成功，但目標測試 '{required_pass_test}' 沒有明確的 PASS 紀錄——"
                        f"可能被刪除、跳過或改名，不算修復成功。"
                        f" (Suite reported success, but no explicit PASS for target test "
                        f"'{required_pass_test}' — possibly deleted, skipped, or renamed; "
                        f"not counted as a successful repair.)"
                    )
                    result["status"] = "missing_required_test"
                    result["error_signature"] = f"required test '{required_pass_test}' did not pass"

            return result

# ==========================================
# 測試區塊 (Testing Block)
# ==========================================
if __name__ == "__main__":
    oracle = QemuOracle(timeout=10)
    
    # 這裡我們模擬上一階段的 west_executor 產生的 docker 啟動指令
    # 注意：我們加入了 -t run 來要求 west 建置完畢後直接啟動 QEMU
    # Note: We added `-t run` to instruct west to run QEMU immediately after building.
    test_project_path = "/絕對路徑/到您的/hello_world" # <--- 請修改為您的絕對路徑 (Must be absolute path)
    
    # -i 允許互動模式，這對 pexpect 抓取 stdout 很重要
    docker_cmd = (
        f"docker run --rm -i -v {test_project_path}:/workspace:ro "
        "-w /workspace zephyr-sandbox "
        "bash -c 'west build -b qemu_x86 -d /tmp/build -p always -t run .'"
    )
    
    print("=== 執行 QEMU 閉環驗證測試 (Testing QEMU Closed-Loop Oracle) ===")
    eval_result = oracle.evaluate(docker_cmd)
    
    print("\n--- 驗證結果 (Evaluation Result) ---")
    print(f"狀態 (Status): {eval_result['status']}")
    if eval_result['error_signature']:
        print(f"捕捉到的錯誤特徵 (Error Signature): {eval_result['error_signature']}")
    print("部分日誌 (Partial Log):")
    # 只印出最後 300 個字元避免洗頻
    print(eval_result['log'][-300:])