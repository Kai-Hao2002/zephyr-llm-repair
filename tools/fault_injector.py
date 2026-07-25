# tools/fault_injector.py
import os
import time
import logging
from typing import Dict, Any

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

    def inject_and_verify(self, case_id: str, baseline_commit: str, target_file: str,
                           operator: str, category: str, target_app: str, board: str) -> Dict[str, Any]:
        """
        執行雙向驗證閘，回傳 {"accepted": bool, "reason": str (若拒絕),
        "mutated_result": dict, "reverted_result": dict (若有跑到這步)}。
        Runs the two-sided verification gate.
        """
        mutated_result = self._run(case_id, baseline_commit, target_file, operator, category, target_app, board, revert=False)

        if mutated_result["status"] == "operator_no_match":
            return {
                "accepted": False,
                "reason": f"mutation operator '{operator}' found no match in {target_file}",
                "mutated_result": mutated_result,
            }

        expected = EXPECTED_FAILURE_STATUSES.get(category, {"eof_no_boot", "crash"})
        if mutated_result["status"] not in expected:
            return {
                "accepted": False,
                "reason": f"injected mutation did not produce an expected '{category}' failure (got status='{mutated_result['status']}')",
                "mutated_result": mutated_result,
            }

        reverted_result = self._run(case_id, baseline_commit, target_file, operator, category, target_app, board, revert=True)
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

    def _run(self, case_id: str, baseline_commit: str, target_file: str, operator: str,
              category: str, target_app: str, board: str, revert: bool) -> Dict[str, Any]:
        """
        跑一次容器：checkout baseline -> west update -> 套用 mutation
        (revert=True 時緊接著再還原) -> west build -t run。
        透過 bind-mount 把 tools/mutate_inject.py 掛進容器，不需要重建 image。
        Runs one container: checkout baseline -> west update -> apply the
        mutation (immediately reverting it too if revert=True) -> west build
        -t run. Bind-mounts tools/mutate_inject.py into the container so no
        image rebuild is needed.
        """
        suffix = "revert" if revert else "mutate"
        container_name = f"inject_{case_id}_{suffix}_{int(time.time() * 1000)}"

        mutate_cmd = f"python3 {MUTATE_SCRIPT_CONTAINER_PATH} /zephyrproject/zephyr/{target_file} {operator}"
        steps = [mutate_cmd]
        if revert:
            steps.append(f"{mutate_cmd} --revert")

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
        # The runtime_crash category targets a ztest suite: the boot banner
        # always appears before any test actually runs, so stopping at it
        # would make it impossible to ever observe a crash that happens
        # during test execution. See QemuOracle.evaluate's
        # wait_for_completion docstring.
        wait_for_completion = (category == "runtime_crash")
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
