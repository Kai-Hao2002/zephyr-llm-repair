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


class AnalyzerOutput(BaseModel):
    """分析建置錯誤並決定後續動作"""
    reasoning: str = Field(description="簡短解釋你認為錯誤的原因，以及為何需要或不需要檢索圖譜。")
    search_keywords: List[str] = Field(
        description="如果要檢索 Kconfig 符號或 DTS 節點，列出精確的關鍵字 (例如 ['I2C', 'bme280'])。如果只是一般 C 語法錯誤，請回傳空列表 []。"
    )
    error_category: str = Field(description="將錯誤分類為: 'kconfig', 'dts', 'c_syntax', 'cmake', 'other'")


def analyzer_node(state: ZephyrAgentState) -> Dict[str, Any]:
    print(f"\n🧠 [LLM Analyzer] 正在分析第 {state['iterations']} 次迭代的日誌...")

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
        ("system", """你是一位資深的 Zephyr RTOS 除錯專家。
你的任務是分析經過壓縮的編譯或執行期錯誤日誌。

決策規則：
1. 若錯誤涉及硬體、周邊、未定義的巨集 (如 DT_NODELABEL)，代表需要檢索圖譜。請提取精確元件名稱作為 search_keywords。
2. 若錯誤是「未宣告 (undeclared)」或「未定義參照 (undefined reference)」的符號/函式，先不要預設是單純打錯字——這通常代表該符號原本應該由某個 Kconfig 選項或 Devicetree 設定條件式地啟用/宣告，但條件被破壞了 (例如某個 CONFIG_ 符號被關掉、或 DTS 節點被移除)，導致依賴它的程式碼被排除在編譯之外。這種情況代表需要檢索圖譜，但 search_keywords **不要只給這個未宣告符號的完整字面名稱**——它通常是測試專屬的函式名 (例如 `test_fcb_crc_disabled`)，Kconfig/DTS 設定檔裡幾乎不會出現這個完整字串，只給它會讓檢索找到「提到這個函式名的檔案」(通常是呼叫它的測試檔案本身)，而不是真正該查的設定檔。請額外推斷它所屬的「子系統/模組名稱」一併列入 search_keywords——通常可以從錯誤訊息裡提到的檔案路徑推斷 (例如 `.../subsys/fs/fcb/src/xxx.c` 裡的 `fcb`)，或從符號名稱本身拆解出核心字根 (例如 `test_fcb_crc_disabled` 的核心是 `fcb`)。Kconfig 符號通常是用子系統/功能名稱命名 (例如 `config FCB`)，不是用個別函式或測試名稱命名。
3. 若錯誤是單純的 C 語言語法錯誤 (如漏掉分號、括號、明顯的拼字錯誤)，才不需要檢索圖譜，search_keywords 必須回傳空列表 []。"""),
        ("human", "這是我專案目前的錯誤日誌：\n{error_log}")
    ])

    chain = prompt | structured_llm
    raw_output = chain.invoke({"error_log": state.get("current_error_log", "")})
    result: AnalyzerOutput = raw_output["parsed"]
    usage_entry = extract_usage(raw_output["raw"], node="analyzer", model=get_model_name("fast"))

    print(f"   ↳ 推論: {result.reasoning}")
    print(f"   ↳ 提取關鍵字: {result.search_keywords}")

    return {
        "search_keywords": result.search_keywords,
        "messages": [f"Analyzer 診斷 ({result.error_category}): {result.reasoning}"],
        "pending_token_usage": append_usage(state.get("pending_token_usage", []), usage_entry),
    }
