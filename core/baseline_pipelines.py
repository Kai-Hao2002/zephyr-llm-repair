# core/baseline_pipelines.py
"""
B1/B2/B3 對照組 (見碩論提案 Table 2) 的執行迴圈——evaluate.py 的 --pipeline
參數選擇要跑哪一個，LLM 呼叫邏輯在 agents/baselines.py。

跟 Proposed pipeline (core/workflow.py) 共用底層工具 (PatchApplier/
QemuOracle/LogFilter/build_devops_docker_cmd/evaluate_repair_attempt)，但
刻意不重用 apply_patch_node/build_node 這兩個 LangGraph 節點：那兩個節點會
呼叫 agents/supervisor.py 的 record_attempt_outcome，把結果寫進
attempt_history (Proposed 的跨迭代記憶機制)。B1/B2 是單發、用不到；B3 依
Table 2 定義 ("no specialized multi-agent split") 刻意不給跨迭代記憶——
Context Compression 明確歸屬 Supervisor Node，屬於多代理人分工的一部分。
重用那兩個節點會產生用不到的壓縮 LLM 呼叫成本，還會讓「B3 沒有記憶」這個
實驗設計在程式碼裡沒有真的做到。

The execution loops for the B1/B2/B3 ablation baselines (see the thesis
proposal's Table 2) — evaluate.py's --pipeline flag picks which one runs;
the LLM-calling logic lives in agents/baselines.py.

Shares the underlying tools with the Proposed pipeline (core/workflow.py):
PatchApplier/QemuOracle/LogFilter/build_devops_docker_cmd/
evaluate_repair_attempt — but deliberately doesn't reuse the
apply_patch_node/build_node LangGraph nodes. Those nodes call
agents/supervisor.py's record_attempt_outcome, writing into attempt_history
(Proposed's cross-iteration memory). B1/B2 are single-shot and never read
it; B3, per Table 2's definition ("no specialized multi-agent split"),
deliberately gets no cross-iteration memory — Context Compression is
explicitly attributed to the Supervisor Node, part of the multi-agent split.
Reusing those nodes would incur an unused compression-LLM cost and would
leave "B3 has no memory" as a claim the code doesn't actually enforce.
"""
import os
from typing import Any, Dict

from core.state import ZephyrAgentState
from core.workflow import evaluate_repair_attempt
from tools.patch_applier import PatchApplier
from agents.patch_expert import collect_relevant_context_paths, MAX_PATCH_CONTEXT_CHARS
from agents.baselines import b1_generate_full_file_patch, b2_generate_patch, b3_generate_patch
from graph_rag.hybrid_retriever import HybridRetriever


def _read_context_files(workspace_path: str, target_app: str, error_log: str,
                         retrieved_files: list) -> str:
    """跟 agents/patch_expert.py 的 patch_node 完全同一套組裝 project_files_content
    的邏輯 (範圍限縮到 collect_relevant_context_paths 挑出的檔案、300k 字元上限)，
    抽出來讓 B2/B3 重用，不必各自重寫一份容易漂移的複本。"""
    content = ""
    total_chars = 0
    for rel_path in collect_relevant_context_paths(workspace_path, target_app, error_log, retrieved_files):
        filepath = os.path.join(workspace_path, rel_path)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                file_content = f.read()
        except Exception:
            continue
        if total_chars + len(file_content) > MAX_PATCH_CONTEXT_CHARS:
            break
        content += f"\n--- {rel_path} ---\n{file_content}\n"
        total_chars += len(file_content)
    return content


def _iteration_log_entry(current_iter: int, compiled: bool, resolved: bool,
                          tool_invocation_error: bool, usage_entries: list) -> Dict[str, Any]:
    """跟 agents/supervisor.py 的 _build_iteration_log_entry 完全同一個
    shape——results.json 的分析腳本 (analyze_results.py) 要能不分 pipeline
    地讀同一種 iteration_log 結構，Proposed 跟 B1/B2/B3 的紀錄格式不能各自
    長得不一樣。"""
    return {
        "iteration": current_iter,
        "compiled": compiled,
        "resolved": resolved,
        "tool_invocation_error": tool_invocation_error,
        "token_usage": usage_entries,
    }


def run_b1(state: ZephyrAgentState) -> Dict[str, Any]:
    """
    B1 Zero-Shot LLM：一次 LLM 呼叫、一次 apply、一次 build，不重試、
    不做 StaticCheck。"raw log as input, no tools, no RAG" 落實在
    agents.baselines.b1_generate_full_file_patch 裡只傳 error_log 一個
    欄位，這裡不額外讀任何檔案內容餵給它。B1 沒有 RAG，first_retrieval_files
    永遠不設定 (代表這個案例的 RQ2 檢索指標對 B1 不適用)。
    """
    workspace_path = state["workspace_path"]
    error_log = state.get("current_error_log", "")
    print("\n🧪 [B1 Zero-Shot] 只憑錯誤日誌，猜測要修正的檔案與內容...")

    patch, usage_entry = b1_generate_full_file_patch(error_log)
    applier = PatchApplier(workspace_path=workspace_path)
    apply_result = applier.apply_full_file(patch["filepath"], patch["content"])

    if not apply_result["success"]:
        print(f"   ❌ 套用失敗：{apply_result['error']}")
        return {
            "final_status": "failed_max_retries", "iterations": 1, "error_type": "patch_format_error",
            "iteration_log": [_iteration_log_entry(1, False, False, True, [usage_entry])],
        }

    eval_result = evaluate_repair_attempt(
        workspace_path, state["board"], state["target_app"], state.get("required_pass_test")
    )
    final_status = "resolved" if eval_result["resolved"] else "failed_max_retries"
    print(f"   {'🎉' if eval_result['resolved'] else '💥'} 建置/執行結果：{eval_result['status']}")
    return {
        "final_status": final_status, "iterations": 1, "error_type": eval_result["status"],
        "iteration_log": [_iteration_log_entry(1, eval_result["compiled"], eval_result["resolved"], False, [usage_entry])],
    }


def run_b2(state: ZephyrAgentState) -> Dict[str, Any]:
    """
    B2 Single Agent + Text RAG (BM25-only)：一次 LLM 呼叫、一次 apply、
    一次 build，不重試。檢索直接拿 current_error_log 全文當查詢字串
    (B2 沒有 Proposed 的 Analyzer 角色去提取 search_keywords，那本身就是
    多代理人分工的一部分，B2 不該有)。單發、沒有閉環重試，這次檢索天生
    就是「第一次也是唯一一次」，直接記進 first_retrieval_files。
    """
    workspace_path = state["workspace_path"]
    target_app = state["target_app"]
    error_log = state.get("current_error_log", "")
    print("\n🧪 [B2 Single-Agent+RAG] BM25-only 檢索候選檔案，生成修補...")

    retriever = HybridRetriever(workspace_path)
    retrieved_files = retriever.retrieve(error_log, top_k=8, bm25_only=True)
    if retrieved_files:
        print(f"   ↳ BM25 檢索到候選檔案：{retrieved_files}")

    project_files_content = _read_context_files(workspace_path, target_app, error_log, retrieved_files)
    patch_text, usage_entry = b2_generate_patch(error_log, project_files_content)

    applier = PatchApplier(workspace_path=workspace_path)
    apply_result = applier.apply_patches(patch_text)
    if not apply_result["success"]:
        print(f"   ❌ 套用失敗：{apply_result['error']}")
        return {
            "final_status": "failed_max_retries", "iterations": 1, "error_type": "patch_format_error",
            "iteration_log": [_iteration_log_entry(1, False, False, True, [usage_entry])],
            "first_retrieval_files": retrieved_files,
        }

    eval_result = evaluate_repair_attempt(
        workspace_path, state["board"], state["target_app"], state.get("required_pass_test")
    )
    final_status = "resolved" if eval_result["resolved"] else "failed_max_retries"
    print(f"   {'🎉' if eval_result['resolved'] else '💥'} 建置/執行結果：{eval_result['status']}")
    return {
        "final_status": final_status, "iterations": 1, "error_type": eval_result["status"],
        "iteration_log": [_iteration_log_entry(1, eval_result["compiled"], eval_result["resolved"], False, [usage_entry])],
        "first_retrieval_files": retrieved_files,
    }


def run_b3(state: ZephyrAgentState, max_iters: int) -> Dict[str, Any]:
    """
    B3 Closed-Loop Single Agent：單一 LLM persona 身兼診斷+修補，重複
    apply→build 直到成功或用完 max_iters；不做 StaticCheck (DevOps Expert
    的專門化分工)、不給 RAG (first_retrieval_files 永遠不設定)、不給跨迭代
    記憶 (每次只帶最新 error_log，不是完整歷史——見 agents/baselines.py 的
    b3_generate_patch 說明)。
    """
    workspace_path = state["workspace_path"]
    target_app = state["target_app"]
    board = state["board"]
    required_pass_test = state.get("required_pass_test")
    error_log = state.get("current_error_log", "")
    iteration_log = []

    for current_iter in range(1, max_iters + 1):
        print(f"\n🧪 [B3 Closed-Loop] 第 {current_iter}/{max_iters} 次迭代...")
        project_files_content = _read_context_files(workspace_path, target_app, error_log, [])
        patch_text, usage_entry = b3_generate_patch(error_log, project_files_content)

        applier = PatchApplier(workspace_path=workspace_path)
        apply_result = applier.apply_patches(patch_text)
        if not apply_result["success"]:
            print(f"   ❌ 套用失敗：{apply_result['error']}")
            error_log = f"Patch Application Failed:\n{apply_result['error']}"
            iteration_log.append(_iteration_log_entry(current_iter, False, False, True, [usage_entry]))
            if current_iter >= max_iters:
                return {"final_status": "failed_max_retries", "iterations": current_iter,
                        "error_type": "patch_format_error", "iteration_log": iteration_log}
            continue

        eval_result = evaluate_repair_attempt(workspace_path, board, target_app, required_pass_test)
        iteration_log.append(_iteration_log_entry(
            current_iter, eval_result["compiled"], eval_result["resolved"], False, [usage_entry]
        ))
        if eval_result["resolved"]:
            print("   🎉 執行期驗證通過！")
            return {"final_status": "resolved", "iterations": current_iter, "error_type": "success",
                    "iteration_log": iteration_log}

        print(f"   💥 建置/執行失敗 (狀態: {eval_result['status']})")
        error_log = eval_result["log"]
        if current_iter >= max_iters:
            return {"final_status": "failed_max_retries", "iterations": current_iter,
                    "error_type": eval_result["status"], "iteration_log": iteration_log}

    return {"final_status": "failed_max_retries", "iterations": max_iters, "error_type": "unknown",
            "iteration_log": iteration_log}
