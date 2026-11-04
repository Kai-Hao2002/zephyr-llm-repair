# core/agentless_pipeline.py
"""
Agentless baseline (2026-10-18；Xia et al. 2024 的三階段，針對 Zephyr 的 C/Kconfig/DTS 調整)。

1. 分層定位：目錄 (Zephyr 樹前兩層) → 檔案 (所選目錄底下的原始碼/設定檔 + target_app + log 提到的檔案)
   → 元素 (所選檔案的骨架：C 的函式/巨集/ZTEST、Kconfig 的 config、DTS 的節點，附行號)。原版用 Python AST，
   這裡用不依賴案例的文字規則。
2. 修補：只給所選位置附近的程式碼，產生 N 個候選 patch (N=4；第 1 個 greedy，其餘 temperature 0.8)，
   格式與 B2/B3 相同 (SEARCH/REPLACE + 規格規則說明)。
3. 驗證：依序套用、build、跑測試，第一個通過的就算修好；每個候選試完都把它改過的檔案還原，和前面重複的
   候選直接跳過。原版用自動產生的重現測試挑選；在這個評測設定裡失敗的測試對所有 pipeline 都看得到，
   所以用真實的 build/測試結果 (論文需說明)。評分同其他 pipeline (evaluate_repair_attempt)。

Agentless baseline (2026-10-18; the three phases of Xia et al. 2024, adapted to Zephyr's C/Kconfig/DTS):
hierarchical localization (directories -> files -> elements via case-independent text rules instead of the
Python AST), repair (N=4 candidates on the localized code only; greedy + temperature 0.8 samples; same
SEARCH/REPLACE format and spec-rule note as B2/B3), validation (apply, build and test candidates in order; the
first that passes counts; each candidate's edits are reverted afterwards; duplicates are skipped). The original
selects with generated reproduction tests; here the failing test is visible to every pipeline, so the real
build/test result is used. Graded like every other pipeline.
"""
import os
import re
from typing import Any, Dict, List, Tuple

from agents.agentless import generate_candidate, localize_directories, localize_elements, localize_files
from core.protected_files import pop_restored
from core.trajectory import build_trajectory
from core.workflow import evaluate_repair_attempt
from tools.patch_applier import PatchApplier

N_CANDIDATES = int(os.environ.get("ZEPHYR_AGENTLESS_N", "4"))
_SKIP_DIRS = {".git", ".cache", "__pycache__", "build", "doc"}
_FILE_EXT = (".c", ".h", ".cpp", ".S", ".dts", ".dtsi", ".overlay", ".conf", ".yaml", ".ld", ".cmake")
_FILE_NAMES = ("CMakeLists.txt",)
_MAX_CANDIDATE_FILES = 2000
_MAX_FILES_PER_DIR = 400
_MAX_CONTEXT_CHARS = 60_000
_WINDOW = 15
_SMALL_FILE_LINES = 200
_LOG_PATH_RE = re.compile(r"(?:/zephyrproject/zephyr/|WEST_TOPDIR/zephyr/)([\w./\-+]+)")


def _is_candidate(name: str) -> bool:
    return name.endswith(_FILE_EXT) or name in _FILE_NAMES or name.startswith("Kconfig")


def repo_tree(ws: str, depth: int = 2) -> Tuple[str, List[str]]:
    dirs = []
    for dirpath, subdirs, _files in os.walk(ws):
        rel = os.path.relpath(dirpath, ws)
        level = 0 if rel == "." else rel.count(os.sep) + 1
        subdirs[:] = sorted(d for d in subdirs if not d.startswith(".") and d not in _SKIP_DIRS)
        if level >= depth:
            subdirs[:] = []
        if rel != ".":
            dirs.append(rel)
    return "\n".join(d + "/" for d in sorted(dirs)), sorted(dirs)


def candidate_files(ws: str, dirs: List[str], target_app: str, error_log: str) -> List[str]:
    out: List[str] = []

    def add(p):
        if p not in out and os.path.isfile(os.path.join(ws, p)):
            out.append(p)
    for p in _LOG_PATH_RE.findall(error_log or ""):
        add(p)
    # 每個目錄最多 _MAX_FILES_PER_DIR 個，讓排在後面的大目錄也列得到檔案。
    # At most _MAX_FILES_PER_DIR per directory, so large directories listed later still get files in.
    for root in [target_app] + dirs:
        count = 0
        for dirpath, subdirs, files in os.walk(os.path.join(ws, root)):
            subdirs[:] = sorted(d for d in subdirs if not d.startswith(".") and d not in _SKIP_DIRS)
            for f in sorted(files):
                if _is_candidate(f) and count < _MAX_FILES_PER_DIR:
                    before = len(out)
                    add(os.path.relpath(os.path.join(dirpath, f), ws))
                    count += len(out) - before
            if len(out) >= _MAX_CANDIDATE_FILES:
                return out[:_MAX_CANDIDATE_FILES]
    return out


_C_DEF_RE = re.compile(r"^(?:[A-Za-z_][\w\s\*]*\b[\w]+\s*\([^;]*|#\s*define\s+\w+|(?:static\s+)?(?:const\s+)?struct\s+\w+[^;]*\{|"
                       r"(?:ZTEST\w*|DEVICE\w*_DEFINE|K_\w+_DEFINE|SYS_INIT\w*|BUILD_ASSERT)\s*\()")
_KCONFIG_RE = re.compile(r"^\s*(?:menuconfig|config|choice|menu|if|source|rsource|osource|orsource)\b")
_DTS_RE = re.compile(r"\{\s*$|^\s*#include|^\s*/(?:delete-node|include)/")


def skeleton(ws: str, path: str, max_lines: int = 150) -> str:
    try:
        lines = open(os.path.join(ws, path), encoding="utf-8", errors="ignore").read().splitlines()
    except OSError:
        return ""
    name = os.path.basename(path)
    if name.endswith((".c", ".h", ".cpp", ".S")):
        keep = [i for i, ln in enumerate(lines) if _C_DEF_RE.match(ln)]
    elif name.startswith("Kconfig"):
        keep = [i for i, ln in enumerate(lines) if _KCONFIG_RE.match(ln)]
    elif name.endswith((".dts", ".dtsi", ".overlay")):
        keep = [i for i, ln in enumerate(lines) if _DTS_RE.search(ln)]
    else:  # .conf, CMakeLists.txt, yaml...: small files are shown whole
        keep = list(range(len(lines)))
    shown = [f"{i + 1}: {lines[i].rstrip()}" for i in keep[:max_lines]]
    if len(keep) > max_lines:
        shown.append(f"... ({len(keep) - max_lines} more)")
    return f"### {path} ({len(lines)} lines)\n" + "\n".join(shown)


def code_context(ws: str, files: List[str], locs: Dict[str, List[Tuple[int, int]]]) -> str:
    parts, total = [], 0
    for path in files:
        try:
            lines = open(os.path.join(ws, path), encoding="utf-8", errors="ignore").read().splitlines()
        except OSError:
            continue
        spans = sorted((max(1, a - _WINDOW), min(len(lines), b + _WINDOW)) for a, b in locs.get(path, []))
        if not spans:
            if len(lines) > _SMALL_FILE_LINES:
                continue
            spans = [(1, len(lines))]
        merged: List[List[int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            block = f"### {path} (from line {a})\n" + "\n".join(lines[a - 1:b])
            if total + len(block) > _MAX_CONTEXT_CHARS:
                return "\n\n".join(parts)
            parts.append(block)
            total += len(block)
    return "\n\n".join(parts)


def _norm(patch: str) -> str:
    return re.sub(r"\s+", " ", patch or "").strip()


def run_agentless(state: Dict[str, Any]) -> Dict[str, Any]:
    ws, app, log = state["workspace_path"], state["target_app"], state.get("current_error_log", "")
    usage: List[Dict] = []
    tree, all_dirs = repo_tree(ws)
    dirs, u = localize_directories(log, tree, all_dirs)
    usage.append(u)
    files_all = candidate_files(ws, dirs, app, log)
    files, u = localize_files(log, files_all)
    usage.append(u)
    skel = "\n\n".join(s for s in (skeleton(ws, f) for f in files) if s)
    locs, u = localize_elements(log, skel, files)
    usage.append(u)
    context = code_context(ws, files, locs)
    print(f"\n🧪 [Agentless] dirs={dirs} files={files} locations={ {k: v for k, v in locs.items()} }")

    iteration_log, seen, duplicates = [], set(), 0
    final_status, error_type = "failed_max_retries", "unknown"
    for idx in range(1, N_CANDIDATES + 1):
        patch, cand_usage = generate_candidate(log, context, idx)
        entry_usage = (usage if idx == 1 else []) + [cand_usage]
        if _norm(patch) in seen:
            duplicates += 1
            iteration_log.append({"iteration": idx, "compiled": False, "resolved": False, "tool_invocation_error": False,
                                  "token_usage": entry_usage,
                                  "trajectory": build_trajectory("apply", "duplicate_skipped", patch=patch)})
            continue
        seen.add(_norm(patch))
        applier = PatchApplier(workspace_path=ws)
        result = applier.apply_patches(patch)
        originals = result.get("original_contents", {})
        if not result["success"]:
            error_type = "patch_format_error"
            iteration_log.append({"iteration": idx, "compiled": False, "resolved": False, "tool_invocation_error": True,
                                  "token_usage": entry_usage,
                                  "trajectory": build_trajectory("apply", "patch_format_error", patch=patch,
                                                                 applied_files=result.get("applied_files", []),
                                                                 apply_error=result["error"])})
        else:
            ev = evaluate_repair_attempt(ws, state["board"], app, state.get("required_pass_test"))
            iteration_log.append({"iteration": idx, "compiled": ev["compiled"], "resolved": ev["resolved"],
                                  "tool_invocation_error": False, "token_usage": entry_usage,
                                  "trajectory": build_trajectory("run" if ev["compiled"] else "build", ev["status"],
                                                                 patch=patch, applied_files=result["applied_files"],
                                                                 filtered_log=ev["log"],
                                                                 restored_files=pop_restored(ws))})
            error_type = ev["status"]
            if ev["resolved"]:
                final_status = "resolved"
                break
        # 這個候選沒通過：把它改過的檔案還原，下一個候選從同一個起點開始。
        # This candidate failed: revert the files it changed so the next one starts from the same state.
        for rel, content in originals.items():
            with open(os.path.join(ws, rel), "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
    return {
        "final_status": final_status, "error_type": error_type,
        "iterations": sum(1 for e in iteration_log if e["trajectory"]["status"] != "duplicate_skipped"),
        "iteration_log": iteration_log, "first_retrieval_files": None,
        "agentless": {"directories": dirs, "candidate_file_count": len(files_all), "files": files,
                      "locations": {k: v for k, v in locs.items()}, "context_chars": len(context),
                      "n_candidates": N_CANDIDATES, "duplicates_skipped": duplicates},
    }
