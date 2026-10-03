# tools/build_failure_report.py
"""
把 tools/failure_taxonomy.py 的輸出 + v2 結果整理成給指導教授看的單頁 HTML 報告。
數字全部在這裡從結果檔重新計算，報告裡的文字敘述只引用這些數字。

Builds the advisor-facing single-page HTML report from tools/failure_taxonomy.py's
output and the v2 result files. Every number is recomputed here from the result files.
"""
import html
import json
from collections import Counter, defaultdict
from math import comb

RUNS = "eval_runs/v2"
OUT = "eval_runs/v2/analysis/failure_report.html"
PIPES = ["b1", "b2", "b3", "proposed"]
PNAME = {"b1": "B1 零樣本", "b2": "B2 單次 + BM25", "b3": "B3 閉環無檢索", "proposed": "Proposed"}
CATS = ["c_syntax", "dts", "kconfig", "compound", "runtime_crash"]
FAIL = ["wrong_file", "spec_reverted", "apply_failed", "right_file_build", "right_file_runtime", "llm_hang"]
FNAME = {
    "wrong_file": "定位失敗：改了別的檔案",
    "spec_reverted": "定位失敗：只改測試/設定，被還原",
    "apply_failed": "Patch 從未成功套用",
    "right_file_build": "改到注入檔案，但建置失敗",
    "right_file_runtime": "改到注入檔案，可編譯但測試仍失敗",
    "llm_hang": "模型無回應 (llm_hang)",
}
TAGNAME = {
    "crash_signal_lost": "crash 訊號遺失",
    "restored": "有檔案被還原",
    "partial_compound": "compound 只修一處",
    "static_check_loop": "卡在 StaticCheck",
    "hallucinated_path": "路徑幻覺",
    "angle_bracket_path": "路徑包 <>",
    "search_mismatch": "SEARCH 不符",
    "format_error": "格式錯誤",
    "intended_injected": "想改注入檔",
    "inj_visible": "注入檔在上下文中",
    "inj_not_visible": "注入檔不在上下文",
}


def load():
    res = {p: {x["case_id"]: x for x in json.load(open(f"{RUNS}/{p}_results.json"))} for p in PIPES}
    rows = json.load(open(f"{RUNS}/analysis/failure_taxonomy.json"))
    ds = {c["id"]: c for c in json.load(open("dataset/cases/final_dataset.json"))}
    return res, rows, ds


def mcnemar(res, a, b):
    ok = lambda p, c: res[p][c]["final_status"] == "resolved"
    x = sum(ok(a, c) and not ok(b, c) for c in res[a])
    y = sum(ok(b, c) and not ok(a, c) for c in res[a])
    n, k = x + y, min(x, y)
    return x, y, min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def main():
    res, rows, ds = load()
    inj = lambda c: {i["target_file"] for i in (ds[c].get("injections") or [ds[c]["injection"]])}
    S = {}
    S["resolved"] = {p: sum(x["final_status"] == "resolved" for x in res[p].values()) for p in PIPES}
    S["fail"] = {p: Counter(r["failure"] for r in rows if r["pipeline"] == p and r["failure"] != "resolved") for p in PIPES}
    S["bycat"] = {p: {c: (sum(res[p][i]["final_status"] == "resolved" for i in ds if ds[i]["category"] == c),
                          sum(1 for i in ds if ds[i]["category"] == c)) for c in CATS} for p in PIPES}
    S["mcn"] = {f"{a}|{b}": mcnemar(res, a, b) for a, b in [("proposed", "b2"), ("proposed", "b3"), ("b3", "b2")]}
    # 錯誤 log 有沒有注入檔路徑 → 定位 / 修好比例 (排除 llm_hang)
    loc = {}
    for p in PIPES:
        R = [r for r in rows if r["pipeline"] == p and r["failure"] != "llm_hang"]
        for flag in (True, False):
            sub = [r for r in R if r["inj_in_log"] == flag]
            loc[f"{p}|{flag}"] = (len(sub), sum(r["touched_injected"] for r in sub), sum(r["failure"] == "resolved" for r in sub))
    S["loc"] = loc
    # crash 訊號遺失
    lost = [c for c in ds if ds[c]["category"] == "runtime_crash" and res["b2"][c].get("initial_error_log")
            and "Fallback Raw Tail" not in res["b2"][c]["initial_error_log"]]
    other = [c for c in ds if ds[c]["category"] == "runtime_crash" and c not in lost]
    S["lost"] = {p: (sum(res[p][c]["final_status"] == "resolved" for c in lost), len(lost),
                     sum(res[p][c]["final_status"] == "resolved" for c in other), len(other)) for p in PIPES}
    # 第一次檢索 hit@8
    S["hit"] = {p: sum(1 for c in ds if inj(c) & set(res[p][c].get("first_retrieval_files") or [])) for p in ("b2", "proposed")}
    S["noretr"] = sum(1 for c in ds if not res["proposed"][c].get("first_retrieval_files"))

    table_rows = []
    for r in rows:
        if r["failure"] == "resolved":
            continue
        table_rows.append({
            "p": r["pipeline"], "id": r["case_id"], "cat": r["category_dataset"], "f": r["failure"],
            "tags": [t for t in r["tags"]], "inj": r["injected"], "app": r["applied"][:6],
            "napp": len(r["applied"]), "it": r["iterations"], "board": r["board"],
        })
    page = TEMPLATE
    page = page.replace("/*DATA*/", "const DATA=" + json.dumps({"S": S, "rows": table_rows, "PNAME": PNAME,
                                                                  "FNAME": FNAME, "TAGNAME": TAGNAME, "FAIL": FAIL,
                                                                  "PIPES": PIPES, "CATS": CATS}, ensure_ascii=False) + ";")
    open(OUT, "w").write(page)
    print("wrote", OUT, len(table_rows), "rows")
    print(json.dumps(S, ensure_ascii=False, default=str)[:3000])


TEMPLATE = open("tools/failure_report_template.html").read() if __name__ == "__main__" else ""

if __name__ == "__main__":
    main()
