# core/llm_usage.py
"""
從 LangChain 的 AIMessage 讀出這次 LLM 呼叫的 token 用量，供 RQ4 的 Token
Efficiency 指標使用。獨立成一個小模組，因為 Proposed pipeline
(agents/analyzer.py、agents/patch_expert.py、agents/supervisor.py) 跟
B1/B2/B3 baseline (agents/baselines.py) 的每一次 LLM 呼叫都要用同一套抽取
邏輯，不要各自重寫一份容易漂移的版本。

Reads a single LLM call's token usage off a LangChain AIMessage, for RQ4's
Token Efficiency metric. Kept as its own small module because every LLM
call across both the Proposed pipeline (agents/analyzer.py,
agents/patch_expert.py, agents/supervisor.py) and the B1/B2/B3 baselines
(agents/baselines.py) needs the same extraction logic, not a separately
reimplemented copy at each call site.
"""
from typing import Any, Dict, List


def extract_usage(ai_message: Any, node: str, model: str) -> Dict[str, Any]:
    """
    ai_message.usage_metadata 是 LangChain 的標準欄位 (dict，含
    input_tokens/output_tokens/total_tokens)；某些情況下可能不存在或是
    None (例如呼叫失敗、或供應商沒有回傳用量資訊)，這時退回全 0，而不是
    讓呼叫端因為這個非必要的統計欄位而出錯——token 用量統計失準好過整個
    節點掛掉。

    ai_message.usage_metadata is LangChain's standard field (a dict with
    input_tokens/output_tokens/total_tokens); it may be missing or None in
    some cases (a failed call, or a provider that doesn't report usage) —
    falls back to all-zero rather than letting the caller fail over this
    non-essential stat: an inaccurate token count beats crashing the node.
    """
    usage = getattr(ai_message, "usage_metadata", None) or {}
    return {
        "node": node,
        "model": model,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }


def append_usage(pending_token_usage: List[Dict[str, Any]], entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """回傳一份新的 list (舊的 + 這次呼叫)，不就地修改傳入的 list——LangGraph
    節點的回傳值應該是新的狀態片段，不該有 side effect 改到呼叫端還握著的
    物件。"""
    return pending_token_usage + [entry]
