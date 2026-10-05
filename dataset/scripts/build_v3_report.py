# dataset/scripts/build_v3_report.py
"""
產生給指導教授看的 v3 資料集報告 (dataset/v3/report.html)。數字全部從資料集與驗證紀錄重新計算。
Builds the advisor-facing v3 dataset report (dataset/v3/report.html); every number is recomputed
from the dataset and validation records.
"""
import collections
import glob
import json

V = "dataset/v3/"
OUT = V + "report.html"
TEMPLATE = "dataset/scripts/v3_report_template.html"


def injections(c):
    return c.get("injections") or [c["injection"]]


def rejection_reason(r):
    c = r.get("checks", {})
    if r.get("infra_error"):
        return "環境錯誤"
    if c.get("app_exists") is False or c.get("files_exist") is False:
        return "該 commit 沒有這個檔案或 app"
    if r.get("mutated_status") == "operator_no_match":
        return "注入點不存在"
    if c.get("reproduces_failure") is False or r.get("mutated_status") == "success":
        return "注入在該板子/commit 沒有效果"
    if c.get("failure_at_target_test") is False:
        return "失敗不在 target_test"
    if r.get("original_status") not in ("success", None):
        return "原始版本本身失敗或無法執行"
    return "其他"


def main():
    d = json.load(open("dataset/cases/final_dataset_v3.json"))
    dates = {l.split()[0]: l.split()[1] for l in open(V + "commit_dates.txt")}
    gold = {r["id"]: r for r in json.load(open(V + "gold/gold_check.json"))}
    stab = {r["id"]: r for r in json.load(open(V + "stability/flaky_check.json"))}
    mod = json.load(open(V + "stability/module_check.json"))
    rej = {}
    for f in glob.glob(V + "validation/*.json"):
        for r in json.load(open(f)):
            if isinstance(r, dict) and r.get("accepted") is False:
                rej[r["id"]] = r
    v2 = json.load(open("dataset/cases/final_dataset.json"))
    cases = []
    for c in d:
        cases.append({
            "id": c["id"], "cat": c["category"], "board": c["board"], "date": dates[c["broken_commit"]],
            "commit": c["broken_commit"][:7], "app": c["target_app"], "src": c.get("source", "v2"),
            "ops": [i["operator"].split(":")[0] for i in injections(c)],
            "files": [i["target_file"] for i in injections(c)], "tt": c["target_test"],
            "fix": c.get("fix_reason"), "gold": gold.get(c["id"], {}).get("resolved"),
            "stable": stab.get(c["id"], {}).get("stable"),
        })
    S = {
        "n": len(d), "n_v2": len(v2),
        "v2_apps": len({c["target_app"] for c in v2}), "v2_commits": len({c["broken_commit"] for c in v2}),
        "v2_qemu": sum(c["board"] != "native_sim" for c in v2),
        "v2_tt": sum(1 for c in v2 if c.get("target_test")),
        "commits": len({c["broken_commit"] for c in d}),
        "date_min": min(dates.values()), "date_max": max(dates.values()),
        "rej": dict(collections.Counter(rejection_reason(r) for r in rej.values())), "n_rej": len(rej),
        "gold_ok": sum(1 for c in cases if c["gold"]), "stab_ok": sum(1 for c in cases if c["stable"]),
        "n_runtime": sum(1 for c in cases if c["cat"] == "runtime_crash"),
        "mod_ok": sum(1 for r in mod if r.get("accepted")), "mod_n": len(mod),
        "fixed": collections.Counter(c["fix"] for c in cases if c["fix"]),
        "ops": collections.Counter(f"{c['cat']}|{o}" for c in cases for o in c["ops"]),
    }
    page = open(TEMPLATE).read().replace("/*DATA*/", "const DATA=" + json.dumps({"S": S, "cases": cases}, ensure_ascii=False) + ";")
    open(OUT, "w").write(page)
    print("wrote", OUT, S["n"], "cases")


if __name__ == "__main__":
    main()
