# 比較 C 語法案例的原始 build log 與 LogFilter 輸出：被注入的檔案有沒有出現，以及 r3 主實驗的結果 (2026-10-30)。
# Compares raw build logs with LogFilter output for C-syntax cases, plus r3 main outcomes.
import glob, json, os, re

H = os.path.expanduser("~/zephyr-eval-work/")
DIRS = [H + "v3_pilot/logs_fixed"] + [H + x + "/logs" for x in
        ("v3_pilot", "v3_migrate", "v3_new", "v3_fix", "v3_expand", "v3_qemucfg", "v3_compfix")]
ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
cases = [c for c in json.load(open("dataset/cases/final_dataset_v3.json")) if c["category"] == "c_syntax"]
hidden, named = [], []
raw_named = failed_named = 0
for c in cases:
    path = next(d + f"/{c['id']}.mutated.log" for d in DIRS if os.path.exists(d + f"/{c['id']}.mutated.log"))
    raw = ANSI.sub('', open(path, errors='replace').read())
    base = os.path.basename((c.get("injections") or [c["injection"]])[0]["target_file"])
    raw_named += bool(re.search(re.escape(base) + r":\d+", raw))
    failed_named += any(base in u for u in re.findall(r"^FAILED: (?:\[code=\d+\] )?(\S+\.obj)", raw, re.M))
    (named if base in c["initial_error_log"] else hidden).append(c["id"])
print(f"cases {len(cases)} | raw log names injected file {raw_named} | FAILED line names it {failed_named} | filtered log names it {len(named)}")
print("hidden:", *hidden, sep="\n  ")
res = {}
for p in ("b3", "agentless", "openhands", "proposed"):
    res[p] = {}
    for f in glob.glob(f"eval_runs/v3_25flash_r3/{p}/*.json"):
        j = json.load(open(f))
        for r in (j if isinstance(j, list) else [j]):
            if isinstance(r, dict) and r.get("category") == "c_syntax":
                res[p][r["case_id"]] = r["final_status"] == "resolved"
    print(p, f"resolved hidden {sum(res[p][i] for i in hidden)}/{len(hidden)} named {sum(res[p][i] for i in named)}/{len(named)}")
shared = [i for i in res["b3"] if not any(res[p][i] for p in res)]
print("all four failed:", len(shared), "| of which hidden:", sum(i in hidden for i in shared))
