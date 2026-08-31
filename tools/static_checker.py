# tools/static_checker.py
"""
StaticCheck：在真正跑一次完整 west build -t run (通常要花好幾分鐘) 之前，
先用便宜、快速的靜態分析檔住明顯壞掉的 patch——cppcheck 檢查剛被修補的 C
檔案有沒有明顯的語法/邏輯錯誤，west build --cmake-only 檢查 Kconfig/DTS
是否還能通過設定階段 (不用真的編譯連結，比完整建置快得多)。任何一項沒
過，就不用再等一次完整建置才知道 patch 有問題。

StaticCheck: before spending several minutes on a full `west build -t run`,
run a cheap, fast static-analysis pass to catch an obviously broken patch —
cppcheck for the C files a patch just touched, `west build --cmake-only`
for whether Kconfig/DTS can still get through the configure stage (no
actual compile/link, far cheaper than a full build). Either failing means
we don't have to wait out a full build to learn the patch is broken.
"""
import os
import re
import subprocess
from typing import Any, Dict, List

_ESCAPE_RE = re.compile(r"([^A-Za-z0-9_./:-])")


def _escape_shell_arg(value: str) -> str:
    return _ESCAPE_RE.sub(r"\\\1", value)


class StaticChecker:
    def __init__(self, cppcheck_timeout: int = 60, cmake_timeout: int = 180):
        self.cppcheck_timeout = cppcheck_timeout
        self.cmake_timeout = cmake_timeout

    def check(self, workspace_path: str, target_app: str, board: str, applied_files: List[str]) -> Dict[str, Any]:
        """
        依序跑 cppcheck (只針對這次 patch 實際碰到的 .c 檔) 再跑
        cmake-only；任一項失敗就立刻回傳，不用兩項都跑完。回傳
        {"passed": bool, "log": str}——log 在 passed=True 時是空字串。
        """
        c_files = [f for f in applied_files if f.endswith(".c")]
        if c_files:
            cppcheck_result = self._run_cppcheck(workspace_path, c_files)
            if not cppcheck_result["passed"]:
                return cppcheck_result

        return self._run_cmake_only(workspace_path, target_app, board)

    def _run_cppcheck(self, workspace_path: str, c_files: List[str]) -> Dict[str, Any]:
        container_paths = " ".join(
            _escape_shell_arg(f"/zephyrproject/zephyr/{f}") for f in c_files
        )
        # --error-exitcode=1 讓 cppcheck 找到問題時真的以非 0 結束碼退出
        # (預設就算報錯也是結束碼 0)；--suppress=missingInclude 是因為這裡
        # 沒有餵給它 Zephyr 完整的 include path，缺標頭檔的假警告會蓋掉
        # 真正的問題。
        # --error-exitcode=1 makes cppcheck actually exit non-zero when it
        # finds something (default exit code is 0 even with findings);
        # --suppress=missingInclude because we're not feeding it Zephyr's
        # full include path here, so false "missing header" noise would
        # bury genuine findings.
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{os.path.abspath(workspace_path)}:/zephyrproject/zephyr:ro",
            "zephyr-sandbox", "bash", "-c",
            f"cppcheck --error-exitcode=1 --quiet --suppress=missingInclude {container_paths}",
        ]
        try:
            result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=self.cppcheck_timeout)
        except subprocess.TimeoutExpired:
            # StaticCheck 逾時不該卡死整個修復迴圈——它的角色是「提早攔下
            # 明顯壞掉的 patch，省一次完整建置的時間」，不是硬性關卡；逾時
            # 就放行，讓真正的 west build -t run 去給出最終判定。
            # A StaticCheck timeout shouldn't stall the whole repair loop —
            # its job is "catch an obviously broken patch early, save a
            # full build," not a hard gate; on timeout, let it through and
            # leave the real verdict to the actual west build -t run.
            return {"passed": True, "log": ""}

        if result.returncode == 0:
            return {"passed": True, "log": ""}
        return {
            "passed": False,
            "log": f"[StaticCheck: cppcheck 在剛修補的檔案中發現問題]\n{result.stdout}\n{result.stderr}",
        }

    def _run_cmake_only(self, workspace_path: str, target_app: str, board: str) -> Dict[str, Any]:
        # 掛載必須可寫，理由跟 core/workflow.py 的 build_devops_docker_cmd
        # 完全一樣：CMake 的 toolchain capability 檢查會在 --cmake-only
        # 這個設定階段 (不是 ninja 建置階段) 就把快取寫進
        # <zephyr>/.cache/ToolchainCapabilityDatabase/，掛成唯讀一樣會在
        # 這裡失敗，而且失敗原因跟真正的 Kconfig/DTS 錯誤長得一樣。
        # The mount must be writable, for exactly the same reason as
        # core/workflow.py's build_devops_docker_cmd: CMake's
        # toolchain-capability check writes its cache during the
        # --cmake-only configure stage (not the ninja build stage), into
        # <zephyr>/.cache/ToolchainCapabilityDatabase/ — read-only fails
        # here too, and looks identical to a genuine Kconfig/DTS error.
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{os.path.abspath(workspace_path)}:/zephyrproject/zephyr",
            "-w", f"/zephyrproject/zephyr/{target_app}",
            "zephyr-sandbox", "bash", "-c",
            f"west build -b {board} -d /tmp/staticcheck_build -p always --cmake-only .",
        ]
        try:
            result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=self.cmake_timeout)
        except subprocess.TimeoutExpired:
            return {"passed": True, "log": ""}

        if result.returncode == 0:
            return {"passed": True, "log": ""}
        return {
            "passed": False,
            "log": f"[StaticCheck: west build --cmake-only (Kconfig/DTS 結構檢查) 失敗]\n"
                   f"{result.stdout[-4000:]}\n{result.stderr[-2000:]}",
        }
