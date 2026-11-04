"""r3 主資料集失敗案例的互斥分類 (Agentless、OpenHands，附 B3、Proposed 對照)。
Mutually exclusive failure modes for r3 main-set failures (Agentless, OpenHands; B3 and Proposed for reference).
  edited_injected : 改到注入檔但沒修好 / touched the injected file but still failing
  only_protected  : 沒改注入檔，只改了受保護檔 (測試、prj.conf 等，評測會還原) / only protected files (restored by the evaluator)
  other_files     : 沒改注入檔，改了其他非受保護檔 / other non-protected files
  no_edit         : 什麼都沒改成功 / no applied edit
OpenHands 另外記錄結束狀態 (finished / max_steps / stuck)；Agentless 另外記錄定位是否包含注入檔。"""
import json, os, sys
from collections import Counter, defaultdict
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(REPO, "tools"))
from failure_taxonomy import injected_files, load_pipeline_results  # noqa: E402

RUNS = os.path.join(REPO, "eval_runs/v3_25flash_r3")
cases = {c["id"]: c for c in json.load(open(os.path.join(REPO, "dataset/cases/final_dataset_v3.json")))}
out = {}
for p in ["b3", "agentless", "openhands", "proposed"]:
    modes, by_cat, extra = Counter(), defaultdict(Counter), defaultdict(Counter)
    for r in load_pipeline_results(RUNS, p):
        if r["final_status"] == "resolved":
            continue
        c = cases[r["case_id"]]
        inj = set(injected_files(c))
        changed, restored = set(), set()
        for it in r.get("iteration_log") or []:
            t = it.get("trajectory") or {}
            changed |= set(t.get("applied_files") or [])
            restored |= set(t.get("restored_files") or [])
        if p == "openhands":
            changed |= {f.replace("(deleted) ", "") for f in r["openhands"].get("changed_files") or []}
            extra["end_status"][r["openhands"].get("status")] += 1
        if p == "agentless":
            loc = set(r["agentless"].get("files") or [])
            extra["localized_injected"]["all" if inj <= loc else "partial" if inj & loc else "none"] += 1
        if inj & changed:
            m = "edited_injected"
        elif changed - restored:
            m = "other_files"
        elif changed or restored:
            m = "only_protected"
        else:
            m = "no_edit"
        modes[m] += 1
        by_cat[c["category"]][m] += 1
    out[p] = {"failed": sum(modes.values()), "modes": dict(modes),
              "by_category": {k: dict(v) for k, v in sorted(by_cat.items())}, **{k: dict(v) for k, v in extra.items()}}
json.dump(out, open(os.path.join(os.path.dirname(__file__), "failure_modes.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
