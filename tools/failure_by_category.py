# tools/failure_by_category.py
"""
v3 失敗案例逐筆整理 (純離線)：每一筆沒修好的 (pipeline, case) 記下
- 每一輪停在哪個階段 (apply / static_check / build / run) 與狀態；
- 每一輪改了哪些檔案，分成 注入檔 / 受保護規格檔 (target_app 底下、被還原) / 其他；
- 最後一輪留下的第一個錯誤，與初始錯誤是否相同 (只比對錯誤行的檔案與訊息)；
- compound：兩個注入檔各自有沒有被改到 (Kconfig 部分 / DTS 部分)。
輸出 <out_dir>/failure_by_category.json，並印出各類別的摘要表。

Per-failure record for v3 (offline): per-iteration stage/status, edited files split into injected /
protected spec (restored) / other, the first remaining error vs the initial one, and for compound
cases which half (Kconfig / DTS) was touched.

Usage: python tools/failure_by_category.py --runs eval_runs/v3_25flash --dataset dataset/cases/final_dataset_v3.json \
           --out-dir eval_runs/v3_25flash/analysis
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from failure_taxonomy import injected_files, load_pipeline_results  # noqa: E402

PIPELINES = ["b1", "b2", "b3", "proposed"]
CATS = ["c_syntax", "kconfig", "dts", "compound", "runtime_crash"]
_ERR_RE = re.compile(r"(error:.*|ERROR:.*|Assertion failed at.*|FATAL ERROR.*|Segmentation fault.*|"
                     r"devicetree error:.*|warning: .*was assigned the value.*|CMake Error.*)")
_PATH_RE = re.compile(r"(?:/zephyrproject/zephyr/|WEST_TOPDIR/zephyr/)")


def first_error(log: Optional[str]) -> Optional[str]:
    """log 裡第一個錯誤行，去掉絕對路徑前綴與行號，方便比較。First error line, normalized."""
    if not log:
        return None
    for line in log.splitlines():
        m = _ERR_RE.search(line)
        if m:
            s = _PATH_RE.sub("", line.strip())
            return re.sub(r":\d+(:\d+)?:", ":", s)[:220]
    return None


def classify_files(case: Dict, files: List[str]) -> Dict[str, List[str]]:
    inj = set(injected_files(case))
    app = case["target_app"].rstrip("/") + "/"
    out = {"injected": [], "spec": [], "other": []}
    for f in files:
        if f in inj:
            out["injected"].append(f)
        elif f.startswith(app):
            out["spec"].append(f)
        else:
            out["other"].append(f)
    return out


def summarize(case: Dict, r: Dict) -> Dict:
    its = [it for it in r.get("iteration_log") or [] if it.get("trajectory")]
    inj = injected_files(case)
    rows, touched = [], set()
    for it in its:
        t = it["trajectory"]
        applied = list(dict.fromkeys(t.get("applied_files") or []))
        if isinstance(t.get("patch"), dict):  # B1
            fp = t["patch"].get("filepath", "").strip()
            if t.get("status") != "patch_format_error" and fp:
                applied = [fp]
        cls = classify_files(case, applied)
        touched |= set(cls["injected"])
        rows.append({"iteration": it["iteration"], "stage": t.get("stage"), "status": t.get("status"),
                     "files": cls, "error": first_error(t.get("filtered_log") or t.get("apply_error"))})
    init_err = first_error(case.get("initial_error_log"))
    last = rows[-1] if rows else {}
    comp = None
    if case["category"] == "compound":
        injs = case.get("injections") or []
        kfile = next((i["target_file"] for i in injs if i["operator"].startswith("kconfig")), None)
        dfile = next((i["target_file"] for i in injs if i["operator"].startswith("dts")), None)
        comp = {"kconfig_touched": kfile in touched, "dts_touched": dfile in touched}
    return {
        "pipeline": r["pipeline"], "case_id": case["id"], "category": case["category"],
        "final_status": r["final_status"], "iterations": len(rows),
        "stages": [f"{x['stage']}/{x['status']}" for x in rows],
        "ever_touched_injected": sorted(touched), "all_injected_touched": set(inj) <= touched,
        "ever_edited_spec": any(x["files"]["spec"] for x in rows),
        "ever_edited_other": any(x["files"]["other"] for x in rows),
        "only_spec_or_other": bool(rows) and not touched,
        "initial_error": init_err, "last_error": last.get("error"),
        "last_error_same_as_initial": bool(init_err) and last.get("error") == init_err,
        "last_stage": last.get("stage"), "last_status": last.get("status"),
        "compound": comp, "iterations_detail": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    cases = {c["id"]: c for c in json.load(open(args.dataset))}
    out = []
    for p in PIPELINES:
        for r in load_pipeline_results(args.runs, p):
            if r["final_status"] != "resolved":
                out.append(summarize(cases[r["case_id"]], r))
    os.makedirs(args.out_dir, exist_ok=True)
    json.dump(out, open(os.path.join(args.out_dir, "failure_by_category.json"), "w"), indent=1, ensure_ascii=False)

    for c in CATS:
        print(f"===== {c}")
        for p in PIPELINES:
            rows = [x for x in out if x["pipeline"] == p and x["category"] == c]
            if not rows:
                continue
            n = len(rows)
            last = Counter(x["last_status"] for x in rows)
            print(f"  {p:8s} failed={n:2d}  never_touched_injected={sum(x['only_spec_or_other'] for x in rows):2d}"
                  f"  touched_all_injected={sum(x['all_injected_touched'] for x in rows):2d}"
                  f"  edited_spec={sum(x['ever_edited_spec'] for x in rows):2d}"
                  f"  same_error_at_end={sum(x['last_error_same_as_initial'] for x in rows):2d}"
                  f"  last_status={dict(last)}")
            if c == "compound":
                cc = Counter((x["compound"]["kconfig_touched"], x["compound"]["dts_touched"]) for x in rows)
                print(f"           compound (kconfig_touched, dts_touched): {dict(cc)}")


if __name__ == "__main__":
    main()
