# tools/fault_injector.py
import os
import re
import time
import logging
from typing import Dict, Any, List, Optional

from tools.qemu_oracle import QemuOracle

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("FaultInjector")

# 每個類別預期會在哪個階段偵測到失敗特徵，用來判斷注入是否「如預期般」重現。
# kconfig/dts/c_syntax 通常在建置階段就結束 (eof_no_boot)，但某些 Kconfig
# 衝突會被 CMake/ninja 判定為直接崩潰退出，所以也接受 crash；runtime_crash
# 則要求必須是真正在 QEMU 執行期偵測到的 crash 特徵，光是建置失敗不算數。
# Which failure statuses count as a match for each category's intended
# failure stage. kconfig/dts/c_syntax usually end at the build stage
# (eof_no_boot), but some Kconfig conflicts get treated as an outright
# crash by CMake/ninja, so that's accepted too; runtime_crash requires an
# actual QEMU-runtime crash signature — a mere build failure doesn't count.
EXPECTED_FAILURE_STATUSES = {
    "kconfig": {"eof_no_boot", "crash"},
    "dts": {"eof_no_boot", "crash"},
    "c_syntax": {"eof_no_boot"},
    "runtime_crash": {"crash"},
    # compound 類別橫跨兩種難度：編譯期組合失敗 (跟 kconfig/dts 一樣是
    # eof_no_boot) 以及執行期靜默邏輯錯誤 (跟 runtime_crash 一樣，ztest
    # 斷言失敗會落在 crash 這個狀態，見 qemu_oracle.py 的
    # PROJECT EXECUTION FAILED pattern)，所以取兩者聯集。
    # compound spans two difficulty tiers: build-time combination failures
    # (eof_no_boot, like kconfig/dts) and runtime silent logic errors (crash,
    # like runtime_crash — a failed ztest assertion lands in this status via
    # qemu_oracle.py's PROJECT EXECUTION FAILED pattern), so this is the
    # union of both.
    "compound": {"eof_no_boot", "crash"},
}

MUTATE_SCRIPT_HOST_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "mutate_inject.py"))
MUTATE_SCRIPT_CONTAINER_PATH = "/tmp/mutate_inject.py"


class FaultInjector:
    """
    對一個已知能成功建置的 baseline commit 注入合成錯誤，並透過雙向驗證閘
    (注入後必須重現預期類別的失敗、還原後必須建置並執行成功) 確認可用，
    才收錄進 Zephyr-Eval。對應論文提案「Controlled Fault Injection Protocol」。

    Injects a synthetic fault into a known-good baseline commit and only
    accepts it into Zephyr-Eval after a two-sided verification gate: the
    mutation must reproduce a failure matching its intended category, and
    reverting it must build and run successfully. Implements the "Controlled
    Fault Injection Protocol" described in the thesis proposal.
    """
    def __init__(self, timeout: int = 600):
        self.oracle = QemuOracle(timeout=timeout)

    def inject_and_verify(self, case_id: str, baseline_commit: str, injections: List[Dict[str, str]],
                           category: str, target_app: str, board: str,
                           extra_files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        執行雙向驗證閘，回傳 {"accepted": bool, "reason": str (若拒絕),
        "mutated_result": dict, "reverted_result": dict (若有跑到這步)}。

        injections：[{"target_file": ..., "operator": ...}, ...] 至少一組。
        單檔案案例傳一個元素的 list 即可；compound / cross-artifact 案例
        (單一根因橫跨多個 artifact，例如某個 Kconfig 符號開了但對應的 DTS
        節點被拔掉) 傳多組，會依序套用、依相反順序還原，整組一起判定
        通過與否——不會個別驗證「只套用其中一組是否也會失敗」，那是
        registration 前人工 recon 階段該做的交叉檢查，用來確認這真的是
        compound bug 而不是兩個各自獨立就會炸的巧合。

        extra_files (選用)：{"相對於 zephyr repo 根目錄的路徑": "host 上的
        來源檔案絕對路徑"}——在套用 mutation 之前，先把這些檔案 bind-mount
        進容器裡對應的位置 (蓋掉/新增該路徑)。用來支援「把既有測試移植到
        新板子」這類需要新增/修改 mutation 目標檔以外的檔案的 injection
        案例 (例如新增一個該測試原本沒有的 boards/<board>.overlay，或修改
        tests.yaml 的 platform_allow)——單純的 mutation 機制沒辦法表達這種
        「baseline checkout 之外還需要額外檔案」的情境。

        Runs the two-sided verification gate.

        injections: [{"target_file": ..., "operator": ...}, ...], at least
        one. Single-file cases pass a one-element list; compound /
        cross-artifact cases (a single root cause spanning multiple
        artifacts, e.g. a Kconfig symbol enabled but the DTS node it depends
        on removed) pass multiple, applied in order and reverted in reverse
        order, judged as a single unit — this does not separately verify
        "does applying only one of them also fail", which is a cross-check
        that belongs in manual recon before registration, to confirm this is
        a genuine compound bug rather than two independently-breaking
        mutations that happen to be bundled together.

        extra_files (optional): {"path relative to the zephyr repo root":
        "absolute host path to the source file"} — bind-mounted into the
        container at the corresponding path *before* the mutations are
        applied (overwriting/adding that path). Supports injection cases
        that need to "port an existing test to a new board" — files beyond
        the mutation target_files (e.g. adding a boards/<board>.overlay the
        test didn't originally have, or editing tests.yaml's
        platform_allow) — which the mutation mechanism alone can't express.
        """
        mutated_result = self._run(case_id, baseline_commit, injections, category, target_app, board, revert=False, extra_files=extra_files)

        if mutated_result["status"] == "operator_no_match":
            targets = ", ".join(f"{inj['operator']} on {inj['target_file']}" for inj in injections)
            return {
                "accepted": False,
                "reason": f"mutation operator found no match for: {targets}",
                "mutated_result": mutated_result,
            }

        expected = EXPECTED_FAILURE_STATUSES.get(category, {"eof_no_boot", "crash"})
        if mutated_result["status"] not in expected:
            return {
                "accepted": False,
                "reason": f"injected mutation did not produce an expected '{category}' failure (got status='{mutated_result['status']}')",
                "mutated_result": mutated_result,
            }

        reverted_result = self._run(case_id, baseline_commit, injections, category, target_app, board, revert=True, extra_files=extra_files)
        if reverted_result["status"] != "success":
            return {
                "accepted": False,
                "reason": f"reverted mutation did not build/boot successfully (got status='{reverted_result['status']}') — operator may be unsafe for this file",
                "mutated_result": mutated_result,
                "reverted_result": reverted_result,
            }

        return {
            "accepted": True,
            "mutated_result": mutated_result,
            "reverted_result": reverted_result,
        }

    def _run(self, case_id: str, baseline_commit: str, injections: List[Dict[str, str]],
              category: str, target_app: str, board: str, revert: bool,
              extra_files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        跑一次容器：checkout baseline -> west update -> 依序套用每組 injection
        的 mutation (revert=True 時緊接著依相反順序全部還原) -> west build
        -t run。透過 bind-mount 把 tools/mutate_inject.py 掛進容器，不需要
        重建 image。
        Runs one container: checkout baseline -> west update -> apply each
        injection's mutation in order (immediately reverting all of them in
        reverse order too if revert=True) -> west build -t run. Bind-mounts
        tools/mutate_inject.py into the container so no image rebuild is
        needed.
        """
        suffix = "revert" if revert else "mutate"
        container_name = f"inject_{case_id}_{suffix}_{int(time.time() * 1000)}"

        # operator 字串裡的 shell 特殊字元 (例如 thread_priority_swap 的
        # hint 常常是原始碼裡的字面文字 "K_PRIO_PREEMPT(0):K_PRIO_PREEMPT(1)"，
        # 含有括號) 需要逐字元跳脫，而不能簡單包一層單引號了事：docker_cmd
        # 這個字串會先被 pexpect.spawn 用 shlex.split() 解析一次 (組出
        # exec docker 用的 argv)，之後容器內真正的 bash 才會把 -c 的內容
        # 當成腳本再解析第二次。這裡本來就已經包在最外層那對單引號 (bash
        # -c '...') 之內，若在裡面「再」包一層單引號，shlex 並不理解巢狀
        # 引號，會把我加的這對單引號當成跟最外層同一種、用來切換
        # 引用狀態的字元，等於把外層引用提早關閉、操作元反而變成「未加
        # 引號」，實測 (shlex.split() + 真的 exec 一次) 確認引號會被吃掉，
        # 括號依然原封不動被丟進容器內的 bash 造成語法錯誤。正確做法是
        # 對括號 (以及其他保留字元) 做反斜線跳脫：shlex 在「已經處於單引號
        # 模式」時完全不處理任何字元 (不只是引號，反斜線也不例外)，所以
        # 反斜線能原封不動撐過 shlex.split() 那一層；等真正進入容器內的
        # bash 重新解析這段未加引號的文字時，反斜線才會被當成「跳脫下一個
        # 字元」正確處理掉，傳給 python3 的引數會是還原後的原始字面值。
        # Shell metacharacters inside the operator string (e.g.
        # thread_priority_swap's hint is often literal source text like
        # "K_PRIO_PREEMPT(0):K_PRIO_PREEMPT(1)", containing parentheses)
        # need per-character backslash escaping, not a simple single-quote
        # wrap: this docker_cmd string first gets parsed once by
        # pexpect.spawn's shlex.split() (to build the argv for exec'ing
        # docker), and only then does the real bash inside the container
        # parse the -c argument as a script a second time. It's already
        # inside the outermost pair of single quotes (bash -c '...'); adding
        # ANOTHER pair of single quotes around the operator doesn't nest —
        # shlex has no concept of nesting and treats my added quotes as the
        # same kind of quote-state toggle as the outer ones, which
        # prematurely closes the outer quoting and leaves the operator
        # effectively unquoted again. Empirically confirmed (shlex.split()
        # + an actual exec) that the quotes just get consumed and the
        # parentheses still reach the container's bash bare, causing the
        # same syntax error. The fix is to backslash-escape the
        # parentheses (and other reserved characters) instead: shlex, once
        # already inside single-quote mode, passes every character through
        # completely untouched (not even backslashes are special inside
        # single quotes), so the backslashes survive shlex.split() intact;
        # once the container's bash re-parses this now-unquoted text, the
        # backslashes are correctly consumed as escape characters, and
        # python3 receives the original, unescaped literal value.
        mutate_cmds = []
        for inj in injections:
            escaped_operator = re.sub(r'([^A-Za-z0-9_./:-])', r'\\\1', inj["operator"])
            mutate_cmds.append(
                f"python3 {MUTATE_SCRIPT_CONTAINER_PATH} /zephyrproject/zephyr/{inj['target_file']} {escaped_operator}"
            )

        # extra_files 裡每一組 (container 相對路徑 -> host 絕對路徑) 用來支援
        # 「把既有測試移植到新板子」這類需要動到 mutation 目標檔以外的檔案的
        # 案例 (新增一個原本沒有的 boards/<board>.overlay，或修改
        # tests.yaml 的 platform_allow)。**不能**直接把它們 bind-mount 到
        # 最終路徑：如果那個路徑是既有的 git 追蹤檔案 (例如 tests.yaml)，
        # 唯讀 bind mount 會讓 git checkout 想要覆寫該路徑時得到 Permission
        # denied，整條 checkout 失敗。改成先掛到一個獨立的 staging 路徑
        # (/tmp/extra_files/N)，等 git checkout + west update 都做完、
        # 目標路徑已經是一般 (非 mount) 檔案之後，再用 cp 把內容複製過去
        # ——這一步是普通檔案寫入，不會撞到 bind mount 的唯讀限制。
        # Each (container-relative path -> host absolute path) pair in
        # extra_files supports "porting an existing test to a new board" —
        # cases that need files beyond the single mutation target_file (e.g.
        # adding a boards/<board>.overlay the test didn't originally have, or
        # editing tests.yaml's platform_allow). These can't be bind-mounted
        # directly at their final path: if that path is an existing
        # git-tracked file (e.g. tests.yaml), a read-only bind mount makes
        # git checkout's attempt to overwrite it fail with Permission denied,
        # aborting the whole checkout. Instead, mount each one at an
        # independent staging path (/tmp/extra_files/N) and, once git
        # checkout + west update have already finished (so the target path
        # is back to being a normal, unmounted file), cp it into place — a
        # plain file write that doesn't collide with the bind mount's
        # read-only restriction.
        extra_mounts = ""
        copy_steps = []
        if extra_files:
            for idx, (container_rel_path, host_path) in enumerate(extra_files.items()):
                staging_path = f"/tmp/extra_files/{idx}"
                extra_mounts += f"-v {host_path}:{staging_path}:ro "
                dest = f"/zephyrproject/zephyr/{container_rel_path}"
                copy_steps.append(f"mkdir -p $(dirname {dest}) && cp {staging_path} {dest}")

        steps = copy_steps + mutate_cmds
        if revert:
            # 依相反順序全部還原——每個 operator 各自的 .orig 備份彼此獨立，
            # 順序本身不影響正確性，但反序是比較保守、一致的慣例。
            # Revert all of them in reverse order — each operator's .orig
            # backup is independent, so order doesn't affect correctness, but
            # reverse order is the more conservative, consistent convention.
            steps.extend(f"{cmd} --revert" for cmd in reversed(mutate_cmds))

        # baseline commit 是動態解析出來的「main 分支目前的 tip」，image 建置
        # 當下抓到的 main 通常已經落後，本地 git 物件庫裡沒有這個新 commit，
        # 必須先 git fetch 才能 checkout，否則會得到
        # "fatal: reference is not a tree" 而整條 && 鏈提早中止，被誤判為
        # 「建置失敗」(剛好符合某些類別預期的失敗特徵，變成假陽性)。
        # The baseline commit is resolved dynamically as "the current tip of
        # main", which is usually newer than whatever main was checked out
        # when the image was built — the local git object store won't have
        # it yet. Must git fetch before checkout, otherwise we get "fatal:
        # reference is not a tree" and the && chain aborts early, which can
        # be misread as "build failed" (a false positive that happens to
        # match some categories' expected failure signature).

        docker_cmd = (
            f"docker run --rm -i --name {container_name} --cpus=2 --memory=2400m "
            f"-v {MUTATE_SCRIPT_HOST_PATH}:{MUTATE_SCRIPT_CONTAINER_PATH}:ro "
            f"{extra_mounts}"
            f"zephyr-sandbox bash -c '"
            f"cd /zephyrproject/zephyr && "
            f"git fetch origin {baseline_commit} && "
            f"git checkout {baseline_commit} && "
            f"west update --narrow && "
            + " && ".join(steps) + " && "
            f"cd /zephyrproject/zephyr/{target_app} && "
            f"west build -b {board} -p always -t run"
            f"'"
        )

        # runtime_crash 類別的目標是 ztest suite：開機橫幅一定會先於測試
        # 執行出現，若一看到它就停止監控，永遠不可能觀察到測試執行期間
        # 才發生的崩潰。見 QemuOracle.evaluate 的 wait_for_completion 說明。
        # compound 也需要同樣的耐心：它的執行期靜默邏輯錯誤子類型
        # (compatible 綁錯 instance) 一樣是開機成功後才在特定測試斷言失敗，
        # 提早在開機橫幅停止監控一樣會錯過；編譯期失敗的 compound 子類型
        # 根本到不了開機橫幅，這個旗標對它是無害的。
        # The runtime_crash category targets a ztest suite: the boot banner
        # always appears before any test actually runs, so stopping at it
        # would make it impossible to ever observe a crash that happens
        # during test execution. See QemuOracle.evaluate's
        # wait_for_completion docstring. compound needs the same patience:
        # its runtime silent-logic-error subtype (compatible bound to the
        # wrong instance) also only fails a specific test assertion after a
        # successful boot, and build-time compound cases never reach the
        # boot banner at all, so this flag is harmless for them.
        wait_for_completion = category in ("runtime_crash", "compound")
        result = self.oracle.evaluate(docker_cmd, container_name=container_name,
                                       wait_for_completion=wait_for_completion)

        # mutate_inject.py 找不到可套用的匹配時會印出 "NO_MATCH: ..." 並以非 0
        # 結束碼中止整條 && 鏈——這會讓 pexpect 收到 EOF，跟「mutation 生效後
        # 真的讓建置失敗」看起來一樣，必須從日誌內容裡明確分辨出來。
        # When mutate_inject.py finds nothing to mutate, it prints
        # "NO_MATCH: ..." and exits non-zero, aborting the && chain — pexpect
        # sees this as an EOF that looks identical to "the mutation took
        # effect and broke the build", so we must disambiguate from the log.
        if "NO_MATCH:" in result.get("log", ""):
            result["status"] = "operator_no_match"

        return result
