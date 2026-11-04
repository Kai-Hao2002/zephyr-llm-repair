"""優化版 Proposed (2.5 Flash) 與 r3 Proposed 的配對比較 (2026-11-03)，方法同 r3_macro_sensitivity.py。
Optimized Proposed on 2.5 Flash vs r3 Proposed (no-graph ablation and full), same methods as r3_macro_sensitivity.py.

- 整體：exact McNemar，Holm 校正。
- 五類等權：分層 sign-flip 置換，CI 用類別內 bootstrap。
- 敏感度：runtime 從 63 筆隨機抽 19 筆 (n=95)。
- 真實 bug 13 筆：exact McNemar。
- 只看評測判定，優化版沒有人工標記，所以不做 strict。

輸出 / outputs: opt_vs_r3.json, opt_vs_r3_stdout.txt (由呼叫者重新導向 / redirect)."""
import json, os, sys
from math import comb
import numpy as np
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
import analyze_v3 as A  # noqa: E402
from failure_taxonomy import load_pipeline_results  # noqa: E402

SEED = 20261103
PERM, BOOT, DRAWS = 200000, 20000, 20000
SRC = {"opt": "eval_runs/v4_25flash_opt", "r3_nograph": "eval_runs/v3_25flash_r3_nograph", "r3_full": "eval_runs/v3_25flash_r3"}
PAIRS = [("opt", "r3_nograph"), ("opt", "r3_full"), ("r3_nograph", "r3_full")]
CATS = ["runtime_crash", "c_syntax", "kconfig", "dts", "compound"]
PRICE_IN, PRICE_OUT = 0.30, 2.50  # gemini-2.5-flash 每百萬 token 美元 (未依帳單核對) / USD per 1M tokens, not checked against billing

cases = json.load(open("dataset/cases/final_dataset_v3.json"))
ids = [c["id"] for c in cases]
cat = {c["id"]: c["category"] for c in cases}
cidx = {c: np.array([k for k, i in enumerate(ids) if cat[i] == c]) for c in CATS}


def load(root, sub=""):
    return {r["case_id"]: r for r in load_pipeline_results(os.path.join(root, sub), "proposed")}


def mcnemar(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def macro(x, idx=cidx):
    return float(np.mean([x[v].mean() for v in idx.values()]))


def strat_test(d, idx, rng):
    w = np.zeros(len(d))
    for v in idx.values():
        w[v] = 1 / (len(idx) * len(v))
    obs = float((w * d).sum())
    nz = np.flatnonzero(d)
    flips = rng.integers(0, 2, size=(PERM, len(nz)), dtype=np.int8) * 2 - 1
    stats = flips @ (w[nz] * d[nz])
    p = (1 + int((np.abs(stats) >= abs(obs) - 1e-12).sum())) / (PERM + 1)
    boots = np.zeros(BOOT)
    for v in idx.values():
        s = rng.integers(0, len(v), size=(BOOT, len(v)))
        boots += d[v][s].mean(axis=1) / len(idx)
    return obs, [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))], p


def usage(r):
    tin = tout = calls = 0
    for it in r.get("iteration_log") or []:
        for u in it.get("token_usage") or []:
            tin += u.get("input_tokens", 0)
            tout += u.get("output_tokens", 0)
            calls += 1
    return tin, tout, calls


rng = np.random.default_rng(SEED)
res = {k: load(v) for k, v in SRC.items()}
for k, r in res.items():
    missing = [i for i in ids if i not in r]
    errors = [i for i in ids if i in r and r[i]["final_status"] == "error"]
    print(f"{k}: {len(r)} results, missing {len(missing)}, error {len(errors)} {errors[:5]}")
y = {k: np.array([res[k][i]["final_status"] == "resolved" for i in ids], dtype=float) for k in SRC}
out = {"seed": SEED, "table": {}, "pairs": {}}

print(f"\n=== main set ({len(ids)})")
print(f"{'':<12}{'overall':>9}{'macro':>8}   " + "  ".join(f"{c[:8]:>8}" for c in CATS) + "   iters   in Mtok  out Mtok  calls  est $")
for k in SRC:
    u = np.array([usage(res[k][i]) for i in ids])
    iters = np.mean([len(res[k][i].get("iteration_log") or []) for i in ids])
    t = {"resolved": int(y[k].sum()), "overall": float(y[k].mean()), "macro": macro(y[k]),
         "by_category": {c: int(y[k][v].sum()) for c, v in cidx.items()}, "mean_iterations": float(iters),
         "input_tokens": int(u[:, 0].sum()), "output_tokens": int(u[:, 1].sum()), "llm_calls": int(u[:, 2].sum()),
         "est_usd": float(u[:, 0].sum() / 1e6 * PRICE_IN + u[:, 1].sum() / 1e6 * PRICE_OUT)}
    out["table"][k] = t
    print(f"{k:<12}{100 * t['overall']:>8.1f}%{100 * t['macro']:>7.1f}%   " + "  ".join(f"{t['by_category'][c]:>8}" for c in CATS)
          + f"   {iters:5.2f} {t['input_tokens'] / 1e6:9.2f} {t['output_tokens'] / 1e6:9.2f} {t['llm_calls']:6} {t['est_usd']:6.1f}")

po, pm = {}, {}
for a, b in PAIRS:
    k = f"{a} vs {b}"
    x, z = y[a], y[b]
    oa, ob = int(((x == 1) & (z == 0)).sum()), int(((x == 0) & (z == 1)).sum())
    po[k] = mcnemar(oa, ob)
    m, ci, p = strat_test(x - z, cidx, rng)
    pm[k] = p
    out["pairs"][k] = dict(only_a=oa, only_b=ob, overall_diff=float((x - z).mean()), macro_diff=m, macro_ci95=ci,
                           by_category={c: [int(((x[v] == 1) & (z[v] == 0)).sum()), int(((x[v] == 0) & (z[v] == 1)).sum())] for c, v in cidx.items()})
ho, hm = A.holm(po), A.holm(pm)
print("\npair                        only a:b  overall   p      holm  |  macro [95% CI]            p        holm")
for k, r in out["pairs"].items():
    r.update(overall_p=po[k], overall_holm=ho[k], macro_p=pm[k], macro_holm=hm[k])
    print(f"{k:<28}{r['only_a']:>3}:{r['only_b']:<3}{100 * r['overall_diff']:>+8.1f} {po[k]:>8.2g} {ho[k]:>7.2g}  |"
          f"{100 * r['macro_diff']:>+6.1f} [{100 * r['macro_ci95'][0]:>+6.1f},{100 * r['macro_ci95'][1]:>+6.1f}] {pm[k]:>9.2g} {hm[k]:>7.2g}")
    print(f"{'':<28}by category only a:b " + "  ".join(f"{c[:8]} {v[0]}:{v[1]}" for c, v in r["by_category"].items()))

rt = cidx["runtime_crash"]
other = np.concatenate([v for c, v in cidx.items() if c != "runtime_crash"])
picks = np.array([rng.choice(rt, 19, replace=False) for _ in range(DRAWS)])
print(f"\n=== runtime downsampled to 19, n=95, {DRAWS} draws")
out["downsample"] = {}
for a, b in PAIRS[:2]:
    x, z = y[a], y[b]
    diff = 100 * ((x[other].sum() + x[picks].sum(axis=1)) - (z[other].sum() + z[picks].sum(axis=1))) / 95
    oa = ((x[other] == 1) & (z[other] == 0)).sum() + ((x[picks] == 1) & (z[picks] == 0)).sum(axis=1)
    ob = ((x[other] == 0) & (z[other] == 1)).sum() + ((x[picks] == 0) & (z[picks] == 1)).sum(axis=1)
    cache = {}
    ps = np.array([cache.setdefault((u, v), mcnemar(int(u), int(v))) for u, v in zip(oa, ob)])
    q = [float(np.percentile(diff, s)) for s in (2.5, 50, 97.5)]
    o = dict(median_diff=q[1], p2_5=q[0], p97_5=q[2], share_a_ahead=float((diff > 0).mean()), share_tie=float((diff == 0).mean()),
             share_b_ahead=float((diff < 0).mean()), share_p_lt_05=float((ps < .05).mean()))
    out["downsample"][f"{a} vs {b}"] = o
    print(f"{a + ' vs ' + b:<28} median {q[1]:+5.1f} [{q[0]:+5.1f},{q[2]:+5.1f}]  a ahead {100 * o['share_a_ahead']:5.1f}%  tie {100 * o['share_tie']:4.1f}%"
          f"  b ahead {100 * o['share_b_ahead']:5.1f}%  raw p<.05 in {100 * o['share_p_lt_05']:5.1f}% of draws")

real_ids = [c["id"] for c in json.load(open("dataset/cases/real_bugs_v3.json"))]
real = {k: load(v, "real") for k, v in SRC.items()}
yr = {k: np.array([real[k].get(i, {}).get("final_status") == "resolved" for i in real_ids], dtype=float) for k in SRC}
print(f"\n=== real bugs ({len(real_ids)})")
out["real"] = {"table": {k: int(v.sum()) for k, v in yr.items()}, "pairs": {}}
for k in SRC:
    print(f"{k:<12}{int(yr[k].sum()):>3}/{len(real_ids)}  results {len(real[k])}  error {sum(r['final_status'] == 'error' for r in real[k].values())}")
for a, b in PAIRS[:2]:
    oa, ob = int(((yr[a] == 1) & (yr[b] == 0)).sum()), int(((yr[a] == 0) & (yr[b] == 1)).sum())
    out["real"]["pairs"][f"{a} vs {b}"] = dict(only_a=oa, only_b=ob, p=mcnemar(oa, ob))
    print(f"{a + ' vs ' + b:<28}only a:b {oa}:{ob}  p {mcnemar(oa, ob):.2g}")

json.dump(out, open("eval_runs/v4_25flash_opt/analysis/opt_vs_r3.json", "w"), indent=1)
