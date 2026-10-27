"""Proposed r2 (e86a76e) vs r3 (93bff1b/ac9ccbc) vs r3 關閉 Graph RAG：Analyzer 關鍵字、檢索、修好的逐類別比較。
Proposed r2 vs r3 vs r3 without Graph RAG: Analyzer keywords, retrieval and resolution by category."""
import json, os, sys
from collections import defaultdict
from math import comb
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(REPO, "tools"))
from failure_taxonomy import injected_files, load_pipeline_results  # noqa: E402

RUNS = {"r2": "eval_runs/v3_25flash_r2", "r3": "eval_runs/v3_25flash_r3", "r3_nograph": "eval_runs/v3_25flash_r3_nograph"}
cases = {c["id"]: c for c in json.load(open(os.path.join(REPO, "dataset/cases/final_dataset_v3.json")))}


def mcnemar(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def features(case, r):
    its = r.get("iteration_log") or []
    kw = [bool(((it.get("trajectory") or {}).get("analyzer_diagnosis") or {}).get("search_keywords")) for it in its]
    files = set()
    for it in its:
        files |= set((it.get("trajectory") or {}).get("retrieved_files") or [])
    first_kw = kw[0] if kw else False
    return {"resolved": r["final_status"] == "resolved", "iters": len(its), "kw_iters": sum(kw),
            "first_kw": first_kw, "any_retrieval": bool(files),
            "any_hit": bool(set(injected_files(case)) & files)}


F = {k: {r["case_id"]: features(cases[r["case_id"]], r) for r in load_pipeline_results(os.path.join(REPO, v), "proposed")}
     for k, v in RUNS.items()}
out = {"by_category": {}, "flips": {}}
cats = sorted({c["category"] for c in cases.values()}) + ["ALL"]
for cat in cats:
    ids = [i for i, c in cases.items() if cat == "ALL" or c["category"] == cat]
    row = {"n": len(ids)}
    for k, f in F.items():
        it = sum(f[i]["iters"] for i in ids)
        row[k] = {"resolved": sum(f[i]["resolved"] for i in ids),
                  "first_iter_keywords": sum(f[i]["first_kw"] for i in ids),
                  "keyword_iter_share": round(sum(f[i]["kw_iters"] for i in ids) / it, 3) if it else None,
                  "any_retrieval": sum(f[i]["any_retrieval"] for i in ids),
                  "any_hit": sum(f[i]["any_hit"] for i in ids)}
    out["by_category"][cat] = row
for a, b in [("r3", "r2"), ("r3_nograph", "r2"), ("r3", "r3_nograph")]:
    ra = {i for i in cases if F[a][i]["resolved"]}
    rb = {i for i in cases if F[b][i]["resolved"]}
    x, y = ra - rb, rb - ra
    # 翻轉的案例：r2 有沒有檢索 / r3 有沒有命中。Flipped cases: retrieval in b / hit in a.
    out["flips"][f"{a}_vs_{b}"] = {
        "only_" + a: len(x), "only_" + b: len(y), "mcnemar_p": mcnemar(len(x), len(y)),
        "only_a_no_retrieval_in_b": sum(not F[b][i]["any_retrieval"] for i in x),
        "only_a_hit_in_a": sum(F[a][i]["any_hit"] for i in x),
        "only_b_hit_in_b": sum(F[b][i]["any_hit"] for i in y),
        "only_b_hit_in_a": sum(F[a][i]["any_hit"] for i in y)}
json.dump(out, open(os.path.join(os.path.dirname(__file__), "r3_vs_r2_proposed.json"), "w"), indent=1)
for cat, row in out["by_category"].items():
    print(f"{cat:14s} n={row['n']:3d} " + "  ".join(
        f"{k}: ok {v['resolved']:2d} kw1 {v['first_iter_keywords']:2d} kw% {v['keyword_iter_share']} ret {v['any_retrieval']:3d} hit {v['any_hit']:2d}"
        for k, v in row.items() if k != "n"))
print(json.dumps(out["flips"], indent=1))
