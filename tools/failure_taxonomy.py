# tools/failure_taxonomy.py
"""
v2 失敗原因自動分類：讀 eval_runs/v2/<p>_results.json 的 iteration_log[].trajectory，
把每一筆沒修好的案例歸到一個主要失敗類別，並附上可檢查的佐證欄位。
純離線分析，不呼叫 LLM、不跑 Docker、不改任何評測程式。

Automatic failure-reason taxonomy for v2: reads iteration_log[].trajectory from
eval_runs/v2/<p>_results.json, assigns every unresolved case one primary failure
category plus the evidence fields behind it. Offline only (no LLM, no Docker).

Primary categories (first matching rule wins):
  llm_hang            model never answered (error_kind=llm_hang)
  apply_failed        no iteration ever applied a patch (hallucinated path / SEARCH mismatch / format)
  spec_reverted       patches applied only to protected test/config files, all restored by the evaluator
  wrong_file          applied edits, but never to an injected file (localization failure)
  right_file_build    edited an injected file, but no such iteration built (incl. StaticCheck failure)
  right_file_runtime  edited an injected file and it built, but the test still crashed/failed
  (resolved cases are kept as failure="resolved" so localization stats can use all rows)
"""
import argparse
import json
import os
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Set

PIPELINES = ["b1", "b2", "b3", "proposed"]
_LOG_PATH_RE = re.compile(r"/zephyrproject/zephyr/([\w\-./]+)")
_PATCH_HEADER_RE = re.compile(r"^([^\n]+?)\s*\n<{4,}\s*SEARCH", re.M)

CATEGORY_ORDER = ["llm_hang", "apply_failed", "spec_reverted", "wrong_file", "right_file_build", "right_file_runtime"]


def injected_files(case: Dict[str, Any]) -> List[str]:
    injs = case.get("injections") or [case["injection"]]
    return sorted({i["target_file"] for i in injs})


def intended_files(patch: Any) -> Set[str]:
    """Files the model *tried* to edit, even if applying failed."""
    if isinstance(patch, dict):
        return {patch.get("filepath", "").strip()} - {""}
    if not isinstance(patch, str):
        return set()
    return {m.strip().strip("`*<> ").strip() for m in _PATCH_HEADER_RE.findall(patch)} - {""}


def log_paths(text: str) -> Set[str]:
    return set(_LOG_PATH_RE.findall(text or ""))


def classify(case: Dict[str, Any], res: Dict[str, Any]) -> Dict[str, Any]:
    inj = set(injected_files(case))
    its = [it for it in res.get("iteration_log", []) if it.get("trajectory")]
    applied_any, restored_any, intended_any, visible_retrieved = set(), set(), set(), set()
    inj_touch_iters, inj_touch_compiled = [], False
    stages = []
    for it in its:
        t = it["trajectory"]
        applied = set(t.get("applied_files") or [])
        restored = set(t.get("restored_files") or [])
        applied_any |= applied
        restored_any |= restored
        intended_any |= intended_files(t.get("patch"))
        visible_retrieved |= set(t.get("retrieved_files") or [])
        stages.append(f"{t.get('stage')}/{t.get('status')}")
        if applied & inj:
            inj_touch_iters.append(it["iteration"])
            if it.get("compiled"):
                inj_touch_compiled = True
    effective = applied_any - restored_any

    # 被注入檔案在模型看得到的上下文裡嗎？(初始 log 路徑 / target_app 底下 / 檢索結果)
    # Was the injected file visible to the model? (path in initial log / under target_app / retrieved)
    in_log = {f for f in inj if f in log_paths(res.get("initial_error_log") or case.get("initial_error_log", ""))}
    in_app = {f for f in inj if f.startswith(case["target_app"].rstrip("/") + "/")}
    in_retr = inj & visible_retrieved
    if res["pipeline"] == "b1":
        visible = set()  # B1 sees only the log text, never file contents
    else:
        visible = in_log | in_app | in_retr

    init_log = res.get("initial_error_log") or ""
    signal_lost = (case["category"] == "runtime_crash" and bool(init_log)
                   and "Fallback Raw Tail" not in init_log)
    if res["final_status"] == "resolved":
        cat = "resolved"
    elif res.get("error_kind") == "llm_hang":
        cat = "llm_hang"
    elif not applied_any:
        cat = "apply_failed"
    elif not (applied_any & inj):
        cat = "spec_reverted" if not effective else "wrong_file"
    elif not inj_touch_compiled:
        cat = "right_file_build"
    else:
        cat = "right_file_runtime"

    apply_errs = " ".join((it["trajectory"].get("apply_error") or "") for it in its)
    tags = []
    if cat == "apply_failed":
        if "Target file '<" in apply_errs:
            tags.append("angle_bracket_path")
        elif "TargetFileNotFound" in apply_errs:
            tags.append("hallucinated_path")
        if "SearchMatchFailed" in apply_errs:
            tags.append("search_mismatch")
        if "FormatError" in apply_errs:
            tags.append("format_error")
    if restored_any:
        tags.append("restored")
    if sum(s == "static_check/static_check_failed" for s in stages) >= 2:
        tags.append("static_check_loop")
    if signal_lost:
        tags.append("crash_signal_lost")
    if len(inj) > 1 and applied_any & inj and not inj <= applied_any:
        tags.append("partial_compound")
    if cat == "apply_failed" and intended_any & inj:
        tags.append("intended_injected")
    if cat in ("wrong_file", "spec_reverted", "apply_failed"):
        tags.append("inj_visible" if visible else "inj_not_visible")
    return {
        "case_id": case["id"],
        "category_dataset": case["category"],
        "board": case["board"],
        "pipeline": res["pipeline"],
        "failure": cat,
        "tags": tags,
        "injected": sorted(inj),
        "applied": sorted(applied_any),
        "restored": sorted(restored_any),
        "intended": sorted(intended_any),
        "inj_in_log": bool(in_log),
        "inj_in_app": bool(in_app),
        "inj_retrieved": bool(in_retr),
        "touched_injected": bool(applied_any & inj),
        "crash_signal_lost": signal_lost,
        "iterations": len(its),
        "stages": stages,
        "error": res.get("error"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="eval_runs/v2")
    ap.add_argument("--dataset", default="dataset/cases/final_dataset.json")
    ap.add_argument("--out", default="eval_runs/v2/analysis/failure_taxonomy.json")
    args = ap.parse_args()
    cases = {c["id"]: c for c in json.load(open(args.dataset))}
    rows = []
    for p in PIPELINES:
        for res in json.load(open(os.path.join(args.runs, f"{p}_results.json"))):
            rows.append(classify(cases[res["case_id"]], res))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(rows, open(args.out, "w"), indent=1, ensure_ascii=False)
    by = defaultdict(Counter)
    for r in rows:
        if r["failure"] != "resolved":
            by[r["pipeline"]][r["failure"]] += 1
    for p in PIPELINES:
        print(p, sum(by[p].values()), {c: by[p][c] for c in CATEGORY_ORDER if by[p][c]})


if __name__ == "__main__":
    main()
