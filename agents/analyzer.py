# agents/analyzer.py
"""
Analyzer Expert：解讀 DevOps 傳回的精簡錯誤日誌，決定是否需要檢索圖譜、以及
用哪些關鍵字檢索。

Analyzer Expert: interprets the compressed error log DevOps hands back,
decides whether graph retrieval is needed, and with which keywords.
"""
from typing import Dict, Any, List

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from core.state import ZephyrAgentState
from core.llm_usage import extract_usage, append_usage
from core.llm_provider import get_chat_model, get_model_name
from core.llm_retry import call_with_retry


class AnalyzerOutput(BaseModel):
    """Analyze a build error and decide the next action."""
    reasoning: str = Field(description="Briefly explain what you think caused the error, and why graph retrieval is or is not needed.")
    search_keywords: List[str] = Field(
        description="If Kconfig symbols or DTS nodes should be retrieved, list the exact keywords (e.g. ['I2C', 'bme280']). For a plain C syntax error, return an empty list []."
    )
    error_category: str = Field(description="Classify the error as one of: 'kconfig', 'dts', 'c_syntax', 'cmake', 'other'")


def analyzer_node(state: ZephyrAgentState) -> Dict[str, Any]:
    print(f"\n🧠 [LLM Analyzer] Analyzing the log of iteration {state['iterations']}...")

    # role="fast"：便宜快速的模型，適合分類任務 (見 core/llm_provider.py)。
    # timeout=120：實測 2026-09-01，Gemini API 呼叫偶爾會完全沒有回應，
    # 沒設 timeout 的話呼叫端會無限期卡住 (曾讓一次 B3 baseline pilot 卡了
    # 1 小時 44 分鐘才被手動砍掉)。120 秒對這裡的分類任務綽綽有餘。
    # role="fast": a cheap, fast model suited to classification (see
    # core/llm_provider.py). timeout=120: confirmed 2026-09-01 that Gemini
    # API calls can hang with no response at all; without a timeout the
    # caller blocks indefinitely (once left a B3 baseline pilot hung for
    # 1h44m before being killed by hand). 120s is generous for this
    # classification-sized task.
    llm = get_chat_model(role="fast", temperature=0.1, timeout=120)
    # include_raw=True：預設的 with_structured_output 只回傳解析好的
    # pydantic 物件，拿不到底層 AIMessage 的 usage_metadata (RQ4 的 Token
    # Efficiency 需要這個)。改成回傳 {"raw","parsed","parsing_error"}。
    # include_raw=True: the default with_structured_output only returns the
    # parsed pydantic object, losing access to the underlying AIMessage's
    # usage_metadata (needed for RQ4's Token Efficiency). Switches the
    # return shape to {"raw", "parsed", "parsing_error"}.
    structured_llm = llm.with_structured_output(AnalyzerOutput, include_raw=True)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a senior Zephyr RTOS debugging expert.
Your task is to analyze a compressed build or runtime error log.

Decision rules:
1. If the error involves hardware, peripherals, or undefined macros (such as DT_NODELABEL), graph retrieval is needed. Extract the exact component names as search_keywords.
2. If the error is an "undeclared" or "undefined reference" symbol/function, do not assume it is a simple typo. It usually means the symbol was supposed to be enabled/declared conditionally by some Kconfig option or Devicetree setting, but that condition was broken (for example a CONFIG_ symbol was turned off, or a DTS node was removed), so the code that depends on it was excluded from compilation. This case also needs graph retrieval, but for search_keywords **do not give only the full literal name of the undeclared symbol**: it is usually a test-specific function name (for example `test_fcb_crc_disabled`) that almost never appears in Kconfig/DTS files, so searching for it would find "files that mention this function name" (usually the test file that calls it) instead of the configuration files that should actually be inspected. Additionally infer the "subsystem/module name" it belongs to and include it in search_keywords: it can usually be inferred from the file paths mentioned in the error message (for example `fcb` in `.../subsys/fs/fcb/src/xxx.c`), or by extracting the core word root from the symbol name itself (for example the core of `test_fcb_crc_disabled` is `fcb`). Kconfig symbols are usually named after a subsystem/feature (for example `config FCB`), not after an individual function or test name.
3. Only if the error is a plain C syntax error (such as a missing semicolon or bracket, or an obvious misspelling) is graph retrieval unnecessary; in that case search_keywords must be the empty list []."""),
        ("human", "Here is the current error log of my project:\n{error_log}")
    ])

    chain = prompt | structured_llm
    raw_output = call_with_retry(lambda: chain.invoke({"error_log": state.get("current_error_log", "")}), what="analyzer")
    result: AnalyzerOutput = raw_output["parsed"]
    usage_entry = extract_usage(raw_output["raw"], node="analyzer", model=get_model_name("fast"))

    print(f"   ↳ Reasoning: {result.reasoning}")
    print(f"   ↳ Extracted keywords: {result.search_keywords}")

    return {
        "search_keywords": result.search_keywords,
        "analyzer_diagnosis": {
            "iteration": state.get("iterations", 0) + 1,
            "reasoning": result.reasoning,
            "error_category": result.error_category,
            "search_keywords": result.search_keywords,
        },
        "messages": [f"Analyzer diagnosis ({result.error_category}): {result.reasoning}"],
        "pending_token_usage": append_usage(state.get("pending_token_usage", []), usage_entry),
    }
