# dataset/scripts/assemble_real_v3.py
"""
把通過 tools/validate_v3_cases.py 的真實 bug 對照案例組成 dataset/cases/real_bugs_v3.json。
這是主資料集 (final_dataset_v3.json，100% 合成注入) 之外的外部效度對照集，不併入主資料集。

每筆案例：broken_commit = 上游的修正 commit F；注入 = restore_blob 把 F 修改的原始碼換回 F^ 的內容
(測試維持 F 的版本，含回歸測試)；標準答案 = F 的原始碼。initial_error_log 用目前的 LogFilter 從
原始 log 重新壓縮。

Assembles the accepted real-bug control cases into dataset/cases/real_bugs_v3.json, a
separate external-validity control set (not merged into the 100%-injected main dataset).

Usage: venv/bin/python dataset/scripts/assemble_real_v3.py [--out dataset/cases/real_bugs_v3.json]
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.log_filter import LogFilter  # noqa: E402

V = "dataset/v3/"
CANDIDATE_FILE = V + "candidates/real_bug_candidates.json"
VALIDATION_FILE = V + "validation/real_bug_validation.json"
LOG_DIR = os.path.expanduser("~/zephyr-eval-work/v3_real/logs")
KEEP = ("id", "category", "broken_commit", "fixed_commit", "target_app", "board", "injections", "source", "upstream")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dataset/cases/real_bugs_v3.json")
    a = ap.parse_args()
    cands = {c["id"]: c for c in json.load(open(CANDIDATE_FILE))}
    out, problems = [], []
    for r in json.load(open(VALIDATION_FILE)):
        if not r.get("accepted"):
            continue
        c = cands[r["id"]]
        p = os.path.join(LOG_DIR, f"{r['id']}.mutated.log")
        if not os.path.exists(p):
            problems.append(f"{r['id']}: raw mutated log missing")
            continue
        x = {k: c[k] for k in KEEP if k in c}
        x["target_test"] = r["target_test"]
        x["error_type"] = r["mutated_status"]
        x["initial_error_log"] = LogFilter().compress_log(open(p).read())
        x["title"] = f"[v3 real] {c['upstream']['subject']}"
        x["validation"] = {k: r.get(k) for k in ("checks", "notes", "mutated_status", "original_status", "failing_test")}
        out.append(x)
    out.sort(key=lambda x: x["id"])

    apps = collections.Counter(x["target_app"] for x in out)
    problems += [f"app used {n}x: {a_}" for a_, n in apps.items() if n > 1]
    problems += [f"{x['id']}: target_test != failing_test" for x in out
                 if x["validation"]["failing_test"] and x["validation"]["failing_test"] != x["target_test"]]
    json.dump(out, open(a.out, "w"), indent=1, ensure_ascii=False)
    print(f"cases: {len(out)}  boards: {dict(collections.Counter(x['board'] for x in out))}")
    print(f"error_type: {dict(collections.Counter(x['error_type'] for x in out))}")
    print("PROBLEMS:" if problems else "no problems", *problems, sep="\n  ")


if __name__ == "__main__":
    main()
