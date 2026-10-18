# core/baseline_pipelines.py
"""
B1/B2/B3 對照組 (見碩論提案 Table 2) 的執行迴圈——evaluate.py 的 --pipeline
參數選擇要跑哪一個，LLM 呼叫邏輯在 agents/baselines.py。

跟 Proposed pipeline (core/workflow.py) 共用底層工具 (PatchApplier/
QemuOracle/LogFilter/build_devops_docker_cmd/evaluate_repair_attempt)，但
刻意不重用 apply_patch_node/devops_node/qa_node 這幾個 LangGraph 節點：
那幾個節點會呼叫 agents/supervisor.py 的 record_attempt_outcome，把結果
寫進 attempt_history (Proposed 的跨迭代記憶機制)。B1/B2 是單發、用不到；
B3 不經過 Supervisor 的 LLM 壓縮記憶 (Context Compression 屬於多代理人
分工)；2026-10-13 起 B3 自己保留原文截斷的前幾輪 patch 與結果 (見 run_b3)。B1/B2/B3 也同樣沒有 DevOps
Expert/QA Expert 這種角色分工 (跟 Proposed 相比，這正是 Table 2 要對照的
變因之一)，所以繼續共用同一個 evaluate_repair_attempt() 呼叫拿到「編譯+
執行」合併後的單一結果，不像 Proposed 拆成 devops_node/qa_node 兩個階段。

The execution loops for the B1/B2/B3 ablation baselines (see the thesis
proposal's Table 2) — evaluate.py's --pipeline flag picks which one runs;
the LLM-calling logic lives in agents/baselines.py.

Shares the underlying tools with the Proposed pipeline (core/workflow.py):
PatchApplier/QemuOracle/LogFilter/build_devops_docker_cmd/
evaluate_repair_attempt — but deliberately doesn't reuse the
apply_patch_node/devops_node/qa_node LangGraph nodes. Those nodes call
agents/supervisor.py's record_attempt_outcome, writing into attempt_history
(Proposed's cross-iteration memory). B1/B2 are single-shot and never read
it; B3 does not get the Supervisor's LLM-compressed memory (Context Compression
belongs to the multi-agent split); from 2026-10-13 B3 keeps its own
mechanically truncated history of earlier patches and outcomes (see run_b3).
B1/B2/B3 also have no DevOps Expert/QA Expert role split at all (exactly
the variable Table 2 measures relative to Proposed), so they keep sharing
one evaluate_repair_attempt() call that returns the combined build+execute
result in one shot, rather than Proposed's two-stage devops_node/qa_node.
"""
import os
from typing import Any, Dict, List

from core.state import ZephyrAgentState
from core.workflow import evaluate_repair_attempt, with_last_build_log
from core.trajectory import build_trajectory
from core.protected_files import pop_restored, restore_reminder
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
                          tool_invocation_error: bool, usage_entries: list,
                          trajectory: Dict[str, Any] = None) -> Dict[str, Any]:
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
        "trajectory": trajectory,
    }


def _trajectory(workspace_path: str, stage: str, status: str, **fields) -> Dict[str, Any]:
    """build_trajectory 加上這一輪被評測端還原的受保護檔案 (見 core/protected_files.py)。
    build_trajectory plus the protected files the evaluator restored this iteration (see core/protected_files.py)."""
    return build_trajectory(stage, status, restored_files=pop_restored(workspace_path), **fields)


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
    print("\n🧪 [B1 Zero-Shot] Guessing the file and content to fix from the error log alone...")

    patch, usage_entry = b1_generate_full_file_patch(error_log)
    applier = PatchApplier(workspace_path=workspace_path)
    apply_result = applier.apply_full_file(patch["filepath"], patch["content"])

    if not apply_result["success"]:
        print(f"   ❌ Patch application failed: {apply_result['error']}")
        return {
            "final_status": "failed_max_retries", "iterations": 1, "error_type": "patch_format_error",
            "iteration_log": [_iteration_log_entry(1, False, False, True, [usage_entry], _trajectory(workspace_path,
                "apply", "patch_format_error", patch=patch, applied_files=apply_result.get("applied_files", []), apply_error=apply_result["error"],
            ))],
        }

    eval_result = evaluate_repair_attempt(
        workspace_path, state["board"], state["target_app"], state.get("required_pass_test")
    )
    final_status = "resolved" if eval_result["resolved"] else "failed_max_retries"
    print(f"   {'🎉' if eval_result['resolved'] else '💥'} Build/run result: {eval_result['status']}")
    return {
        "final_status": final_status, "iterations": 1, "error_type": eval_result["status"],
        "iteration_log": [_iteration_log_entry(
            1, eval_result["compiled"], eval_result["resolved"], False, [usage_entry],
            _trajectory(workspace_path, "run" if eval_result["compiled"] else "build", eval_result["status"], patch=patch,
                             applied_files=apply_result["applied_files"],
                             filtered_log=eval_result["log"]),
        )],
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
    print("\n🧪 [B2 Single-Agent+RAG] Retrieving candidate files with BM25 only, generating the patch...")

    retriever = HybridRetriever(workspace_path)
    retrieved_files = retriever.retrieve(error_log, top_k=8, bm25_only=True)
    if retrieved_files:
        print(f"   ↳ BM25 retrieved candidate files: {retrieved_files}")

    project_files_content = _read_context_files(workspace_path, target_app, error_log, retrieved_files)
    patch_text, usage_entry = b2_generate_patch(error_log, project_files_content)

    applier = PatchApplier(workspace_path=workspace_path)
    apply_result = applier.apply_patches(patch_text)
    if not apply_result["success"]:
        print(f"   ❌ Patch application failed: {apply_result['error']}")
        return {
            "final_status": "failed_max_retries", "iterations": 1, "error_type": "patch_format_error",
            "iteration_log": [_iteration_log_entry(1, False, False, True, [usage_entry], _trajectory(workspace_path,
                "apply", "patch_format_error", patch=patch_text, applied_files=apply_result.get("applied_files", []),
                apply_error=apply_result["error"], retrieved_files=retrieved_files,
            ))],
            "first_retrieval_files": retrieved_files,
        }

    eval_result = evaluate_repair_attempt(
        workspace_path, state["board"], state["target_app"], state.get("required_pass_test")
    )
    final_status = "resolved" if eval_result["resolved"] else "failed_max_retries"
    print(f"   {'🎉' if eval_result['resolved'] else '💥'} Build/run result: {eval_result['status']}")
    return {
        "final_status": final_status, "iterations": 1, "error_type": eval_result["status"],
        "iteration_log": [_iteration_log_entry(
            1, eval_result["compiled"], eval_result["resolved"], False, [usage_entry],
            _trajectory(workspace_path, "run" if eval_result["compiled"] else "build", eval_result["status"], patch=patch_text,
                             applied_files=apply_result["applied_files"],
                             filtered_log=eval_result["log"], retrieved_files=retrieved_files),
        )],
        "first_retrieval_files": retrieved_files,
    }


# B3 歷史的機械式截斷上限 (每輪 patch / 每輪結果 log)。Mechanical truncation limits for B3's history.
_B3_HISTORY_PATCH_CHARS = 6000
_B3_HISTORY_LOG_CHARS = 3000


def _clip_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n…[{len(text) - limit} chars omitted]…\n{text[-half:]}"


def _render_b3_history(history: List[Dict[str, Any]]) -> str:
    parts = []
    for h in history:
        parts.append(f"--- Attempt {h['iteration']} ---\n[Patch]\n{_clip_middle(h['patch'] or '', _B3_HISTORY_PATCH_CHARS)}\n"
                     f"[Outcome: {h['outcome']}]\n{_clip_middle(h['log'] or '', _B3_HISTORY_LOG_CHARS)}")
    return "\n\n".join(parts)


def run_b3(state: ZephyrAgentState, max_iters: int) -> Dict[str, Any]:
    """
    B3 Closed-Loop Single Agent：單一 LLM persona 身兼診斷+修補，重複
    apply→build 直到成功或用完 max_iters；不做 StaticCheck (DevOps Expert
    的專門化分工)、不給 RAG (first_retrieval_files 永遠不設定)。2026-10-13 起
    每輪附上這個 session 前幾輪的 patch 與結果 (原文機械式截斷，不經 LLM 壓縮)，
    見 agents/baselines.py 的 b3_generate_patch 說明；patch 套用失敗時，最近一次
    build/run 的 log 附在失敗訊息後面 (core/workflow.with_last_build_log)。
    """
    workspace_path = state["workspace_path"]
    target_app = state["target_app"]
    board = state["board"]
    required_pass_test = state.get("required_pass_test")
    error_log = state.get("current_error_log", "")
    last_build_log = error_log
    history: List[Dict[str, Any]] = []
    iteration_log = []

    for current_iter in range(1, max_iters + 1):
        print(f"\n🧪 [B3 Closed-Loop] Iteration {current_iter}/{max_iters}...")
        project_files_content = _read_context_files(workspace_path, target_app, error_log, [])
        patch_text, usage_entry = b3_generate_patch(error_log, project_files_content, _render_b3_history(history))

        applier = PatchApplier(workspace_path=workspace_path)
        apply_result = applier.apply_patches(patch_text)
        if not apply_result["success"]:
            print(f"   ❌ Patch application failed: {apply_result['error']}")
            failure = f"Patch Application Failed:\n{apply_result['error']}"
            history.append({"iteration": current_iter, "patch": patch_text,
                            "outcome": "patch application failed (nothing was built)", "log": failure})
            error_log = with_last_build_log(failure, last_build_log, "from before this patch; nothing was built after it")
            iteration_log.append(_iteration_log_entry(current_iter, False, False, True, [usage_entry], _trajectory(workspace_path,
                "apply", "patch_format_error", patch=patch_text, applied_files=apply_result.get("applied_files", []), apply_error=apply_result["error"],
            )))
            if current_iter >= max_iters:
                return {"final_status": "failed_max_retries", "iterations": current_iter,
                        "error_type": "patch_format_error", "iteration_log": iteration_log}
            continue

        eval_result = evaluate_repair_attempt(workspace_path, board, target_app, required_pass_test)
        iteration_log.append(_iteration_log_entry(
            current_iter, eval_result["compiled"], eval_result["resolved"], False, [usage_entry],
            _trajectory(workspace_path, "run" if eval_result["compiled"] else "build", eval_result["status"], patch=patch_text,
                             applied_files=apply_result["applied_files"], filtered_log=eval_result["log"]),
        ))
        if eval_result["resolved"]:
            print("   🎉 Runtime verification passed!")
            return {"final_status": "resolved", "iterations": current_iter, "error_type": "success",
                    "iteration_log": iteration_log}

        print(f"   💥 Build/run failed (status: {eval_result['status']})")
        # 規格檔被還原時提醒 agent (2026-10-18，見 core/protected_files.restore_reminder)。
        # Remind the agent when specification files were reverted (2026-10-18; see core/protected_files.restore_reminder).
        reminder = restore_reminder(iteration_log[-1]["trajectory"].get("restored_files") or [])
        last_build_log = eval_result["log"]
        error_log = reminder + eval_result["log"]
        history.append({"iteration": current_iter, "patch": patch_text,
                        "outcome": f"applied to {apply_result['applied_files']}; build/run failed with status {eval_result['status']}",
                        "log": error_log})
        if current_iter >= max_iters:
            return {"final_status": "failed_max_retries", "iterations": current_iter,
                    "error_type": eval_result["status"], "iteration_log": iteration_log}

    return {"final_status": "failed_max_retries", "iterations": max_iters, "error_type": "unknown",
            "iteration_log": iteration_log}
