# tools/sample_repeat_subset.py
"""
第 4 步重複實驗的子集：從主資料集依類別分層抽 40 筆 (各類別按比例，最大餘數法；餘數相同時用亂數決定)。
固定亂數種子，輸出的案例順序與主資料集相同。不呼叫 LLM、不改評測程式。

Subset for the step-4 repeats: 40 cases stratified by category from the main set (proportional allocation,
largest remainder; ties broken by the seeded RNG). Fixed seed; cases keep the main-set order.

Usage: python tools/sample_repeat_subset.py [--n 40] [--seed 20261027] \
           [--dataset dataset/cases/final_dataset_v3.json] [--out dataset/v3/eval/repeat_subset_40.json]
"""
import argparse
import json
import random
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset/cases/final_dataset_v3.json")
    ap.add_argument("--out", default="dataset/v3/eval/repeat_subset_40.json")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261027)
    args = ap.parse_args()
    cases = json.load(open(args.dataset))
    rng = random.Random(args.seed)
    by_cat = defaultdict(list)
    for c in cases:
        by_cat[c["category"]].append(c["id"])
    cats = sorted(by_cat)
    quota = {k: args.n * len(by_cat[k]) / len(cases) for k in cats}
    alloc = {k: int(q) for k, q in quota.items()}
    order = sorted(cats, key=lambda k: (-(quota[k] - alloc[k]), rng.random()))
    for k in order[:args.n - sum(alloc.values())]:
        alloc[k] += 1
    chosen = set()
    for k in cats:
        chosen |= set(rng.sample(sorted(by_cat[k]), alloc[k]))
    subset = [c for c in cases if c["id"] in chosen]
    json.dump(subset, open(args.out, "w"), indent=2, ensure_ascii=False)
    print(f"seed={args.seed} n={len(subset)} alloc={alloc} -> {args.out}")


if __name__ == "__main__":
    main()
