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
from langchain_google_genai import ChatGoogleGenerativeAI

from core.state import ZephyrAgentState


class AnalyzerOutput(BaseModel):
    """分析建置錯誤並決定後續動作"""
    reasoning: str = Field(description="簡短解釋你認為錯誤的原因，以及為何需要或不需要檢索圖譜。")
    search_keywords: List[str] = Field(
        description="如果要檢索 Kconfig 符號或 DTS 節點，列出精確的關鍵字 (例如 ['I2C', 'bme280'])。如果只是一般 C 語法錯誤，請回傳空列表 []。"
    )
    error_category: str = Field(description="將錯誤分類為: 'kconfig', 'dts', 'c_syntax', 'cmake', 'other'")


def analyzer_node(state: ZephyrAgentState) -> Dict[str, Any]:
    print(f"\n🧠 [LLM Analyzer] 正在分析第 {state['iterations']} 次迭代的日誌...")

    # 改用 Gemini 1.5 Flash (快速且便宜，適合分類任務)
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.1)
    structured_llm = llm.with_structured_output(AnalyzerOutput)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一位資深的 Zephyr RTOS 除錯專家。
你的任務是分析經過壓縮的編譯或執行期錯誤日誌。

決策規則：
1. 若錯誤涉及硬體、周邊、未定義的巨集 (如 DT_NODELABEL)，代表需要檢索圖譜。請提取精確元件名稱作為 search_keywords。
2. 若錯誤是單純的 C 語言語法錯誤 (如漏掉分號、括號)，不需要檢索圖譜，search_keywords 必須回傳空列表 []。"""),
        ("human", "這是我專案目前的錯誤日誌：\n{error_log}")
    ])

    chain = prompt | structured_llm
    result: AnalyzerOutput = chain.invoke({"error_log": state.get("current_error_log", "")})

    print(f"   ↳ 推論: {result.reasoning}")
    print(f"   ↳ 提取關鍵字: {result.search_keywords}")

    return {
        "search_keywords": result.search_keywords,
        "messages": [f"Analyzer 診斷 ({result.error_category}): {result.reasoning}"]
    }
