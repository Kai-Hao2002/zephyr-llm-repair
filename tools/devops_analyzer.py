# tools/devops_analyzer.py
"""
DevOps Expert 專用：在 west build 失敗 (真正編譯/連結才發現的失敗，不是
StaticCheck 那種 cppcheck/--cmake-only 就能攔到的) 時，對建置日誌做一次
輕量的分類，標出這比較像是「依賴/Kconfig 衝突」還是其他類型的失敗——見
提案 Methodology 對 DevOps Expert 的描述："parses the resulting log for
dependency or Kconfig conflicts"。

純文字規則比對，不呼叫 LLM：分類結果只是附加在 current_error_log 後面的
一行標註，讓下一次 Analyzer 診斷時多一個現成的線索，不是拿來決定路由
(core/workflow.py 的 devops_node 失敗時一律照現有行為退回 Analyzer 重新
診斷，不因為分類結果而跳過——這是跟使用者確認過的決定，見對應的 commit
說明)。分類抓不準或抓不到 (classify_build_failure 回傳 None) 完全不影響
現有行為，純粹是加分項。

DevOps Expert-specific: when a west build genuinely fails (only caught by
a real full compile/link, not the cheaper cppcheck/--cmake-only StaticCheck
already catches), does a lightweight classification of the build log —
does this look like a dependency/Kconfig conflict, or something else? See
the proposal's Methodology description of the DevOps Expert: "parses the
resulting log for dependency or Kconfig conflicts".

Plain text-pattern matching, no LLM call: the classification is just one
annotation line appended to current_error_log, giving the next Analyzer
diagnosis an extra ready-made clue — it does NOT decide routing
(core/workflow.py's devops_node still always bounces back to Analyzer on
failure, unchanged from before — a decision confirmed with the user, see
the corresponding commit message). A missed or wrong classification
(classify_build_failure returning None) doesn't change existing behavior
at all — this is a pure value-add, not load-bearing logic.
"""
import os
import re
from typing import List, Optional

# 每個 pattern 對應到一個簡短的分類標籤，依序嘗試 (愈前面的愈具體/罕見，
# 排前面才不會被後面較通用的 pattern 搶先命中)。
# Each pattern maps to a short classification tag, tried in order (more
# specific/rare patterns first, so a later, more generic pattern doesn't
# steal the match).
_BUILD_FAILURE_PATTERNS = [
    (re.compile(r"devicetree error", re.IGNORECASE), "devicetree_conflict"),
    (re.compile(r"has direct dependencies|dependency loop|Kconfig:\d+: error"), "kconfig_dependency_conflict"),
    (re.compile(r"CMake Error"), "cmake_configure_error"),
    (re.compile(r"undefined reference to"), "undefined_reference"),
    (re.compile(r"undeclared \(first use in this function\)"), "undeclared_symbol"),
    (re.compile(r"redefinition of|conflicting types for"), "type_conflict"),
    (re.compile(r"No SOURCES given to|No such file or directory"), "missing_source_or_header"),
]


def classify_build_failure(log: str) -> Optional[str]:
    """回傳偵測到的失敗類別標籤，或完全沒認出任何已知樣式時回傳 None。"""
    for pattern, tag in _BUILD_FAILURE_PATTERNS:
        if pattern.search(log):
            return tag
    return None


def annotate_log_with_classification(log: str) -> str:
    """把分類結果 (有的話) 附加在原始日誌後面，供下一次 Analyzer 診斷參考；
    沒認出任何樣式時原封不動回傳原始日誌。"""
    tag = classify_build_failure(log)
    if tag is None:
        return log
    return log + f"\n\n[DevOps Expert analysis: the log pattern looks like a build failure of the \"{tag}\" category.]"


# 出錯的編譯單元 (2026-10-18，Proposed 優化)：ninja 的 "FAILED: ...c.obj" 下一行是編譯指令，其中
# "-c <原始碼>" 就是正在編譯的檔案。LogFilter 只保留錯誤行，錯誤若在標頭檔 (例如測試檔少了 '}'，gcc 在之後
# include 的 ztest_test.h 報錯)，模型就看不到真正被編譯的檔案。v3 r2：Proposed 有 6 筆 c_syntax 因此失敗。
# Failing compilation units (2026-10-18, Proposed optimization): the line after ninja's "FAILED: ...c.obj"
# is the compile command, whose "-c <source>" is the file being compiled. LogFilter keeps only error lines,
# so when the error is reported in a header (e.g. a test file missing '}' makes gcc complain inside the
# later-included ztest_test.h) the model never sees which file was being compiled. v3 r2: 6 Proposed
# c_syntax failures.
_FAILED_OBJ_RE = re.compile(r"^FAILED: \S+\.(?:c|cc|cpp|S)\.obj\b")
_COMPILE_SRC_RE = re.compile(r"\s-c\s+(\S+\.(?:c|cc|cpp|S))(?:\s|$)")
_MAX_FAILED_UNITS = 5
# 開關，預設關閉 (2026-10-18 使用者決定)：這項是看了 r2 的失敗案例才設計的，不放進 Proposed 的正式版本，
# 只保留作為事後分析；run_meta 會記錄。
# Switch, off by default (user decision 2026-10-18): it was designed after looking at r2's failed cases, so it
# is not part of the official Proposed; kept only for post-hoc analysis; recorded in run_meta.
FAILED_UNIT_HINT = os.environ.get("ZEPHYR_FAILED_UNIT_HINT", "0").strip().lower() in ("1", "true", "yes")


def extract_failed_units(raw_log: str) -> List[str]:
    """原始建置 log 裡 ninja 編譯失敗的原始碼檔 (相對 Zephyr 根目錄，去重、保留順序，最多 5 個)。
    Source files whose compilation ninja reported as FAILED (Zephyr-root-relative, deduplicated, in order, max 5)."""
    units: List[str] = []
    lines = (raw_log or "").splitlines()
    for i, line in enumerate(lines):
        if not _FAILED_OBJ_RE.match(line.strip()):
            continue
        for nxt in lines[i + 1:i + 3]:
            m = _COMPILE_SRC_RE.search(nxt)
            if m:
                path = m.group(1).replace("/zephyrproject/zephyr/", "", 1)
                if path not in units:
                    units.append(path)
                break
        if len(units) >= _MAX_FAILED_UNITS:
            break
    return units


def failed_units_note(units: List[str]) -> str:
    """給 Analyzer/Patch Expert 的一段說明；沒有時回傳空字串。A note for Analyzer/Patch Expert, or ''."""
    if not units:
        return ""
    return ("[DevOps Expert: compilation failed while compiling these source files (from the ninja build "
            "output); errors reported inside headers were raised while compiling them:\n"
            + "\n".join(f"- /zephyrproject/zephyr/{u}" for u in units) + "]\n\n")
