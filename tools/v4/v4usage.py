"""Gemini 4 主實驗用量報表：每條 pipeline 的筆數、修好數、token、LLM 呼叫次數、估計費用與跑完的推估。
寫到 eval_runs/v4_main/usage.md，由 v4watch.py 每分鐘呼叫。費用用 11/30 前的定價，輸入不扣快取折扣、
不含 embedding，所以是上限估計；實際以 AI Studio 帳單為準。"""
import glob, json, os, time

REPO = os.path.expanduser("~/zephyr-llm-repair")
PRICE_IN, PRICE_OUT = 2.0, 10.0  # gemini-4-argon USD / 1M tokens (2026-11-30 前 / before)
N_CASES = 139
PIPES = ["proposed", "openhands", "agentless", "b3", "b2", "b1", "proposed_norag"]


def summarize(d):
    s = dict(results=0, resolved=0, errors=0, input=0, output=0, calls=0, seconds=0.0)
    for f in glob.glob(os.path.join(d, "*.json")):
        try:
            r = json.load(open(f))[0]
        except Exception:
            continue
        s["results"] += 1
        s["resolved"] += r.get("final_status") == "resolved"
        s["errors"] += r.get("final_status") == "error"
        s["seconds"] += r.get("ttr_seconds") or 0
        for it in r.get("iteration_log") or []:
            for u in it.get("token_usage") or []:
                s["input"] += u.get("input_tokens", 0)
                s["output"] += u.get("output_tokens", 0)
                s["calls"] += u.get("llm_calls", 1)
    s["usd"] = s["input"] / 1e6 * PRICE_IN + s["output"] / 1e6 * PRICE_OUT
    return s


def write():
    rows, total, proj = [], 0.0, 0.0
    for p in PIPES:
        d = os.path.join(REPO, "eval_runs/v4_main", p)
        if not os.path.isdir(d):
            continue
        s = summarize(d)
        if not s["results"]:
            continue
        per = s["usd"] / s["results"]
        est = s["usd"] + per * max(0, N_CASES - s["results"])
        total += s["usd"]
        proj += est
        rows.append(f"| {p} | {s['results']}/{N_CASES} | {s['resolved']} | {s['errors']} | {s['input'] / 1e6:.2f} | {s['output'] / 1e6:.2f} "
                    f"| {s['calls']} | ${s['usd']:.2f} | ${per:.2f} | ${est:.0f} |")
    lines = [f"# Gemini 4 主實驗用量（{time.strftime('%F %T')}）", "",
             f"gemini-4-argon，輸入 ${PRICE_IN:g}、輸出 ${PRICE_OUT:g} / 百萬 token（11/30 前）。輸入沒扣快取折扣，也不含 embedding，"
             "所以是上限估計；實際費用以 AI Studio 帳單為準。輸出 token 含 thinking。", "",
             "| pipeline | 完成 | 修好 | 錯誤 | 輸入 M | 輸出 M | LLM 呼叫 | 已用 | 每筆 | 跑完推估 |",
             "|---|---|---|---|---|---|---|---|---|---|", *rows, "",
             f"已用合計 ${total:.2f}；照目前每筆平均，跑完推估 ${proj:.0f}。月上限 $250，回報費用到 $185 會自動暫停。"]
    open(os.path.join(REPO, "eval_runs/v4_main/usage.md"), "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    write()
