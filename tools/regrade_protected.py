"""
對單發 pipeline (B1/B2) 已完成的結果，用受保護檔案規則重新判定 (見
core/protected_files.py)，不需要再呼叫 LLM：在新準備的 workspace 重放當時
的 patch，建置前還原受保護檔案，再跑一次建置/執行判定。

B1/B2 只有一輪、而且不看回饋，所以「重放 + 還原後判定」跟「一開始就有還原
規則」的結果完全相同。只處理 patch 動過受保護檔案的案例，其他案例加了還原
也不會有任何差別。每筆結果寫成 <out_dir>/<case_id>.json，已存在就跳過。

Regrades completed single-shot (B1/B2) results under the protected-file
rule (see core/protected_files.py) without calling the LLM again: replays
the recorded patch on a freshly prepared workspace, restores protected
files before building, and re-runs the build/execute verdict.

B1/B2 have a single iteration and never see feedback, so "replay + restore,
then grade" gives exactly the result the rule would have given from the
start. Only cases whose patch touched protected files are processed; the
rule changes nothing for the others. Each result is written to
<out_dir>/<case_id>.json and skipped if it already exists.

Usage: regrade_protected.py <pipeline results.json> <b1|b2> <out_dir> <work_dir> [--shard i/n] [--case-id ID ...]
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evaluate  # noqa: E402  (loads .env, sets up logging)
from core.protected_files import register, unregister, pop_restored  # noqa: E402
from core.workflow import evaluate_repair_attempt  # noqa: E402
from tools.patch_applier import PatchApplier  # noqa: E402


def _targets(case):
    return [inj["target_file"] for inj in evaluate._normalize_injections(case)]


def touches_protected(case, record) -> bool:
    app = os.path.normpath(case.get("target_app", ".")) + os.sep
    targets = set(_targets(case))
    for entry in record.get("iteration_log") or []:
        for f in entry["trajectory"]["applied_files"] or []:
            if f.startswith(app) and f not in targets:
                return True
    return False


def regrade(case, record, pipeline: str, work_dir: str):
    trajectory = record["iteration_log"][0]["trajectory"]
    dest_dir = os.path.join(work_dir, case["id"])
    workspace = evaluate.prepare_broken_workspace(case, dest_dir)
    register(workspace, case.get("target_app", "."), _targets(case), dest_dir + ".protected")
    try:
        applier = PatchApplier(workspace_path=workspace)
        if pipeline == "b1":
            patch = trajectory["patch"]
            applied = applier.apply_full_file(patch["filepath"], patch["content"])
        else:
            applied = applier.apply_patches(trajectory["patch"])
        if not applied["success"] or sorted(applied["applied_files"]) != sorted(trajectory["applied_files"]):
            raise RuntimeError(f"replay did not reproduce the recorded application: {applied}")
        result = evaluate_repair_attempt(workspace, case.get("board", "qemu_x86"), case.get("target_app", "."),
                                         case.get("target_test"))
        return {
            "case_id": case["id"], "pipeline": pipeline,
            "original_final_status": record["final_status"], "original_status": trajectory["status"],
            "regraded_final_status": "resolved" if result["resolved"] else "failed_max_retries",
            "regraded_status": result["status"], "regraded_compiled": result["compiled"],
            "restored_files": pop_restored(workspace), "regraded_log": result["log"][-4000:],
        }
    finally:
        unregister(workspace)
        shutil.rmtree(dest_dir, ignore_errors=True)
        shutil.rmtree(dest_dir + ".protected", ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results")
    parser.add_argument("pipeline", choices=("b1", "b2"))
    parser.add_argument("out_dir")
    parser.add_argument("work_dir")
    parser.add_argument("--shard", default="0/1")
    parser.add_argument("--case-id", action="append", help="Only regrade these cases (for piloting).")
    args = parser.parse_args()
    shard, shards = (int(x) for x in args.shard.split("/"))

    cases = {c["id"]: c for c in evaluate.load_dataset(evaluate.DEFAULT_DATASET_PATH)}
    records = json.load(open(args.results))
    todo = [r for r in records if r.get("iteration_log") and touches_protected(cases[r["case_id"]], r)]
    if args.case_id:
        todo = [r for r in todo if r["case_id"] in args.case_id]
    todo = todo[shard::shards]
    os.makedirs(args.out_dir, exist_ok=True)
    print(f"{len(todo)} cases to regrade in shard {shard}/{shards}", flush=True)
    for record in todo:
        out = os.path.join(args.out_dir, record["case_id"] + ".json")
        if os.path.exists(out):
            continue
        result = regrade(cases[record["case_id"]], record, args.pipeline, os.path.join(args.work_dir, f"shard{shard}"))
        result["run_meta"] = evaluate.collect_run_meta()
        json.dump(result, open(out, "w"), indent=2, ensure_ascii=False)
        print(f"{record['case_id']}: {result['original_final_status']} -> {result['regraded_final_status']} "
              f"(restored {len(result['restored_files'])})", flush=True)


if __name__ == "__main__":
    main()
