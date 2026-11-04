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
from collections import Counter
from typing import Any, Dict, List, Optional

_ESCAPE_RE = re.compile(r"([^A-Za-z0-9_./:-])")


def _escape_shell_arg(value: str) -> str:
    return _ESCAPE_RE.sub(r"\\\1", value)


class StaticChecker:
    def __init__(self, cppcheck_timeout: int = 60, cmake_timeout: int = 180):
        self.cppcheck_timeout = cppcheck_timeout
        self.cmake_timeout = cmake_timeout

    # cppcheck 解析不了 Zephyr 的巨集與板子相關的條件編譯時會報這些 (2026-10-13 起忽略)：真正的語法錯誤
    # 完整建置時編譯器一樣會抓到。v3 2.5-flash 那一輪，Proposed 有 14 筆失敗卡在這類誤報上。
    # Reported when cppcheck cannot parse Zephyr macros or board-dependent conditionals (ignored from
    # 2026-10-13); the compiler still catches real syntax errors in the full build. In the v3
    # 2.5-flash run, 14 failed Proposed cases were stuck on these false positives.
    IGNORED_IDS = frozenset({"syntaxError", "unknownMacro", "preprocessorErrorDirective", "internalAstError",
                             "missingInclude", "missingIncludeSystem", "checkersReport"})

    def check(self, workspace_path: str, target_app: str, board: str, applied_files: List[str],
              pre_patch_contents: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        依序跑 cppcheck (只針對這次 patch 實際碰到的 .c 檔) 再跑
        cmake-only；任一項失敗就立刻回傳，不用兩項都跑完。回傳
        {"passed": bool, "log": str}——log 在 passed=True 時是空字串。
        pre_patch_contents 有給時，cppcheck 只回報 patch 之後才出現的問題 (同一檔案、同 id、同訊息，
        不比行號)，原本就有的問題不算 patch 的錯。
        When pre_patch_contents is given, cppcheck only reports findings that appear after the patch
        (same file, id and message; line numbers ignored); pre-existing findings are not the patch's fault.
        """
        c_files = [f for f in applied_files if f.endswith(".c")]
        if c_files:
            cppcheck_result = self._run_cppcheck(workspace_path, c_files, pre_patch_contents or {})
            if not cppcheck_result["passed"]:
                return cppcheck_result

        return self._run_cmake_only(workspace_path, target_app, board)

    # 這些代表 cppcheck 沒能完整分析該檔案 (分析在語法錯誤處停下)。These mean the file was not fully analysed.
    PARSE_FAILURE_IDS = frozenset({"syntaxError", "internalAstError", "preprocessorErrorDirective"})

    def _cppcheck_findings(self, workspace_path: str, c_files: List[str]) -> Optional[List[Dict[str, str]]]:
        """cppcheck 的發現；逾時回傳 None。IGNORED_IDS 的項目保留在結果裡 (id 不變)，由呼叫端過濾。
        Findings, or None on timeout. IGNORED_IDS entries are kept (callers filter them)."""
        container_paths = " ".join(
            _escape_shell_arg(f"/zephyrproject/zephyr/{f}") for f in c_files
        )
        # --suppress=missingInclude 是因為這裡沒有餵給它 Zephyr 完整的 include path，缺標頭檔的假警告
        # 會蓋掉真正的問題。--template 讓輸出可以逐行解析。
        # --suppress=missingInclude because we're not feeding it Zephyr's full include path here, so
        # false "missing header" noise would bury genuine findings. --template makes output parseable.
        template = "{file}\\|{line}\\|{severity}\\|{id}\\|{message}"
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{os.path.abspath(workspace_path)}:/zephyrproject/zephyr:ro",
            "zephyr-sandbox", "bash", "-c",
            f"cppcheck --quiet --suppress=missingInclude --template={template} {container_paths}",
        ]
        try:
            result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=self.cppcheck_timeout)
        except subprocess.TimeoutExpired:
            return None
        findings = []
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            parts = line.split("|", 4)
            if len(parts) != 5:
                continue
            path = parts[0].replace("/zephyrproject/zephyr/", "", 1)
            findings.append({"file": path, "line": parts[1], "severity": parts[2], "id": parts[3], "message": parts[4]})
        return findings

    def _run_cppcheck(self, workspace_path: str, c_files: List[str],
                      pre_patch_contents: Dict[str, str]) -> Dict[str, Any]:
        after_all = self._cppcheck_findings(workspace_path, c_files)
        # StaticCheck 逾時不該卡死整個修復迴圈——它的角色是「提早攔下明顯壞掉的 patch，省一次完整
        # 建置的時間」，不是硬性關卡；逾時就放行，讓真正的 west build -t run 去給出最終判定。
        # A StaticCheck timeout shouldn't stall the repair loop — it's an early filter, not a hard
        # gate; on timeout, let it through and leave the verdict to the actual west build -t run.
        if after_all is None:
            return {"passed": True, "log": ""}
        after = [x for x in after_all if x["id"] not in self.IGNORED_IDS]
        if not after:
            return {"passed": True, "log": ""}

        new = after
        with_pre = [f for f in c_files if f in pre_patch_contents]
        if with_pre:
            # 暫時把 patch 前的內容寫回原位 (同一目錄，相對 include 才會一樣)，跑完立刻換回 patch 後的內容。
            # Temporarily put the pre-patch content back in place (same directory, so relative includes
            # resolve identically), then restore the patched content right after.
            patched = {}
            try:
                for rel in with_pre:
                    path = os.path.join(workspace_path, rel)
                    with open(path, encoding="utf-8") as f:
                        patched[rel] = f.read()
                    with open(path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(pre_patch_contents[rel])
                before_all = self._cppcheck_findings(workspace_path, with_pre)
            finally:
                for rel, content in patched.items():
                    with open(os.path.join(workspace_path, rel), "w", encoding="utf-8", newline="\n") as f:
                        f.write(content)
            if before_all is None:
                return {"passed": True, "log": ""}
            # patch 前就解析失敗的檔案 (例如 c_syntax 案例本身的語法錯誤) 沒有可靠的比較基準：cppcheck 在
            # 錯誤處就停了，原本就有的問題會看起來像新的。這些檔案的發現一律放行，交給完整建置判定。
            # Files that already failed to parse before the patch (e.g. a c_syntax case's own syntax
            # error) have no reliable baseline: cppcheck stopped at the error, so pre-existing findings
            # would look new. Their findings are let through; the full build decides.
            unparsed_before = {x["file"] for x in before_all if x["id"] in self.PARSE_FAILURE_IDS}
            remaining = Counter((x["file"], x["id"], x["message"]) for x in before_all
                                if x["id"] not in self.IGNORED_IDS)
            new = []
            for x in after:
                key = (x["file"], x["id"], x["message"])
                if x["file"] in unparsed_before:
                    continue
                if remaining[key] > 0:
                    remaining[key] -= 1
                else:
                    new.append(x)

        if not new:
            return {"passed": True, "log": ""}
        lines = "\n".join(f"/zephyrproject/zephyr/{x['file']}:{x['line']}: {x['severity']}: {x['message']} [{x['id']}]"
                          for x in new)
        return {
            "passed": False,
            "log": f"[StaticCheck: cppcheck found new problems introduced by the patch]\n{lines}",
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
            "log": f"[StaticCheck: west build --cmake-only (Kconfig/DTS structure check) failed]\n"
                   f"{result.stdout[-4000:]}\n{result.stderr[-2000:]}",
        }
