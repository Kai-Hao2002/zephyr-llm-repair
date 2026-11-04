import json, os, random, sys, glob
from collections import Counter, defaultdict
sys.path.insert(0, 'tools')
import analyze_v3 as A
from failure_taxonomy import injected_files
P = ["b1", "b2", "b3", "agentless", "openhands", "proposed"]
SRC = {"b1": "eval_runs/v3_25flash_r2", "b2": "eval_runs/v3_25flash_r2"}
cases = {c["id"]: c for c in json.load(open("dataset/cases/final_dataset_v3.json"))}
res = {p: {json.load(open(f))[0]["case_id"]: json.load(open(f))[0] for f in glob.glob(f"{SRC.get(p,'eval_runs/v3_25flash_r3')}/{p}/*.json")} for p in P}
r2 = {p: {json.load(open(f))[0]["case_id"]: json.load(open(f))[0] for f in glob.glob(f"eval_runs/v3_25flash_r2/{p}/*.json")} for p in ["b3", "proposed"]}
ok = lambda r: r["final_status"] == "resolved"
ids = sorted(cases); N = len(ids)
out = {}
print("== sanity"); 
for p in P:
    rs = res[p]; print(p, len(rs), Counter(r["final_status"] for r in rs.values()), Counter(r["run_meta"]["models"]["pro"] for r in rs.values()), sum(r["run_meta"].get("code_dirty") for r in rs.values()))
print("== overall")
for p in P:
    k = sum(ok(res[p][i]) for i in ids); lo, hi = A.wilson(k, N); out.setdefault("overall", {})[p] = [k, N, k/N, lo, hi]
    print(f"{p:10s} {k:3d}/{N} {100*k/N:5.1f}%  CI {100*lo:4.1f}-{100*hi:4.1f}")
PAIRS = [("proposed","b3"),("proposed","agentless"),("proposed","openhands"),("proposed","b2"),("proposed","b1"),
         ("openhands","agentless"),("openhands","b3"),("agentless","b3"),("b3","b2")]
rng = random.Random(A.SEED); ps = {}; pw = {}
for a, b in PAIRS:
    x = [int(ok(res[a][i])) for i in ids]; y = [int(ok(res[b][i])) for i in ids]
    oa = sum(u and not v for u, v in zip(x, y)); ob = sum(v and not u for u, v in zip(x, y))
    p_ = A.mcnemar_exact(oa, ob); ci = A.paired_boot_ci(x, y, rng); key = f"{a} vs {b}"
    ps[key] = p_; pw[key] = [(sum(x)-sum(y))/N, ci, oa, ob, p_]
hp = A.holm(ps); print("== pairwise (Holm over 9)")
for k, v in pw.items():
    print(f"{k:24s} diff {100*v[0]:+5.1f}  CI [{100*v[1][0]:+5.1f},{100*v[1][1]:+5.1f}]  only_a {v[2]:2d} only_b {v[3]:2d}  p {v[4]:.2g}  holm {hp[k]:.2g}")
out["pairwise"] = {k: v + [hp[k]] for k, v in pw.items()}
def grp(feat):
    g = defaultdict(list)
    for i in ids: g[str(A.case_features(cases[i])[feat])].append(i)
    print(f"== by {feat}")
    for k, v in sorted(g.items()):
        print(f"{k:22s} n={len(v):3d} " + "  ".join(f"{p}={sum(ok(res[p][i]) for i in v)}" for p in P))
for f in ("category", "inj_in_test_app", "inj_in_log", "board"): grp(f)
print("== r2 -> r3 (same pipeline)")
for p in ("b3", "proposed"):
    a = sum(ok(r2[p][i]) for i in ids); b = sum(ok(res[p][i]) for i in ids); both = sum(ok(r2[p][i]) and ok(res[p][i]) for i in ids)
    print(p, "r2", a, "r3", b, "both", both, "r3 only", b-both, "r2 only", a-both)
print("== cost (list price 2.5-flash: $0.30/M in, $2.50/M out; OH litellm cost separately)")
for p in P:
    rs = [res[p][i] for i in ids]; tk = [A.tokens(r) for r in rs]
    ti = sum(t[0] for t in tk); to = sum(t[1] for t in tk); k = sum(ok(r) for r in rs)
    usd = ti/1e6*0.30 + to/1e6*2.50
    ttr = sum((r.get("ttr_seconds") or 0) for r in rs)
    calls = sum(A.gen_calls(r) for r in rs) if p != "openhands" else sum((r.get("openhands") or {}).get("llm_calls") or 0 for r in rs)
    extra = f" litellm_cost=${sum((r.get('openhands') or {}).get('cost') or 0 for r in rs):.2f}" if p == "openhands" else ""
    print(f"{p:10s} in/case {ti/N/1e3:7.1f}k out/case {to/N/1e3:5.1f}k calls/case {calls/N:5.1f} $/case {usd/N:.4f} $/resolved {usd/max(k,1):.4f} total ${usd:.2f} min/case {ttr/N/60:5.1f} min/resolved {ttr/max(k,1)/60:5.1f}{extra}")
print("== resolved patch location")
for p in P: print(p, dict(Counter(A.patch_location(cases[i], res[p][i]) for i in ids if ok(res[p][i]))))
print("== integrity violations (iterations / cases)")
for p in P:
    v = [sum("violation" in str((it.get("trajectory") or {}).get("status")) for it in res[p][i].get("iteration_log") or []) for i in ids]
    print(p, sum(v), sum(x > 0 for x in v))
print("== proposed retrieval")
rs = []
for i in ids:
    r = res["proposed"][i]; inj = set(injected_files(cases[i])); first = r.get("first_retrieval_files") or []
    rank = next((k+1 for k, f in enumerate(first) if f in inj), None)
    anyit = set().union(*[set((it.get("trajectory") or {}).get("retrieved_files") or []) for it in r["iteration_log"]])
    rs.append((rank, bool(inj & anyit), ok(r)))
n = len(rs); hit = [x for x in rs if x[0]]
print("hit@1", sum(x[0] == 1 for x in rs), "hit@8", len(hit), "/", n, "MRR %.3f" % (sum(1/x[0] for x in hit)/n), "any-iter", sum(x[1] for x in rs), "resolved|hit", sum(x[2] for x in hit), "/", len(hit), "resolved|miss", sum(x[2] for x in rs if not x[0]), "/", n-len(hit))
oh = [res["openhands"][i]["openhands"] for i in ids]
print("== openhands", Counter(o.get("status") for o in oh), "steps mean %.1f" % (sum(o.get("steps") or 0 for o in oh)/N))
ms = [i for i in ids if res["openhands"][i]["openhands"].get("status") == "max_steps"]
print("max_steps by cat", Counter(cases[i]["category"] for i in ms), "resolved among max_steps", sum(ok(res["openhands"][i]) for i in ms))
json.dump(out, open(sys.argv[1], "w"), indent=1, default=float)
