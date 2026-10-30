"""r3 第 4 步重複實驗：40 筆分層子集上 r3 / rep1 / rep2 三次的執行間變動。
r3 step-4 repeats: run-to-run variance over r3 / rep1 / rep2 on the 40-case stratified subset.

輸出 / outputs: repeat_variance.json, repeat_variance_stdout.txt (由呼叫者重新導向 / redirect)."""
import json, os, random, statistics, sys
from math import comb
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(REPO, "tools"))
from failure_taxonomy import load_pipeline_results  # noqa: E402

PIPELINES = ["proposed", "openhands", "agentless", "b3"]
RUNS = {"r3": "eval_runs/v3_25flash_r3", "rep1": "eval_runs/v3_25flash_r3_rep1", "rep2": "eval_runs/v3_25flash_r3_rep2"}
PAIRS = [("proposed", "openhands"), ("proposed", "agentless"), ("proposed", "b3"),
         ("openhands", "agentless"), ("agentless", "b3")]
SUBSET = os.path.join(REPO, "dataset/v3/eval/repeat_subset_40.json")
MAIN = os.path.join(REPO, "dataset/cases/final_dataset_v3.json")
B = 10000


def mcnemar(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


sub = json.load(open(SUBSET))
ids = [c["id"] for c in sub]
cat = {c["id"]: c["category"] for c in sub}
ok = {}
for p in PIPELINES:
    for r, d in RUNS.items():
        res = {x["case_id"]: x for x in load_pipeline_results(os.path.join(REPO, d), p)}
        missing = [i for i in ids if i not in res]
        assert not missing, (p, r, missing)
        bad = [i for i in ids if res[i]["final_status"] not in ("resolved", "failed_max_retries")]
        assert not bad, (p, r, bad)
        ok[p, r] = {i for i in ids if res[i]["final_status"] == "resolved"}

out = {"n": len(ids), "runs": list(RUNS), "pipelines": {}, "pairs": {}, "by_category": {}}
print(f"Subset: {len(ids)} cases, runs {', '.join(RUNS)}\n")
print("Pipeline     r3  rep1  rep2   mean    sd   3/3  2/3  1/3  0/3  flip  full-set r3 rate")
main_ids = [c["id"] for c in json.load(open(MAIN))]
for p in PIPELINES:
    counts = [len(ok[p, r]) for r in RUNS]
    k = {i: sum(i in ok[p, r] for r in RUNS) for i in ids}
    dist = {n: sum(v == n for v in k.values()) for n in (3, 2, 1, 0)}
    full = {x["case_id"]: x["final_status"] == "resolved" for x in load_pipeline_results(os.path.join(REPO, RUNS["r3"]), p)}
    full_rate = sum(full[i] for i in main_ids) / len(main_ids)
    o = {"resolved": dict(zip(RUNS, counts)), "mean": statistics.mean(counts), "sd": statistics.stdev(counts),
         "always": dist[3], "two": dist[2], "one": dist[1], "never": dist[0],
         "flip_cases": dist[2] + dist[1], "r3_full_set_rate": full_rate, "per_case_k": k}
    out["pipelines"][p] = o
    print(f"{p:<11}{counts[0]:>4}{counts[1]:>6}{counts[2]:>6}{o['mean']:>7.1f}{o['sd']:>6.2f}"
          f"{dist[3]:>6}{dist[2]:>5}{dist[1]:>5}{dist[0]:>5}{o['flip_cases']:>6}   {100 * full_rate:.1f}%"
          f" (subset r3 {100 * counts[0] / len(ids):.1f}%, 3-run mean {100 * o['mean'] / len(ids):.1f}%)")

# 成對比較：每一次各自的 McNemar，以及三次合併後的平均差 (以案例為單位 bootstrap)。
# Pairwise: McNemar per run, and the 3-run mean difference with a case-level bootstrap CI.
print("\nPairwise (x : y = only first / only second solves), per run; pooled = mean of 3 runs, case bootstrap 95% CI")
rng = random.Random(20261030)
for a, b in PAIRS:
    per = {}
    for r in RUNS:
        x, y = len(ok[a, r] - ok[b, r]), len(ok[b, r] - ok[a, r])
        per[r] = {"only_a": x, "only_b": y, "p": mcnemar(x, y)}
    diff = {i: sum((i in ok[a, r]) - (i in ok[b, r]) for r in RUNS) / len(RUNS) for i in ids}
    mean = 100 * sum(diff.values()) / len(ids)
    boots = sorted(100 * sum(diff[rng.choice(ids)] for _ in ids) / len(ids) for _ in range(B))
    lo, hi = boots[int(0.025 * B)], boots[int(0.975 * B) - 1]
    signs = {(o["only_a"] > o["only_b"]) - (o["only_a"] < o["only_b"]) for o in per.values()}
    out["pairs"][f"{a}_vs_{b}"] = {"per_run": per, "mean_diff_pts": mean, "ci95": [lo, hi],
                                   "same_sign_all_runs": len(signs) == 1 and 0 not in signs}
    runs_txt = "   ".join(f"{r} {o['only_a']}:{o['only_b']} (p={o['p']:.2g})" for r, o in per.items())
    print(f"  {a} vs {b}: {runs_txt}   pooled {mean:+.1f} pts [{lo:+.1f}, {hi:+.1f}]")

# 每類別：三次的平均修好笔數。Per category: mean resolved over the 3 runs.
cats = sorted(set(cat.values()))
print("\nPer category, resolved per run (r3/rep1/rep2)")
print(f"{'category':<15}{'n':>3}  " + "  ".join(f"{p:>14}" for p in PIPELINES))
for c in cats:
    cid = [i for i in ids if cat[i] == c]
    row = {p: [len(ok[p, r] & set(cid)) for r in RUNS] for p in PIPELINES}
    out["by_category"][c] = {"n": len(cid), **row}
    print(f"{c:<15}{len(cid):>3}  " + "  ".join(f"{'/'.join(map(str, row[p])):>14}" for p in PIPELINES))

json.dump(out, open(os.path.join(os.path.dirname(__file__), "repeat_variance.json"), "w"), indent=1)
