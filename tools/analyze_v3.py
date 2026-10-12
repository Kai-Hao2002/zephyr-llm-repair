# tools/analyze_v3.py
"""
v3 評測結果分析 (純離線：不呼叫 LLM、不跑 Docker、不改評測程式)。
讀 <runs>/<pipeline>/<case_id>.json 與資料集，輸出 <out_dir>/summary.json、taxonomy.json，
並把主要表格印到 stdout。報告文字另外手寫 (REPORT.md)。

v3 evaluation analysis (offline only). Reads <runs>/<pipeline>/<case_id>.json plus the dataset,
writes <out_dir>/summary.json and taxonomy.json, prints the main tables.

Usage:
  python tools/analyze_v3.py --runs eval_runs/v3_25flash --dataset dataset/cases/final_dataset_v3.json \
      --real-runs eval_runs/v3_25flash/real --real-dataset dataset/cases/real_bugs_v3.json \
      --out-dir eval_runs/v3_25flash/analysis
"""
import argparse
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from failure_taxonomy import CATEGORY_ORDER, classify, injected_files, load_pipeline_results  # noqa: E402

PIPELINES = ["b1", "b2", "b3", "proposed"]
PAIRS = [("proposed", "b3"), ("proposed", "b2"), ("proposed", "b1"), ("b3", "b2"), ("b3", "b1"), ("b2", "b1")]
BOOT_N = 10000
SEED = 20261012


# ---------- statistics ----------

def wilson(k: int, n: int, z: float = 1.959964) -> Tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def mcnemar_exact(b: int, c: int) -> float:
    """雙尾精確 McNemar (不一致對數的二項檢定)。Two-sided exact McNemar."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def holm(pvals: Dict[str, float]) -> Dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, out, running = len(items), {}, 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def paired_boot_ci(x: List[int], y: List[int], rng: random.Random) -> Tuple[float, float]:
    """成對 bootstrap (以案例為單位重抽) 的差異 95% CI。Paired case-level bootstrap CI of x-y."""
    n = len(x)
    diffs = []
    for _ in range(BOOT_N):
        idx = [rng.randrange(n) for _ in range(n)]
        diffs.append(sum(x[i] - y[i] for i in idx) / n)
    diffs.sort()
    return (diffs[int(0.025 * BOOT_N)], diffs[int(0.975 * BOOT_N) - 1])


# ---------- loading ----------

def load(runs: str, dataset: str) -> Tuple[Dict[str, Dict], Dict[str, Dict[str, Dict]]]:
    cases = {c["id"]: c for c in json.load(open(dataset))}
    res = {}
    for p in PIPELINES:
        rows = {r["case_id"]: r for r in load_pipeline_results(runs, p)}
        res[p] = rows
    return cases, res


def sanity(cases: Dict, res: Dict) -> Dict[str, Any]:
    out = {}
    for p, rows in res.items():
        missing = sorted(set(cases) - set(rows))
        extra = sorted(set(rows) - set(cases))
        errors = sorted(r["case_id"] for r in rows.values() if r["final_status"] == "error")
        models = Counter(r["run_meta"]["models"]["pro"] for r in rows.values())
        commits = Counter(r["run_meta"]["code_commit"][:7] for r in rows.values())
        dirty = sum(bool(r["run_meta"].get("code_dirty")) for r in rows.values())
        out[p] = {"n": len(rows), "missing": missing, "extra": extra, "errors": errors,
                  "models": dict(models), "code_commits": dict(commits), "code_dirty": dirty}
    return out


# ---------- case features ----------

def primary_operator(case: Dict) -> str:
    injs = case.get("injections") or [case["injection"]]
    ops = [i["operator"].split(":")[0] for i in injs]
    return "+".join(ops)


def case_features(case: Dict) -> Dict[str, Any]:
    inj = injected_files(case)
    app = case["target_app"].rstrip("/") + "/"
    return {
        "category": case["category"],
        "operator": primary_operator(case),
        "board": "native_sim" if case["board"].startswith("native_sim") else "qemu",
        # 初始 log (資料集記錄的版本) 是否寫出某個被注入檔的路徑。用子字串比對：runtime 的
        # assertion 行是 WEST_TOPDIR/zephyr/...，log_paths() 只認 /zephyrproject/zephyr/...
        # whether the dataset's initial log names any injected file. Substring match: runtime
        # assertion lines print WEST_TOPDIR/zephyr/..., which log_paths() (/zephyrproject/...) misses.
        "inj_in_log": any(f in case.get("initial_error_log", "") for f in inj),
        # 注入在 target_app 底下 (測試程式/設定) 或在核心程式 (kernel/lib/drivers/...)
        # injection inside the test app vs. in the RTOS code base
        "inj_in_test_app": any(f.startswith(app) for f in inj),
    }


# ---------- per-result facts ----------

def resolving_iteration(r: Dict) -> Optional[Dict]:
    for it in r.get("iteration_log") or []:
        if it.get("resolved"):
            return it
    return None


def patch_location(case: Dict, r: Dict) -> Optional[str]:
    """修好的案例：到修好那一輪為止的生效修改 (workspace 跨迭代保留，受保護檔每次建置前還原)
    有沒有落在被注入檔。
    For resolved cases: do the effective edits up to the resolving iteration (the workspace keeps
    edits across iterations; protected files are restored before every build) cover the injected files?"""
    if r["final_status"] != "resolved":
        return None
    inj = set(injected_files(case))
    applied, restored = set(), set()
    for it in r.get("iteration_log") or []:
        t = it.get("trajectory") or {}
        applied |= set(t.get("applied_files") or [])
        if isinstance(t.get("patch"), dict):  # B1 full-file patch
            applied |= {t["patch"].get("filepath", "").strip()} - {""}
        restored |= set(t.get("restored_files") or [])
        if it.get("resolved"):
            break
    eff = applied - restored
    if not eff:
        return "no_effective_edit"
    if inj <= eff:
        return "all_injected"
    if inj & eff:
        return "some_injected"
    return "elsewhere"


def gen_calls(r: Dict) -> int:
    return sum(len(it.get("token_usage") or []) for it in r.get("iteration_log") or [])


def tokens(r: Dict) -> Tuple[int, int]:
    ins = outs = 0
    for it in r.get("iteration_log") or []:
        for t in it.get("token_usage") or []:
            ins += t.get("input_tokens") or 0
            outs += t.get("output_tokens") or 0
    return ins, outs


# ---------- analyses ----------

def apply_labels(res: Dict, labels_path: Optional[str], drop: Tuple[str, ...]) -> Dict:
    """人工標註 (tools/replay_patches.py 之後的判讀) 中屬於 drop 的修好案例改判為未修好，回傳新的結果表。
    Returns a copy of res where resolved cases manually labelled with any of `drop` count as unresolved."""
    if not labels_path:
        return res
    bad = {(p, cid) for p, cid, label, _ in json.load(open(labels_path))["labels"] if label in drop}
    return {p: {cid: (dict(r, final_status="relabelled_" + "_".join(drop)) if (p, cid) in bad else r)
                for cid, r in rows.items()} for p, rows in res.items()}


def rates(cases, res, ids=None) -> Dict[str, Dict]:
    ids = ids if ids is not None else sorted(cases)
    out = {}
    for p in PIPELINES:
        k = sum(res[p][i]["final_status"] == "resolved" for i in ids if i in res[p])
        n = sum(i in res[p] for i in ids)
        lo, hi = wilson(k, n)
        out[p] = {"k": k, "n": n, "rate": k / n if n else None, "ci95": [lo, hi]}
    return out


def pairwise(cases, res) -> Dict[str, Dict]:
    ids = sorted(cases)
    rng = random.Random(SEED)
    out, ps = {}, {}
    for a, b in PAIRS:
        x = [int(res[a][i]["final_status"] == "resolved") for i in ids]
        y = [int(res[b][i]["final_status"] == "resolved") for i in ids]
        only_a = sum(xi and not yi for xi, yi in zip(x, y))
        only_b = sum(yi and not xi for xi, yi in zip(x, y))
        both = sum(xi and yi for xi, yi in zip(x, y))
        p = mcnemar_exact(only_a, only_b)
        key = f"{a}_vs_{b}"
        ps[key] = p
        out[key] = {"diff": (sum(x) - sum(y)) / len(ids), "ci95": list(paired_boot_ci(x, y, rng)),
                    "only_a": only_a, "only_b": only_b, "both": both, "neither": len(ids) - only_a - only_b - both,
                    "p_exact": p}
    for k, v in holm(ps).items():
        out[k]["p_holm"] = v
    return out


def grouped(cases, res, feat: str) -> Dict[str, Dict]:
    groups = defaultdict(list)
    for cid, c in cases.items():
        groups[str(case_features(c)[feat])].append(cid)
    return {g: {"n": len(ids), **{p: v for p, v in rates(cases, res, ids).items()}} for g, ids in sorted(groups.items())}


def retrieval(cases, res) -> Dict[str, Dict]:
    out = {}
    for p in ("b2", "proposed"):
        rows = []
        for cid, c in cases.items():
            r = res[p][cid]
            inj = set(injected_files(c))
            first = r.get("first_retrieval_files")
            if first is None:
                continue
            rank = next((i + 1 for i, f in enumerate(first) if f in inj), None)
            any_iter = set()
            for it in r.get("iteration_log") or []:
                any_iter |= set((it.get("trajectory") or {}).get("retrieved_files") or [])
            rows.append({"rank": rank, "any_iter_hit": bool(inj & (any_iter | set(first))),
                         "resolved": r["final_status"] == "resolved"})
        n = len(rows)
        hit = [x for x in rows if x["rank"]]
        miss = [x for x in rows if not x["rank"]]
        out[p] = {
            "n_with_retrieval": n,
            "hit@1": sum(x["rank"] == 1 for x in rows) / n if n else None,
            "hit@3": sum(bool(x["rank"]) and x["rank"] <= 3 for x in rows) / n if n else None,
            "hit@8": len(hit) / n if n else None,
            "mrr": sum(1 / x["rank"] for x in hit) / n if n else None,
            "any_iteration_hit": sum(x["any_iter_hit"] for x in rows) / n if n else None,
            "resolved_given_hit": [sum(x["resolved"] for x in hit), len(hit)],
            "resolved_given_miss": [sum(x["resolved"] for x in miss), len(miss)],
        }
    return out


def integrity_and_format(cases, res, tax) -> Dict[str, Dict]:
    out = {}
    for p in PIPELINES:
        rows = [t for t in tax if t["pipeline"] == p]
        out[p] = {
            "integrity_violation_cases": sum(t["integrity_violations"] > 0 for t in rows),
            "integrity_violation_iterations": sum(t["integrity_violations"] for t in rows),
            "integrity_violation_then_resolved": sum(t["integrity_violations"] > 0 and t["failure"] == "resolved" for t in rows),
            "format_error_cases": sum(t["format_errors"] > 0 for t in rows),
            "format_error_iterations": sum(t["format_errors"] for t in rows),
            "multi_block_one_header_cases": sum("multi_block_one_header" in t["tags"] for t in rows),
            "diff_style_header_cases": sum("diff_style_header" in t["tags"] for t in rows),
            "total_iterations": sum(t["iterations"] for t in rows),
        }
    return out


def locations(cases, res) -> Dict[str, Dict]:
    return {p: dict(Counter(patch_location(cases[cid], res[p][cid]) for cid in cases
                            if res[p][cid]["final_status"] == "resolved")) for p in PIPELINES}


def costs(cases, res) -> Dict[str, Dict]:
    out = {}
    for p in PIPELINES:
        rs = [res[p][cid] for cid in cases]
        calls = [gen_calls(r) for r in rs]
        tok = [tokens(r) for r in rs]
        ttr = [r.get("ttr_seconds") or 0 for r in rs]
        its = [r.get("iterations") or 0 for r in rs]
        solved = [r for r in rs if r["final_status"] == "resolved"]
        out[p] = {"gen_calls_mean": sum(calls) / len(rs), "gen_calls_total": sum(calls),
                  "input_tokens_mean": sum(t[0] for t in tok) / len(rs), "output_tokens_mean": sum(t[1] for t in tok) / len(rs),
                  "iterations_mean": sum(its) / len(rs), "ttr_mean_s": sum(ttr) / len(rs),
                  "iterations_mean_resolved": (sum(r["iterations"] for r in solved) / len(solved)) if solved else None}
    return out


def manual_sample(cases, res, out_path: str, per_pipeline=(("proposed", 8), ("b3", 6), ("b2", 6))) -> List[Dict]:
    rng = random.Random(SEED)
    picked = []
    for p, k in per_pipeline:
        solved = sorted(cid for cid in cases if res[p][cid]["final_status"] == "resolved")
        for cid in rng.sample(solved, min(k, len(solved))):
            it = resolving_iteration(res[p][cid]) or {}
            t = it.get("trajectory") or {}
            picked.append({"pipeline": p, "case_id": cid, "category": cases[cid]["category"],
                           "operator": primary_operator(cases[cid]), "injected": injected_files(cases[cid]),
                           "applied": t.get("applied_files"), "restored": t.get("restored_files"),
                           "patch": t.get("patch")})
    with open(out_path, "w") as f:
        for i, x in enumerate(picked, 1):
            f.write(f"## {i}. [{x['pipeline']}] {x['case_id']} ({x['category']}, {x['operator']})\n")
            f.write(f"- injected: {x['injected']}\n- applied: {x['applied']}\n- restored: {x['restored']}\n\n")
            patch = x["patch"] if isinstance(x["patch"], str) else json.dumps(x["patch"], ensure_ascii=False, indent=1)
            f.write("```\n" + (patch or "") + "\n```\n\n")
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--dataset", default="dataset/cases/final_dataset_v3.json")
    ap.add_argument("--real-runs")
    ap.add_argument("--real-dataset", default="dataset/cases/real_bugs_v3.json")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--labels", help="manual_labels_main.json (人工判讀；用來算嚴格修復率)")
    ap.add_argument("--replay", help="replay_main.json (tools/replay_patches.py 的輸出)")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    cases, res = load(args.runs, args.dataset)
    tax = [classify(cases[cid], res[p][cid]) for p in PIPELINES for cid in sorted(cases)]
    summary = {
        "sanity": sanity(cases, res),
        "rates": rates(cases, res),
        "pairwise": pairwise(cases, res),
        "by_category": grouped(cases, res, "category"),
        "by_inj_in_log": grouped(cases, res, "inj_in_log"),
        "by_inj_in_test_app": grouped(cases, res, "inj_in_test_app"),
        "by_operator": grouped(cases, res, "operator"),
        "by_board": grouped(cases, res, "board"),
        "retrieval": retrieval(cases, res),
        "integrity_and_format": integrity_and_format(cases, res, tax),
        "patch_location_resolved": locations(cases, res),
        "failure_taxonomy": {p: dict(Counter(t["failure"] for t in tax if t["pipeline"] == p)) for p in PIPELINES},
        "costs": costs(cases, res),
    }
    manual_sample(cases, res, os.path.join(args.out_dir, "manual_review_sample.md"))
    if args.replay:
        rp = json.load(open(args.replay))
        summary["replay"] = {p: dict(Counter(x.get("overall") for x in rp if x["pipeline"] == p)) for p in PIPELINES}
    if args.labels:
        lab = json.load(open(args.labels))["labels"]
        summary["manual_labels"] = {p: dict(Counter(x[2] for x in lab if x[0] == p)) for p in PIPELINES}
        for name, drop in (("strict_no_test_change", ("test_changed",)),
                           ("strict_root_fix", ("test_changed", "alternative"))):
            r2 = apply_labels(res, args.labels, drop)
            summary[name] = {"rates": rates(cases, r2), "pairwise": pairwise(cases, r2),
                             "by_category": grouped(cases, r2, "category")}

    if args.real_runs:
        rcases, rres = load(args.real_runs, args.real_dataset)
        rtax = [classify(rcases[cid], rres[p][cid]) for p in PIPELINES for cid in sorted(rcases)]
        summary["real"] = {
            "sanity": sanity(rcases, rres),
            "rates": rates(rcases, rres),
            "per_case": {cid: {p: rres[p][cid]["final_status"] for p in PIPELINES} for cid in sorted(rcases)},
            "failure_taxonomy": {p: dict(Counter(t["failure"] for t in rtax if t["pipeline"] == p)) for p in PIPELINES},
            "patch_location_resolved": locations(rcases, rres),
            "retrieval": retrieval(rcases, rres),
        }
        json.dump(rtax, open(os.path.join(args.out_dir, "taxonomy_real.json"), "w"), indent=1, ensure_ascii=False)

    json.dump(tax, open(os.path.join(args.out_dir, "taxonomy.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(summary, open(os.path.join(args.out_dir, "summary.json"), "w"), indent=1, ensure_ascii=False)

    # ---- stdout tables ----
    print("== sanity"); [print(" ", p, v) for p, v in summary["sanity"].items()]
    print("== rates (Wilson 95% CI)")
    for p, v in summary["rates"].items():
        print(f"  {p:9s} {v['k']:3d}/{v['n']}  {v['rate']:.1%}  [{v['ci95'][0]:.1%}, {v['ci95'][1]:.1%}]")
    print("== pairwise (exact McNemar, Holm; paired bootstrap CI of difference)")
    for k, v in summary["pairwise"].items():
        print(f"  {k:18s} diff {v['diff']:+.1%} [{v['ci95'][0]:+.1%}, {v['ci95'][1]:+.1%}]  "
              f"only_a {v['only_a']} only_b {v['only_b']}  p={v['p_exact']:.2g} holm={v['p_holm']:.2g}")
    for feat in ("by_category", "by_inj_in_log", "by_inj_in_test_app", "by_board", "by_operator"):
        print(f"== {feat}")
        for g, v in summary[feat].items():
            print(f"  {g:45s} n={v['n']:3d} " + "  ".join(f"{p} {v[p]['k']:2d} ({v[p]['rate']:.0%})" for p in PIPELINES))
    print("== retrieval"); [print(" ", p, v) for p, v in summary["retrieval"].items()]
    print("== integrity/format"); [print(" ", p, v) for p, v in summary["integrity_and_format"].items()]
    print("== patch location (resolved)"); [print(" ", p, v) for p, v in summary["patch_location_resolved"].items()]
    print("== failure taxonomy")
    for p, v in summary["failure_taxonomy"].items():
        print(" ", p, {c: v.get(c, 0) for c in ["resolved"] + CATEGORY_ORDER if v.get(c)})
    print("== costs"); [print(" ", p, {k: round(x, 1) if isinstance(x, float) else x for k, x in v.items()}) for p, v in summary["costs"].items()]
    for name in ("strict_no_test_change", "strict_root_fix"):
        if name in summary:
            print(f"== {name}")
            for p, v in summary[name]["rates"].items():
                print(f"  {p:9s} {v['k']:3d}/{v['n']}  {v['rate']:.1%}  [{v['ci95'][0]:.1%}, {v['ci95'][1]:.1%}]")
            for k, v in summary[name]["pairwise"].items():
                print(f"  {k:18s} diff {v['diff']:+.1%} [{v['ci95'][0]:+.1%}, {v['ci95'][1]:+.1%}]  "
                      f"only_a {v['only_a']} only_b {v['only_b']}  p={v['p_exact']:.2g} holm={v['p_holm']:.2g}")
            for g, v in summary[name]["by_category"].items():
                print(f"  {g:15s} n={v['n']:3d} " + "  ".join(f"{p} {v[p]['k']:2d}" for p in PIPELINES))
    if "replay" in summary:
        print("== replay", summary["replay"])
        print("== manual labels", summary.get("manual_labels"))
    if "real" in summary:
        print("== real-bug set")
        print("  sanity", summary["real"]["sanity"])
        for p, v in summary["real"]["rates"].items():
            print(f"  {p:9s} {v['k']}/{v['n']}")
        for cid, v in summary["real"]["per_case"].items():
            print(f"  {cid:50s} " + " ".join(f"{p}:{'R' if s == 'resolved' else '.'}" for p, s in v.items()))
        print("  taxonomy", summary["real"]["failure_taxonomy"])
        print("  patch location", summary["real"]["patch_location_resolved"])
        print("  retrieval", summary["real"]["retrieval"])


if __name__ == "__main__":
    main()
