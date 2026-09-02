# analyze_results.py
"""
把 evaluate.py 產出的 results.json 原始紀錄，換算成論文提案 RQ1-RQ4 需要的
指標。刻意跟 evaluate.py 分開：results.json 只存原始的 per-iteration 紀錄
(iteration_log/first_retrieval_files/ttr_seconds)，所有衍生指標的計算邏輯
集中在這支腳本，不在兩處各寫一份容易漂移的統計公式。

RQ2 的 ground truth (injection.target_file / injections[*].target_file)
只在這裡讀取，用來對照 first_retrieval_files 算名次——這支腳本是負責打分
的外層，不是 agent 本身，讀取這些欄位不違反 README 的「agent 永遠看不到
injection 資訊」規則 (跟 evaluate.py 的 verify_reproduces_initial_failure/
required_pass_test 評分邏輯同一個原則)。

Turns evaluate.py's raw results.json records into the metrics the thesis
proposal's RQ1-RQ4 need. Deliberately separate from evaluate.py:
results.json stores only raw per-iteration records
(iteration_log/first_retrieval_files/ttr_seconds); all derived-metric
computation lives here, not duplicated in two drifting places.

RQ2's ground truth (injection.target_file / injections[*].target_file) is
read only here, to rank against first_retrieval_files — this script is the
grading layer, not the agent itself, so reading these fields doesn't
violate README's "the agent never sees injection info" rule (same
principle as evaluate.py's verify_reproduces_initial_failure/
required_pass_test grading).
"""
import argparse
import glob
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

DEFAULT_DATASET_PATH = os.path.join("dataset", "cases", "final_dataset.json")

# Recall@k / Top-k Accuracy 要報告的 k 值——8 是 Hybrid RAG (Knowledge
# Expert) 現有 top_k 設定 (graph_rag/hybrid_retriever.py)，1/3/5 拿來看
# 名次分布是不是集中在很前面。
# The k values reported for Recall@k / Top-k Accuracy — 8 matches Hybrid
# RAG's (Knowledge Expert's) existing top_k setting
# (graph_rag/hybrid_retriever.py); 1/3/5 show whether ranks cluster near
# the very top.
RETRIEVAL_K_VALUES = (1, 3, 5, 8)


def load_results(paths: List[str]) -> List[Dict[str, Any]]:
    """paths 可以是檔案，也可以是目錄 (該目錄下所有 results.json 都會被讀
    進來，方便一次分析多次 evaluate.py --runs-dir 產出的結果)。"""
    records: List[Dict[str, Any]] = []
    for path in paths:
        files = [path] if os.path.isfile(path) else sorted(glob.glob(os.path.join(path, "**", "results.json"), recursive=True))
        for f in files:
            with open(f, "r", encoding="utf-8") as fh:
                records.extend(json.load(fh))
    return records


def load_ground_truth(dataset_path: str) -> Dict[str, List[str]]:
    """case_id -> 這個案例所有被注入檔案的相對路徑列表 (單一注入案例是
    1 個元素，compound 案例是多個)。"""
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    ground_truth = {}
    for case in dataset:
        if "injections" in case:
            ground_truth[case["id"]] = [inj["target_file"] for inj in case["injections"]]
        elif "injection" in case:
            ground_truth[case["id"]] = [case["injection"]["target_file"]]
    return ground_truth


def _rank_of_first_relevant(retrieved: List[str], relevant: List[str]) -> Optional[int]:
    """retrieved 裡第一個出現在 relevant 集合裡的檔案，其名次 (從 1 開始)；
    完全沒找到回傳 None。"""
    relevant_set = set(relevant)
    for i, path in enumerate(retrieved):
        if path in relevant_set:
            return i + 1
    return None


def compute_retrieval_metrics(records: List[Dict[str, Any]], ground_truth: Dict[str, List[str]]) -> Dict[str, Any]:
    """
    RQ2：MRR、Recall@k、Top-k Accuracy。只看 first_retrieval_files 不是
    None 的案例 (B1/B3 沒有 RAG，或 Proposed/B2 這次案例從沒觸發過檢索的，
    都不計入分母——這些案例的「檢索品質」本來就無從定義)。

    Recall@k：每個案例「這次注入的檔案裡，有幾個出現在 retrieved 前 k 名」
    除以「這次案例總共注入了幾個檔案」，取所有案例的平均——compound 案例
    (多檔案) 用這個定義才有意義，single-injection 案例則等同於「找到了
    沒」(0 或 1)。
    Top-k Accuracy：「前 k 名裡有沒有命中至少一個被注入的檔案」，案例層級
    的 0/1，取平均——不管 compound 案例注入了幾個檔案，只要前 k 名命中一個
    就算對，衡量的是「有沒有把 agent 導向正確方向」而不是「是否找齊全部」。
    """
    ranked_cases = []
    for r in records:
        first_retrieval = r.get("first_retrieval_files")
        if first_retrieval is None:
            continue
        relevant = ground_truth.get(r["case_id"])
        if not relevant:
            continue
        rank = _rank_of_first_relevant(first_retrieval, relevant)
        hit_at_k = {k: (rank is not None and rank <= k) for k in RETRIEVAL_K_VALUES}
        recall_at_k = {
            k: len({p for p in first_retrieval[:k] if p in relevant}) / len(relevant)
            for k in RETRIEVAL_K_VALUES
        }
        ranked_cases.append({"case_id": r["case_id"], "rank": rank, "hit_at_k": hit_at_k, "recall_at_k": recall_at_k})

    n = len(ranked_cases)
    if n == 0:
        return {"n_cases": 0, "mrr": None, "top_k_accuracy": {}, "recall_at_k": {}}

    mrr = sum((1.0 / c["rank"]) if c["rank"] else 0.0 for c in ranked_cases) / n
    top_k_accuracy = {k: sum(c["hit_at_k"][k] for c in ranked_cases) / n for k in RETRIEVAL_K_VALUES}
    recall_at_k = {k: sum(c["recall_at_k"][k] for c in ranked_cases) / n for k in RETRIEVAL_K_VALUES}
    return {"n_cases": n, "mrr": mrr, "top_k_accuracy": top_k_accuracy, "recall_at_k": recall_at_k}


def compute_loop_metrics(records: List[Dict[str, Any]], max_k: int) -> Dict[str, Any]:
    """
    RQ1/RQ3：Bounded Compilation Success Rate (Pass@k)、Functional Pass
    Rate、平均迭代次數。只看有 iteration_log 的案例 (final_status=="error"
    的環境層失敗，例如 API 逾時、workspace 準備失敗，排除在外，另外單獨
    報告 error 數量——那些不是 agent 修復能力的訊號)。

    Pass@k：前 k 次迭代裡，有沒有任何一次真的編譯出執行檔 (compiled=True)
    ——跟 Functional Pass Rate (resolved=True，還要通過執行期/指定測試)
    是兩個獨立指標，見 core/workflow.py 的 evaluate_repair_attempt。
    """
    completed = [r for r in records if r.get("iteration_log") is not None]
    error_count = len(records) - len(completed)
    n = len(completed)
    if n == 0:
        return {"n_cases": 0, "n_errors": error_count, "pass_at_k": {}, "functional_pass_rate": None, "mean_iterations": None}

    pass_at_k = {}
    for k in range(1, max_k + 1):
        hits = sum(
            1 for r in completed
            if any(entry["compiled"] for entry in r["iteration_log"][:k])
        )
        pass_at_k[k] = hits / n

    functional_pass_rate = sum(1 for r in completed if r.get("final_status") == "resolved") / n
    mean_iterations = sum(r.get("iterations", 0) for r in completed) / n

    return {
        "n_cases": n, "n_errors": error_count, "pass_at_k": pass_at_k,
        "functional_pass_rate": functional_pass_rate, "mean_iterations": mean_iterations,
    }


def compute_cost_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    RQ4：Token Efficiency (平均每案例花費的 input/output token)、Tool
    Invocation Error Rate (所有迭代裡，patch 格式本身無效的比例)。API 成本
    (美金) 需要各模型的計價表才能換算，等第 3 項 (多模型支援) 做完才接，
    這裡先只算原始 token 數。

    CI/CD 實務面的 TTR (Time-to-Repair) 平均值也在這裡一併算。
    """
    completed = [r for r in records if r.get("iteration_log") is not None]
    n = len(completed)
    if n == 0:
        return {"n_cases": 0, "mean_input_tokens": None, "mean_output_tokens": None,
                "tool_invocation_error_rate": None, "mean_ttr_seconds": None}

    total_input = total_output = 0
    total_iterations = tool_errors = 0
    for r in completed:
        for entry in r["iteration_log"]:
            total_iterations += 1
            if entry.get("tool_invocation_error"):
                tool_errors += 1
            for usage in entry.get("token_usage", []):
                total_input += usage.get("input_tokens", 0)
                total_output += usage.get("output_tokens", 0)

    mean_ttr = sum(r.get("ttr_seconds", 0.0) for r in completed) / n
    return {
        "n_cases": n,
        "mean_input_tokens": total_input / n,
        "mean_output_tokens": total_output / n,
        "tool_invocation_error_rate": (tool_errors / total_iterations) if total_iterations else None,
        "mean_ttr_seconds": mean_ttr,
    }


def summarize(records: List[Dict[str, Any]], ground_truth: Dict[str, List[str]], max_k: int) -> Dict[str, Any]:
    by_pipeline = defaultdict(list)
    for r in records:
        by_pipeline[r.get("pipeline", "unknown")].append(r)

    summary = {}
    for pipeline, recs in sorted(by_pipeline.items()):
        summary[pipeline] = {
            "loop_metrics": compute_loop_metrics(recs, max_k),
            "retrieval_metrics": compute_retrieval_metrics(recs, ground_truth),
            "cost_metrics": compute_cost_metrics(recs),
        }
    return summary


def _fmt_pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _fmt_num(x: Optional[float], digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def print_summary(summary: Dict[str, Any], max_k: int) -> None:
    for pipeline, metrics in summary.items():
        loop = metrics["loop_metrics"]
        retrieval = metrics["retrieval_metrics"]
        cost = metrics["cost_metrics"]
        print(f"\n=== {pipeline} (n={loop['n_cases']}, errors={loop.get('n_errors', 0)}) ===")
        print("[RQ1/RQ3] Bounded Compilation Success Rate (Pass@k):")
        for k in range(1, max_k + 1):
            if k in loop["pass_at_k"]:
                print(f"    Pass@{k}: {_fmt_pct(loop['pass_at_k'][k])}")
        print(f"  Functional Pass Rate: {_fmt_pct(loop['functional_pass_rate'])}")
        print(f"  Mean iterations: {_fmt_num(loop['mean_iterations'], 2)}")
        print(f"[RQ2] Retrieval quality (n={retrieval['n_cases']} cases with a first-firing retrieval):")
        print(f"  MRR: {_fmt_num(retrieval['mrr'], 3)}")
        for k in RETRIEVAL_K_VALUES:
            if k in retrieval["top_k_accuracy"]:
                print(f"    Top-{k} Accuracy: {_fmt_pct(retrieval['top_k_accuracy'][k])}  Recall@{k}: {_fmt_pct(retrieval['recall_at_k'][k])}")
        print("[RQ4 / CI-CD] Cost & reliability:")
        print(f"  Mean input tokens/case: {_fmt_num(cost['mean_input_tokens'], 0)}")
        print(f"  Mean output tokens/case: {_fmt_num(cost['mean_output_tokens'], 0)}")
        print(f"  Tool Invocation Error Rate: {_fmt_pct(cost['tool_invocation_error_rate'])}")
        print(f"  Mean TTR: {_fmt_num(cost['mean_ttr_seconds'], 1)}s")


def main():
    parser = argparse.ArgumentParser(description="Compute RQ1-RQ4 metrics from evaluate.py's results.json output.")
    parser.add_argument("results", nargs="+", help="One or more results.json files, or directories to search recursively.")
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH, help="Path to the cases JSON file (for RQ2 ground truth).")
    parser.add_argument("--max-k", type=int, default=5, help="Max k to report for Pass@k (should match --max-retries used at eval time).")
    parser.add_argument("--out", help="Optional path to also write the summary as JSON.")
    args = parser.parse_args()

    records = load_results(args.results)
    if not records:
        raise SystemExit(f"no results found under {args.results}")
    ground_truth = load_ground_truth(args.dataset)

    summary = summarize(records, ground_truth, args.max_k)
    print_summary(summary, args.max_k)

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"\nSummary written to {args.out}")


if __name__ == "__main__":
    main()
