# tools/gold_patch_check.py
"""
標準答案 (gold patch) 驗證：對每筆 v3 案例走評測時完全相同的路徑——
evaluate.prepare_broken_workspace (拿掉 .git/.orig) -> 註冊受保護檔案 ->
把被注入的檔案換回 broken_commit 上的原始內容 (= 標準答案) ->
core.workflow.evaluate_repair_attempt (還原受保護檔案、QemuOracle、required_pass_test)。
預期每筆都判 resolved；判不出來代表這筆案例在評測端永遠不可能被修好。

Gold-patch check: runs every v3 case through the exact evaluation path (workspace prep,
protected-file registration, evaluate_repair_attempt with required_pass_test) after
replacing the injected files with their original content. Every case should come out
resolved; one that does not can never be graded as repaired.

Usage: venv/bin/python tools/gold_patch_check.py <dataset.json> <out.json> [--workdir DIR] [--jobs 2]
Resumable: cases already in <out.json> are skipped.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _original(commit, path):
    r = subprocess.run(["docker", "run", "--rm", "zephyr-sandbox", "bash", "-c",
                        f"cd /zephyrproject/zephyr && git show {commit}:{path}"],
                       capture_output=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"git show failed for {path}: {r.stderr[-300:]}")
    return r.stdout


def check(case, workdir):
    from evaluate import _normalize_injections, prepare_broken_workspace
    from core.protected_files import register, unregister
    from core.workflow import evaluate_repair_attempt
    t0 = time.time()
    dest = os.path.join(workdir, case["id"])
    rec = {"id": case["id"], "board": case["board"], "category": case["category"]}
    try:
        ws = prepare_broken_workspace(case, dest)
        injs = _normalize_injections(case)
        register(ws, case["target_app"], [i["target_file"] for i in injs], dest + ".protected")
        try:
            for inj in injs:
                with open(os.path.join(ws, inj["target_file"]), "wb") as f:
                    f.write(_original(case["broken_commit"], inj["target_file"]))
            r = evaluate_repair_attempt(ws, case["board"], case["target_app"], case.get("target_test"))
        finally:
            unregister(ws)
        rec.update(status=r["status"], resolved=r["resolved"], log_tail=r["log"][-1500:])
    except Exception as e:
        rec.update(status="infra_error", resolved=False, error=repr(e)[-1500:])
    finally:
        shutil.rmtree(dest, ignore_errors=True)
        shutil.rmtree(dest + ".protected", ignore_errors=True)
    rec["seconds"] = round(time.time() - t0)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset"); ap.add_argument("out")
    ap.add_argument("--workdir", default=os.path.expanduser("~/zephyr-eval-work/v3_gold"))
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args()
    os.makedirs(a.workdir, exist_ok=True)
    cases = json.load(open(a.dataset))
    done = json.load(open(a.out)) if os.path.exists(a.out) else []
    todo = [c for c in cases if c["id"] not in {d["id"] for d in done}]
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(check, c, a.workdir): c for c in todo}
        for f in as_completed(futs):
            rec = f.result()
            done.append(rec)
            json.dump(done, open(a.out, "w"), indent=1, ensure_ascii=False)
            print(f"{rec['id']}: resolved={rec['resolved']} status={rec['status']} ({rec['seconds']}s)", flush=True)
    print(f"GOLD_DONE {sum(d['resolved'] for d in done)}/{len(done)} resolved")


if __name__ == "__main__":
    main()
