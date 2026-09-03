# core/llm_provider.py
"""
RQ4 (跨模型比較) 需要同一套 pipeline 換不同 LLM 供應商跑——這支模組把「用
哪個供應商/模型」這個決定，從散落在 agents/*.py 各處寫死的
`ChatGoogleGenerativeAI(model="gemini-2.5-...")` 集中成一個工廠函式。

「供應商」是整次 evaluate.py 執行期間的單一設定 (跟 board/target_app 這種
逐案例才會變的東西不同)，所以刻意不透過 LangGraph 的 state 傳遞，而是像
agents/patch_expert.py 的 MAX_PATCH_CONTEXT_CHARS 一樣是模組層級的設定——
evaluate.py 在整個行程一開始呼叫一次 set_provider()，之後 agents/*.py 每次
呼叫 get_chat_model() 都讀到同一個值，不用把 provider 字串一路往下傳過
Analyzer/Knowledge/Patch/Supervisor 每一層函式簽名。

RTOS repair role 分兩種 (跟 requirements.txt 已經裝的三個供應商包各自的
「便宜/快速」vs「強力/複雜任務」模型對應)：
- "fast": 分類/壓縮這種輕量任務 (Analyzer 的錯誤分類、Supervisor 的
  Context Compression)。
- "pro": 修補生成這種需要看大量上下文、產生結構化 diff 的任務 (Patch
  Expert、B1/B2/B3 baseline 的修補生成)。

Embedding 模型 (graph_rag/hybrid_retriever.py 的 Hybrid RAG 語意層) 刻意
不透過這裡切換：Anthropic 沒有自己的 embedding API，跨供應商比較時語意
檢索層維持用 Gemini 的 embedding 模型，只有生成式 LLM 呼叫 (分類/修補/
壓縮) 隨 provider 切換——這是 RQ4 "model-specific tool-use behaviors" 真正
要測的東西，不是檢索層本身。

RQ4 (cross-model comparison) needs the same pipeline runnable against
different LLM providers — this module centralizes the "which
provider/model" decision, previously hardcoded as
`ChatGoogleGenerativeAI(model="gemini-2.5-...")` scattered across
agents/*.py, into one factory function.

"Provider" is a single setting for the whole evaluate.py run (unlike
board/target_app, which vary per case), so it's deliberately not threaded
through LangGraph state — instead it's a module-level setting, the same
pattern as agents/patch_expert.py's MAX_PATCH_CONTEXT_CHARS: evaluate.py
calls set_provider() once at process start, and every agents/*.py call to
get_chat_model() afterward reads that same value, without threading a
provider string down through every Analyzer/Knowledge/Patch/Supervisor
function signature.

Two roles cover the RTOS repair pipeline's LLM calls (mapped to each
installed provider's own cheap/fast vs. strong/complex-task model tiers):
- "fast": lightweight classification/compression (Analyzer's error
  categorization, Supervisor's Context Compression).
- "pro": patch generation — large context, structured diff output (Patch
  Expert, and the B1/B2/B3 baselines' patch generation).

The embedding model (graph_rag/hybrid_retriever.py's Hybrid RAG semantic
layer) is deliberately NOT switched here: Anthropic has no embeddings API
of its own, so the semantic retrieval layer stays on Gemini's embedding
model across providers — only the generative LLM calls (classification/
patching/compression) switch with the provider. That's what RQ4's
"model-specific tool-use behaviors" is actually measuring, not the
retrieval layer itself.
"""
import os
from typing import Any

_DEFAULT_PROVIDER = "gemini"

# 每個供應商在 "fast"/"pro" 這兩個角色各自對應的實際模型名稱。Gemini 沿用
# 既有程式碼原本就在用的兩個模型；Anthropic/OpenAI 選跟角色定位相符的
# 對應模型 (便宜快速 vs. 主力生成)。
# The concrete model name each provider maps to for the "fast"/"pro" roles.
# Gemini reuses the two models the existing code already used; Anthropic/
# OpenAI pick the model matching each role's intent (cheap & fast vs. the
# main generation workhorse).
_MODEL_BY_PROVIDER_AND_ROLE = {
    "gemini": {"fast": "gemini-2.5-flash", "pro": "gemini-2.5-pro"},
    "anthropic": {"fast": "claude-haiku-4-5-20251001", "pro": "claude-sonnet-5"},
    "openai": {"fast": "gpt-5-mini", "pro": "gpt-5"},
}

_provider = os.environ.get("ZEPHYR_LLM_PROVIDER", _DEFAULT_PROVIDER)


def set_provider(provider: str) -> None:
    """整次 evaluate.py 執行期間呼叫一次 (main() 裡，剖析完 --model-provider
    之後)，之後所有 get_chat_model() 呼叫都讀到這個值。"""
    if provider not in _MODEL_BY_PROVIDER_AND_ROLE:
        raise ValueError(f"unknown provider '{provider}', expected one of {sorted(_MODEL_BY_PROVIDER_AND_ROLE)}")
    global _provider
    _provider = provider


def get_provider() -> str:
    return _provider


def get_model_name(role: str) -> str:
    """呼叫端 (agents/*.py) 記 token 用量時要標記用的實際模型名稱字串——
    不能寫死 "gemini-2.5-pro" 之類的固定字串，跨供應商比較時會失真。"""
    if role not in ("fast", "pro"):
        raise ValueError(f"unknown role '{role}', expected 'fast' or 'pro'")
    return _MODEL_BY_PROVIDER_AND_ROLE[_provider][role]


def get_chat_model(role: str, temperature: float = 0, timeout: int = 120) -> Any:
    """
    role 必須是 "fast" 或 "pro"。回傳的物件都是 LangChain 的
    BaseChatModel，介面 (invoke/with_structured_output(include_raw=True)/
    回傳值的 usage_metadata) 在三個供應商之間一致，呼叫端 (agents/*.py) 不
    需要因為換供應商而改寫呼叫方式。

    role must be "fast" or "pro". All returned objects are LangChain
    BaseChatModel instances — the interface (invoke/
    with_structured_output(include_raw=True)/the response's
    usage_metadata) is consistent across all three providers, so callers
    (agents/*.py) never need to change how they call this just because the
    provider changed.
    """
    if role not in ("fast", "pro"):
        raise ValueError(f"unknown role '{role}', expected 'fast' or 'pro'")
    model = _MODEL_BY_PROVIDER_AND_ROLE[_provider][role]

    if _provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, temperature=temperature, timeout=timeout)
    if _provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=temperature, timeout=timeout)
    if _provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=temperature, timeout=timeout)
    raise ValueError(f"unknown provider '{_provider}'")  # pragma: no cover - set_provider already validates
