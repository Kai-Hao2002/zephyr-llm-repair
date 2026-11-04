"""r3 檢索消融：完整版 / 關閉 Graph RAG / 只保留 Graph RAG 的修復率、配對檢定、分類別、檢索命中。
r3 retrieval ablation: full / no Graph RAG / Graph RAG only — resolve rates, paired tests, per category, retrieval hits."""
import json, os, sys
from collections import Counter
from math import comb
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(REPO, "tools"))
from failure_taxonomy import injected_files, load_pipeline_results  # noqa: E402

CONFIGS = {"full": "eval_runs/v3_25flash_r3", "nograph": "eval_runs/v3_25flash_r3_nograph",
           "graphonly": "eval_runs/v3_25flash_r3_graphonly"}
STAGES = {"main": ("", "dataset/cases/final_dataset_v3.json"), "real": ("real", "dataset/cases/real_bugs_v3.json")}


def mcnemar(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def hits(case, r):
    inj = set(injected_files(case))
    first = r.get("first_retrieval_files")
    any_iter = set(first or [])
    for it in r.get("iteration_log") or []:
        any_iter |= set((it.get("trajectory") or {}).get("retrieved_files") or [])
    rank = next((i + 1 for i, f in enumerate(first or []) if f in inj), None)
    return {"has_retrieval": bool(first), "rank": rank, "any_iter_hit": bool(inj & any_iter)}


out = {}
for stage, (sub, ds) in STAGES.items():
    cases = {c["id"]: c for c in json.load(open(os.path.join(REPO, ds)))}
    res = {k: {r["case_id"]: r for r in load_pipeline_results(os.path.join(REPO, v, sub), "proposed")}
           for k, v in CONFIGS.items()}
    for k in res:
        assert set(res[k]) == set(cases), (stage, k, len(res[k]))
    ok = {k: {cid for cid, r in res[k].items() if r["final_status"] == "resolved"} for k in res}
    o = {"n": len(cases), "resolved": {k: len(v) for k, v in ok.items()}, "pairs": {}, "by_category": {}, "retrieval": {}}
    for a, b in [("full", "nograph"), ("full", "graphonly"), ("nograph", "graphonly")]:
        x, y = len(ok[a] - ok[b]), len(ok[b] - ok[a])
        o["pairs"][f"{a}_vs_{b}"] = {"only_" + a: x, "only_" + b: y, "mcnemar_p": mcnemar(x, y)}
    for cat in sorted({c["category"] for c in cases.values()}):
        ids = {cid for cid, c in cases.items() if c["category"] == cat}
        o["by_category"][cat] = {"n": len(ids), **{k: len(ok[k] & ids) for k in ok}}
    for k in res:
        rows = [dict(hits(cases[cid], r), resolved=cid in ok[k]) for cid, r in res[k].items()]
        w = [x for x in rows if x["has_retrieval"]]
        n = len(w)
        o["retrieval"][k] = {
            "n_with_first_retrieval": n,
            "hit@1": sum(x["rank"] == 1 for x in w) / n if n else None,
            "hit@8": sum(bool(x["rank"]) for x in w) / n if n else None,
            "any_iteration_hit": sum(x["any_iter_hit"] for x in rows) / len(rows),
            "resolved_given_any_hit": [sum(x["resolved"] for x in rows if x["any_iter_hit"]), sum(x["any_iter_hit"] for x in rows)],
            "resolved_given_no_hit": [sum(x["resolved"] for x in rows if not x["any_iter_hit"]), sum(not x["any_iter_hit"] for x in rows)],
        }
    out[stage] = o
json.dump(out, open(os.path.join(os.path.dirname(__file__), "ablation_summary.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
