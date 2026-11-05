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

Baseline 定義 (2026-11-05 起，"2026-11-hybrid")：B1 → B2 → B3 → Proposed 每一步只多一個變因。
B2 = 單一 agent + Hybrid RAG (單發)；B3 = B2 + 閉環重試 (每輪都做同樣的 Hybrid 檢索)；Proposed 再加上
多代理人分工。B2/B3 的檢索和 agents/knowledge_expert.py 沒有關鍵字時的路徑相同 (同一個
get_hybrid_retriever、top_k=8、查詢用最近一次過濾後的 build/run log)。之前的定義 B2 是 BM25-only、
B3 沒有檢索，2.5 Flash 的 v3 結果用的是舊定義。
Baseline definition (from 2026-11-05, "2026-11-hybrid"): each step B1 → B2 → B3 → Proposed adds one variable.
B2 = single agent + Hybrid RAG (single shot); B3 = B2 + closed-loop retries (the same Hybrid retrieval every
iteration); Proposed adds the multi-agent split. B2/B3 retrieve exactly like agents/knowledge_expert.py's
no-keyword path (same get_hybrid_retriever, top_k=8, query = most recent filtered build/run log). Under the
previous definition B2 was BM25-only and B3 had no retrieval; the 2.5 Flash v3 results use that definition.
"""
import logging
import os
from typing import Any, Dict, List

from core.state import ZephyrAgentState
from core.workflow import _rollback_patch, evaluate_repair_attempt, with_last_build_log
from core.proposed_opt import B3_GATE, MAX_GATE_RETRIES_PER_ITERATION
from core.trajectory import build_trajectory
from core.protected_files import is_protected, pop_restored, restore_reminder
from core.llm_retry import RetrievalDegradedError, record_fallback, strict_fallbacks
from tools.patch_applier import PatchApplier
from agents.patch_expert import collect_relevant_context_paths, MAX_PATCH_CONTEXT_CHARS
from agents.baselines import b1_generate_full_file_patch, b2_generate_patch, b3_generate_patch
from agents.knowledge_expert import get_hybrid_retriever

logger = logging.getLogger(__name__)

# 寫進 run_meta，區分新舊 baseline 定義。Recorded in run_meta to tell the baseline definitions apart.
BASELINE_DEFINITION = "2026-11-hybrid"


def _hybrid_retrieve(workspace_path: str, query: str) -> List[str]:
    """和 agents/knowledge_expert.py 的 Hybrid RAG 呼叫與失敗處理相同：評測 (strict) 模式下失敗就
    raise RetrievalDegradedError，不默默跳過。
    Same Hybrid RAG call and failure handling as agents/knowledge_expert.py: in evaluation (strict)
    mode a failure raises RetrievalDegradedError instead of being skipped."""
    try:
        retrieved_files = get_hybrid_retriever(workspace_path).retrieve(query, top_k=8)
    except Exception as e:
        if isinstance(e, RetrievalDegradedError):
            raise
        record_fallback("hybrid_retrieval", e)
        if strict_fallbacks():
            raise RetrievalDegradedError(f"Hybrid RAG retrieval failed and strict mode forbids skipping it: {e}") from e
        logger.warning(f"Hybrid RAG retrieval failed, skipping: {e}")
        return []
    if retrieved_files:
        print(f"   ↳ Hybrid RAG (BM25 + semantic) retrieved candidate files: {retrieved_files}")
    return retrieved_files


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
    B2 Single Agent + Hybrid retrieval：一次 LLM 呼叫、一次 apply、一次 build，不重試。
    檢索和 Proposed 的 Knowledge Expert 相同 (Hybrid RAG，見 _hybrid_retrieve)，查詢用過濾後的 log，
    等同 Proposed 沒有 Analyzer 關鍵字時的退路 (B2 沒有 Analyzer，提取關鍵字屬於多代理人分工)。
    單發，這次檢索就是第一次也是唯一一次，直接記進 first_retrieval_files。
    B2 Single Agent + Hybrid retrieval: one LLM call, one apply, one build, no retries. Retrieval is the
    Knowledge Expert's Hybrid RAG (see _hybrid_retrieve), queried with the filtered log, i.e. Proposed's
    fallback when the Analyzer gives no keywords (B2 has no Analyzer; keyword extraction is part of the
    multi-agent split). Single shot, so this retrieval is the first and only one: first_retrieval_files.
    """
    workspace_path = state["workspace_path"]
    target_app = state["target_app"]
    error_log = state.get("current_error_log", "")
    print("\n🧪 [B2 Single-Agent+Hybrid RAG] Retrieving candidate files, generating the patch...")

    retrieved_files = _hybrid_retrieve(workspace_path, state.get("last_build_log") or error_log)

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
    B3 Closed-Loop Single Agent + Hybrid retrieval：單一 LLM persona 身兼診斷+修補，重複
    apply→build 直到成功或用完 max_iters；不做 StaticCheck (DevOps Expert 的專門化分工)。
    2026-11-05 起每一輪 (包含套用閘門的重試) 都做和 B2 相同的 Hybrid 檢索，查詢用 last_build_log
    (最近一次過濾後的 build/run log，不含規格檔還原提醒和 patch 套用失敗訊息)，同 knowledge_expert；
    第一次檢索寫進 first_retrieval_files，每輪的 trajectory 記 retrieved_files。之前 B3 不給檢索。
    From 2026-11-05 every iteration (gate retries included) does B2's Hybrid retrieval, queried with
    last_build_log (most recent filtered build/run log, without the restore reminder or patch-application
    failure text), as in knowledge_expert; the first retrieval goes into first_retrieval_files and each
    iteration's trajectory records retrieved_files. B3 previously had no retrieval. 2026-10-13 起
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
    first_retrieval_files = None

    def _done(**fields) -> Dict[str, Any]:
        return {**fields, "iteration_log": iteration_log, "first_retrieval_files": first_retrieval_files}

    for current_iter in range(1, max_iters + 1):
        print(f"\n🧪 [B3 Closed-Loop] Iteration {current_iter}/{max_iters}...")
        # 套用閘門 (2026-10-31，ZEPHYR_B3_GATE)：和優化版 Proposed 相同，不合格的 patch 還原後在本輪重試，
        # 不消耗迭代；關閉時第一次就離開迴圈，行為和之前相同。
        # Apply gate (ZEPHYR_B3_GATE): as in optimized Proposed, a rejected patch is rolled back and retried
        # within the iteration; with the switch off the loop exits on the first pass, as before.
        usages: List[Dict[str, Any]] = []
        gate_events: List[str] = []
        while True:
            retrieved_files = _hybrid_retrieve(workspace_path, last_build_log)
            if first_retrieval_files is None:
                first_retrieval_files = retrieved_files
            project_files_content = _read_context_files(workspace_path, target_app, error_log, retrieved_files)
            patch_text, usage_entry = b3_generate_patch(error_log, project_files_content, _render_b3_history(history))
            usages.append(usage_entry)

            applier = PatchApplier(workspace_path=workspace_path)
            apply_result = applier.apply_patches(patch_text)
            if not B3_GATE:
                break
            applied = apply_result.get("applied_files", [])
            gate_reason = None
            if not apply_result["success"]:
                gate_reason = f"Patch Application Failed:\n{apply_result['error']}"
                _rollback_patch(workspace_path, apply_result.get("original_contents", {}))
            elif applied and all(is_protected(workspace_path, f) for f in applied):
                gate_reason = restore_reminder(sorted(set(applied))).strip()
            if not gate_reason or len(gate_events) >= MAX_GATE_RETRIES_PER_ITERATION:
                break
            print(f"   ↩️ Patch rejected before building (retry {len(gate_events) + 1}/{MAX_GATE_RETRIES_PER_ITERATION} "
                  f"in iteration {current_iter}).")
            _rollback_patch(workspace_path, apply_result.get("original_contents", {}))
            error_log = with_last_build_log(gate_reason, last_build_log, "from before this patch; nothing was built after it")
            gate_events.append(gate_reason[:300])

        if not apply_result["success"]:
            print(f"   ❌ Patch application failed: {apply_result['error']}")
            failure = f"Patch Application Failed:\n{apply_result['error']}"
            history.append({"iteration": current_iter, "patch": patch_text,
                            "outcome": "patch application failed (nothing was built)", "log": failure})
            error_log = with_last_build_log(failure, last_build_log, "from before this patch; nothing was built after it")
            iteration_log.append(_iteration_log_entry(current_iter, False, False, True, usages, _trajectory(workspace_path,
                "apply", "patch_format_error", patch=patch_text, applied_files=apply_result.get("applied_files", []), apply_error=apply_result["error"],
                retrieved_files=retrieved_files,
            )))
            if B3_GATE:
                iteration_log[-1]["trajectory"]["gate_events"] = gate_events
            if current_iter >= max_iters:
                return _done(final_status="failed_max_retries", iterations=current_iter, error_type="patch_format_error")
            continue

        eval_result = evaluate_repair_attempt(workspace_path, board, target_app, required_pass_test)
        iteration_log.append(_iteration_log_entry(
            current_iter, eval_result["compiled"], eval_result["resolved"], False, usages,
            _trajectory(workspace_path, "run" if eval_result["compiled"] else "build", eval_result["status"], patch=patch_text,
                             applied_files=apply_result["applied_files"], filtered_log=eval_result["log"],
                             retrieved_files=retrieved_files),
        ))
        if B3_GATE:
            iteration_log[-1]["trajectory"]["gate_events"] = gate_events
        if eval_result["resolved"]:
            print("   🎉 Runtime verification passed!")
            return _done(final_status="resolved", iterations=current_iter, error_type="success")

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
            return _done(final_status="failed_max_retries", iterations=current_iter, error_type=eval_result["status"])

    return _done(final_status="failed_max_retries", iterations=max_iters, error_type="unknown")
