"""r3 五類等權平均與 runtime 抽樣敏感度分析，含配對檢定 (2026-10-30)。
Macro (equal-weight five-category) rates and runtime-downsampling sensitivity, with paired tests.

- 整體：exact McNemar，Holm 校正。
- 五類等權：每類各占 1/5 的平均差。檢定用分層 sign-flip 置換 (類別內翻轉每筆案例的配對差)，
  CI 用類別內案例 bootstrap。
- 敏感度：runtime 從 63 筆隨機抽 19 筆 (其他類全留，共 95 筆)，同一組抽樣用在所有 pipeline。
- 重複實驗：40 筆子集、r3/rep1/rep2，以案例的三次平均做同樣的分層檢定。

輸出 / outputs: macro_sensitivity.json, macro_sensitivity_stdout.txt (由呼叫者重新導向 / redirect)."""
import json, os, sys
from math import comb
import numpy as np
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
import analyze_v3 as A  # noqa: E402
from failure_taxonomy import load_pipeline_results  # noqa: E402

SEED = 20261030
PERM, BOOT, DRAWS = 200000, 20000, 20000
P = ["b1", "b2", "b3", "agentless", "openhands", "proposed"]
SRC = {"b1": "eval_runs/v3_25flash_r2", "b2": "eval_runs/v3_25flash_r2"}
PAIRS = [("proposed", "b3"), ("proposed", "agentless"), ("proposed", "openhands"), ("proposed", "b2"), ("proposed", "b1"),
         ("openhands", "agentless"), ("openhands", "b3"), ("agentless", "b3"), ("b3", "b2")]
RPAIRS = [("proposed", "openhands"), ("proposed", "agentless"), ("proposed", "b3"), ("openhands", "agentless"), ("agentless", "b3")]
CATS = ["runtime_crash", "c_syntax", "kconfig", "dts", "compound"]

cases = json.load(open("dataset/cases/final_dataset_v3.json"))
ids = [c["id"] for c in cases]
cat = {c["id"]: c["category"] for c in cases}
labels = json.load(open("eval_runs/v3_25flash_r3/analysis/manual_labels_main.json"))["labels"]
alt = {(p, i) for p, i, lab, *_ in labels if lab == "alternative"}
res = {p: {r["case_id"]: r for r in load_pipeline_results(SRC.get(p, "eval_runs/v3_25flash_r3"), p)} for p in P}
judged = {p: np.array([res[p][i]["final_status"] == "resolved" for i in ids], dtype=float) for p in P}
strict = {p: np.array([res[p][i]["final_status"] == "resolved" and (p, i) not in alt for i in ids], dtype=float) for p in P}
cidx = {c: np.array([k for k, i in enumerate(ids) if cat[i] == c]) for c in CATS}


def mcnemar(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def macro(x, idx=cidx):
    return float(np.mean([x[v].mean() for v in idx.values()]))


def strat_test(d, idx, rng):
    """分層 sign-flip 置換 + 類別內 bootstrap。回傳平均差、CI、雙尾 p。"""
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


def full_set(name, y, rng):
    print(f"\n=== {name}: main set ({len(ids)})")
    print(f"{'':<11}{'overall':>9}{'macro':>8}   " + "  ".join(f"{c[:8]:>8}" for c in CATS))
    tab = {}
    for p in P:
        tab[p] = {"resolved": int(y[p].sum()), "overall": float(y[p].mean()), "macro": macro(y[p]),
                  "by_category": {c: int(y[p][v].sum()) for c, v in cidx.items()}}
        print(f"{p:<11}{100 * tab[p]['overall']:>8.1f}%{100 * tab[p]['macro']:>7.1f}%   "
              + "  ".join(f"{tab[p]['by_category'][c]:>8}" for c in CATS))
    pm, po, row = {}, {}, {}
    for a, b in PAIRS:
        k = f"{a} vs {b}"
        x, z = y[a], y[b]
        oa, ob = int(((x == 1) & (z == 0)).sum()), int(((x == 0) & (z == 1)).sum())
        po[k] = mcnemar(oa, ob)
        m, ci, p = strat_test(x - z, cidx, rng)
        pm[k] = p
        row[k] = dict(only_a=oa, only_b=ob, overall_diff=float((x - z).mean()), macro_diff=m, macro_ci95=ci)
    ho, hm = A.holm(po), A.holm(pm)
    print("pair                      only a:b  overall   p      holm  |  macro [95% CI]            p        holm")
    for k, r in row.items():
        r.update(overall_p=po[k], overall_holm=ho[k], macro_p=pm[k], macro_holm=hm[k])
        print(f"{k:<26}{r['only_a']:>3}:{r['only_b']:<3}{100 * r['overall_diff']:>+8.1f} {po[k]:>8.2g} {ho[k]:>7.2g}  |"
              f"{100 * r['macro_diff']:>+6.1f} [{100 * r['macro_ci95'][0]:>+6.1f},{100 * r['macro_ci95'][1]:>+6.1f}] {pm[k]:>9.2g} {hm[k]:>7.2g}")
    return {"table": tab, "pairs": row}


def downsample(name, y, rng, strata=None):
    """runtime 抽到 19 筆。strata 有給時依 (test-file, core) 等比例抽。"""
    rt = cidx["runtime_crash"]
    other = np.concatenate([v for c, v in cidx.items() if c != "runtime_crash"])
    picks = np.zeros((DRAWS, 19), dtype=int)
    for t in range(DRAWS):
        if strata is None:
            picks[t] = rng.choice(rt, 19, replace=False)
        else:
            a, b = strata
            na = round(19 * len(a) / (len(a) + len(b)))
            picks[t] = np.concatenate([rng.choice(a, na, replace=False), rng.choice(b, 19 - na, replace=False)])
    print(f"\n=== {name}: runtime downsampled to 19, n=95, {DRAWS} draws" + (" (stratified by location)" if strata else ""))
    out = {}
    for a, b in PAIRS[:3] + PAIRS[5:6]:
        x, z = y[a], y[b]
        fa, fb = x[other].sum() + x[picks].sum(axis=1), z[other].sum() + z[picks].sum(axis=1)
        diff = 100 * (fa - fb) / 95
        oa = ((x[other] == 1) & (z[other] == 0)).sum() + ((x[picks] == 1) & (z[picks] == 0)).sum(axis=1)
        ob = ((x[other] == 0) & (z[other] == 1)).sum() + ((x[picks] == 0) & (z[picks] == 1)).sum(axis=1)
        cache = {}
        ps = np.array([cache.setdefault((u, v), mcnemar(int(u), int(v))) for u, v in zip(oa, ob)])
        q = [float(np.percentile(diff, s)) for s in (2.5, 50, 97.5)]
        o = dict(median_diff=q[1], p2_5=q[0], p97_5=q[2], share_a_ahead=float((diff > 0).mean()),
                 share_tie=float((diff == 0).mean()), share_b_ahead=float((diff < 0).mean()), share_p_lt_05=float((ps < .05).mean()))
        out[f"{a} vs {b}"] = o
        print(f"{a + ' vs ' + b:<26} median {q[1]:+5.1f} [{q[0]:+5.1f},{q[2]:+5.1f}]  a ahead {100 * o['share_a_ahead']:5.1f}%  tie {100 * o['share_tie']:4.1f}%"
              f"  b ahead {100 * o['share_b_ahead']:5.1f}%  raw p<.05 in {100 * o['share_p_lt_05']:5.1f}% of draws")
    return out


def repeats(rng):
    sub = json.load(open("dataset/v3/eval/repeat_subset_40.json"))
    sid = [c["id"] for c in sub]
    idx = {c: np.array([k for k, c2 in enumerate(sub) if c2["category"] == c]) for c in CATS}
    RUNS = {"r3": "eval_runs/v3_25flash_r3", "rep1": "eval_runs/v3_25flash_r3_rep1", "rep2": "eval_runs/v3_25flash_r3_rep2"}
    ok = {}
    for p in ("proposed", "openhands", "agentless", "b3"):
        for r, d in RUNS.items():
            m = {x["case_id"]: x["final_status"] == "resolved" for x in load_pipeline_results(d, p)}
            ok[p, r] = np.array([m[i] for i in sid], dtype=float)
    print(f"\n=== 40-case subset, three runs (categories {[len(idx[c]) for c in CATS]})")
    print(f"{'':<11}" + "".join(f"{r:>10}" for r in RUNS) + f"{'mean':>10}   (macro %; overall mean in last column)")
    out = {"table": {}, "pairs": {}}
    for p in ("proposed", "openhands", "agentless", "b3"):
        ms = [macro(ok[p, r], idx) for r in RUNS]
        allr = sum(ok[p, r] for r in RUNS) / 3
        out["table"][p] = {"macro_per_run": ms, "macro_mean": float(np.mean(ms)), "overall_mean": float(allr.mean())}
        print(f"{p:<11}" + "".join(f"{100 * m:>9.1f}%" for m in ms) + f"{100 * np.mean(ms):>9.1f}%   {100 * allr.mean():.1f}%")
    ps = {}
    for a, b in RPAIRS:
        k = f"{a} vs {b}"
        d = sum(ok[a, r] - ok[b, r] for r in RUNS) / 3
        m, ci, p = strat_test(d, idx, rng)
        ps[k] = p
        out["pairs"][k] = dict(macro_diff=m, macro_ci95=ci, macro_p=p, per_run=[macro(ok[a, r] - ok[b, r], idx) for r in RUNS])
    h = A.holm(ps)
    for k, r in out["pairs"].items():
        r["macro_holm"] = h[k]
        print(f"{k:<26}{100 * r['macro_diff']:>+6.1f} [{100 * r['macro_ci95'][0]:>+6.1f},{100 * r['macro_ci95'][1]:>+6.1f}]  p {r['macro_p']:.2g}  holm {h[k]:.2g}"
              f"  per run " + " ".join(f"{100 * v:+.1f}" for v in r["per_run"]))
    return out


rng = np.random.default_rng(SEED)
out = {"seed": SEED, "perm": PERM, "boot": BOOT, "draws": DRAWS}
out["judged"] = full_set("evaluator verdict", judged, rng)
out["strict"] = full_set("original or equivalent only (r3 manual labels)", strict, rng)
loc = [A.case_features(next(c for c in cases if c["id"] == ids[k]))["inj_in_test_app"] for k in cidx["runtime_crash"]]
rt = cidx["runtime_crash"]
strata = (rt[np.array(loc, dtype=bool)], rt[~np.array(loc, dtype=bool)])
print(f"\nruntime by location: in test app {len(strata[0])}, outside {len(strata[1])}")
for name, y in (("evaluator verdict", judged), ("original or equivalent only", strict)):
    out.setdefault("downsample", {})[name] = {"uniform": downsample(name, y, rng), "by_location": downsample(name, y, rng, strata)}
out["repeats"] = repeats(rng)
json.dump(out, open("eval_runs/v3_25flash_r3/analysis/macro_sensitivity.json", "w"), indent=1)
