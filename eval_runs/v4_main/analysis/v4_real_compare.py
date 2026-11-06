# 真實 bug 13 筆：Gemini 4 (v4_main/real) 與 2.5 Flash 的逐筆配對比較。
# Real bugs (13): per-case paired comparison of Gemini 4 (v4_main/real) vs 2.5 Flash.
# 2.5 Flash Proposed 有兩組：r3 (OPT=0) 與 v4_25flash_opt (OPT=1，與 G4 Proposed 同設定)。
# 2.5 Flash OpenHands 是舊 workspace (時間戳未統一)。
import glob, json, os
from math import comb

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
SRC = {"g4_proposed": "eval_runs/v4_main/real/proposed", "g4_agentless": "eval_runs/v4_main/real/agentless",
       "g4_openhands": "eval_runs/v4_main/real/openhands",
       "f25_proposed_opt": "eval_runs/v4_25flash_opt/real/proposed", "f25_proposed_r3": "eval_runs/v3_25flash_r3/real/proposed",
       "f25_agentless": "eval_runs/v3_25flash_r3/real/agentless", "f25_openhands_oldws": "eval_runs/v3_25flash_r3/real/openhands",
       "f25_b3_olddef": "eval_runs/v3_25flash_r3/real/b3"}
PAIRS = [("g4_proposed", "f25_proposed_opt"), ("g4_agentless", "f25_agentless"), ("g4_openhands", "f25_openhands_oldws"),
         ("g4_proposed", "g4_agentless"), ("g4_proposed", "g4_openhands"), ("g4_openhands", "g4_agentless")]
PRICE = {"g4": (2.0, 10.0), "f25": (0.30, 2.50)}  # USD / 1M tokens, 未扣快取 / no cache discount


def load(d):
    out = {}
    for f in sorted(glob.glob(f"{d}/*.json")):
        r = json.load(open(f))[0]
        ti = to = 0
        for it in r.get("iteration_log") or []:
            for u in it.get("token_usage") or []:
                ti += u.get("input_tokens", 0); to += u.get("output_tokens", 0)
        out[r["case_id"]] = {"ok": r["final_status"] == "resolved", "status": r["final_status"], "in": ti, "out": to,
                             "iters": r.get("iterations"), "ttr": r.get("ttr_seconds"), "api_events": len(r.get("api_events") or [])}
    return out


def mcnemar(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


R = {k: load(v) for k, v in SRC.items()}
cases = sorted(R["g4_proposed"])
assert all(set(R[k]) == set(cases) for k in R), {k: len(R[k]) for k in R}
res = {"pipelines": {}, "pairs": [], "per_case": {}}
print(f"{'pipeline':22s} resolved  input_tok  output_tok   usd")
for k, r in R.items():
    pi, po = PRICE["g4" if k.startswith("g4") else "f25"]
    ti = sum(x["in"] for x in r.values()); to = sum(x["out"] for x in r.values())
    usd = ti / 1e6 * pi + to / 1e6 * po
    n = sum(x["ok"] for x in r.values())
    res["pipelines"][k] = {"resolved": n, "n": len(r), "input_tokens": ti, "output_tokens": to, "usd_listed": round(usd, 2),
                           "api_events": sum(x["api_events"] for x in r.values())}
    print(f"{k:22s} {n:2d}/{len(r)}  {ti:10d} {to:10d} {usd:7.2f}")
raw = []
for a, b in PAIRS:
    only_a = [c for c in cases if R[a][c]["ok"] and not R[b][c]["ok"]]
    only_b = [c for c in cases if R[b][c]["ok"] and not R[a][c]["ok"]]
    raw.append((a, b, only_a, only_b, mcnemar(len(only_a), len(only_b))))
order = sorted(range(len(raw)), key=lambda i: raw[i][4]); holm = [0] * len(raw); run = 0
for rank, i in enumerate(order):
    run = max(run, min(1.0, raw[i][4] * (len(raw) - rank))); holm[i] = run
print("\npair                                   a_only:b_only  p_exact  p_holm")
for (a, b, oa, ob, p), ph in zip(raw, holm):
    res["pairs"].append({"a": a, "b": b, "a_only": oa, "b_only": ob, "p_exact": p, "p_holm": ph})
    print(f"{a+' vs '+b:40s} {len(oa):2d}:{len(ob):<2d}        {p:.3f}    {ph:.3f}")
print("\nper case (1 = resolved):", " ".join(k[:12] for k in R))
for c in cases:
    res["per_case"][c] = {k: R[k][c]["status"] for k in R}
    print(f"{c[:44]:44s}", "  ".join(" 1" if R[k][c]["ok"] else " ." for k in R))
json.dump(res, open("eval_runs/v4_main/analysis/v4_real_compare.json", "w"), indent=1)
