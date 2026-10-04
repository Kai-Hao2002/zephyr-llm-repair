# dataset/scripts/assemble_v3.py
"""
把所有通過 tools/validate_v3_cases.py 的 v3 案例組成 dataset/cases/final_dataset_v3.json，
並做資料集層級的稽核：app / 注入檔不重複、每筆有 target_test、類別與板子比例、
每個 commit 的案例數、runtime 類的 initial_error_log 看得到失敗訊息。
initial_error_log 一律用目前的 LogFilter 從原始 log 重新壓縮 (pilot 早期用的是舊版 filter)。

Assembles every v3 case accepted by tools/validate_v3_cases.py into
dataset/cases/final_dataset_v3.json and audits it at dataset level. initial_error_log is
always recompressed from the raw mutated log with the current LogFilter.

Usage: venv/bin/python dataset/scripts/assemble_v3.py [--out dataset/cases/final_dataset_v3.json]
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.log_filter import LogFilter  # noqa: E402

V = "dataset/v3/"
CANDIDATE_FILES = sorted(glob.glob(V + "candidates/*.json"))
VALIDATION_FILES = [V + "validation/pilot_validation.json", V + "validation/pilot_validation_fixed.json",
                    V + "validation/migrated_validation.json", V + "validation/new_validation.json",
                    V + "validation/fix_validation.json", V + "validation/expand_validation.json"]
# 刻意不收的已通過案例 (例如超出類別目標的備用案例)：{"id": "reason"}
# Accepted cases deliberately left out (e.g. spares beyond a category target): {"id": "reason"}
EXCLUDED_FILE = V + "validation/excluded.json"
LOG_DIRS = [os.path.expanduser(p) for p in ("~/zephyr-eval-work/v3_pilot/logs_fixed", "~/zephyr-eval-work/v3_pilot/logs",
                                             "~/zephyr-eval-work/v3_migrate/logs", "~/zephyr-eval-work/v3_new/logs",
                                             "~/zephyr-eval-work/v3_fix/logs", "~/zephyr-eval-work/v3_expand/logs")]
FAIL_MARK = re.compile(r"Assertion failed|FAIL - |ZEPHYR FATAL|Segmentation fault|Fault|Aborted", re.I)
KEEP = ("id", "category", "broken_commit", "fixed_commit", "target_app", "board", "injection", "injections",
        "extra_files", "v2_source", "source", "retry_of", "fix_reason")


def injections(c):
    return c.get("injections") or [c["injection"]]


def raw_log(case_id):
    for d in LOG_DIRS:
        p = os.path.join(d, f"{case_id}.mutated.log")
        if os.path.exists(p):
            return open(p).read()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dataset/cases/final_dataset_v3.json")
    a = ap.parse_args()
    cands = {}
    for f in CANDIDATE_FILES:
        for c in json.load(open(f)):
            cands[c["id"]] = c
    accepted = {}
    for f in VALIDATION_FILES:
        if os.path.exists(f):
            for r in json.load(open(f)):
                if r.get("accepted"):
                    accepted[r["id"]] = r  # later files (fixed re-runs) win
    # 通過驗證的重試/修正版 (retry_of) 取代原案例 / an accepted retry or fix supersedes its original
    superseded = {cands[cid].get("retry_of") for cid in accepted if cands.get(cid, {}).get("retry_of")}
    for cid in superseded & set(accepted):
        del accepted[cid]
    if os.path.exists(EXCLUDED_FILE):
        for cid in json.load(open(EXCLUDED_FILE)):
            accepted.pop(cid, None)
    out, problems = [], []
    for cid, r in sorted(accepted.items()):
        c = cands[cid]
        log = raw_log(cid)
        if log is None:
            problems.append(f"{cid}: raw mutated log missing")
            continue
        x = {k: c[k] for k in KEEP if k in c}
        x["target_test"] = r["target_test"]
        x["error_type"] = r["mutated_status"]
        x["initial_error_log"] = LogFilter().compress_log(log)
        x["title"] = f"[v3] {c['category']}: " + " + ".join(f"{i['operator'].split(':')[0]} on {i['target_file']}" for i in injections(c))
        x["validation"] = {k: r.get(k) for k in ("checks", "notes", "mutated_status", "original_status", "failing_test")}
        out.append(x)

    # 稽核 Audit
    apps = collections.Counter(x["target_app"] for x in out)
    files = collections.Counter(i["target_file"] for x in out for i in injections(x))
    problems += [f"app used {n}x: {a_}" for a_, n in apps.items() if n > 1]
    problems += [f"injected file used {n}x: {f}" for f, n in files.items() if n > 1]
    problems += [f"{x['id']}: no target_test" for x in out if not x["target_test"]]
    problems += [f"{x['id']}: runtime log has no failure marker" for x in out
                 if x["category"] == "runtime_crash" and not FAIL_MARK.search(x["initial_error_log"])]
    cats = collections.Counter(x["category"] for x in out)
    boards = collections.Counter(x["board"] for x in out)
    per_commit = collections.Counter(x["broken_commit"][:7] for x in out)
    qemu = sum(n for b, n in boards.items() if b != "native_sim")
    json.dump(out, open(a.out, "w"), indent=1, ensure_ascii=False)
    print(f"cases: {len(out)}  categories: {dict(cats)}")
    print(f"boards: {dict(boards)}  QEMU share: {qemu / max(1, len(out)):.1%}")
    print(f"commits: {len(per_commit)}  per-commit min/max: {min(per_commit.values())}/{max(per_commit.values())}")
    print(f"sources: {dict(collections.Counter(x.get('source', 'v2') for x in out))}")
    print("PROBLEMS:" if problems else "no problems", *problems, sep="\n  ")


if __name__ == "__main__":
    main()
