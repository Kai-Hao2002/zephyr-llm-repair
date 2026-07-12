# core/workflow.py
import os
import logging
from typing import Dict, Any, List
from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, END
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI 

# 引入自訂工具鏈與狀態
from core.state import ZephyrAgentState
from tools.patch_applier import PatchApplier
from tools.log_filter import LogFilter
from tools.qemu_oracle import QemuOracle

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 1. 定義 Analyzer 強型別輸出
# ==========================================
class AnalyzerOutput(BaseModel):
    """分析建置錯誤並決定後續動作"""
    reasoning: str = Field(description="簡短解釋你認為錯誤的原因，以及為何需要或不需要檢索圖譜。")
    search_keywords: List[str] = Field(
        description="如果要檢索 Kconfig 符號或 DTS 節點，列出精確的關鍵字 (例如 ['I2C', 'bme280'])。如果只是一般 C 語法錯誤，請回傳空列表 []。"
    )
    error_category: str = Field(description="將錯誤分類為: 'kconfig', 'dts', 'c_syntax', 'cmake', 'other'")

# ==========================================
# 2. 定義代理人節點
# ==========================================

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

def knowledge_node(state: ZephyrAgentState) -> Dict[str, Any]:
    keywords = state.get("search_keywords", [])
    print(f"\n📚 [Knowledge Expert] 針對關鍵字 {keywords} 檢索圖譜...")
    # 這裡未來會接上您的 Graph RAG，目前先模擬
    mock_context = f"找到 {keywords} 的拓樸關係：缺少相應的 include 或設定檔。"
    return {"retrieved_context": mock_context}

def patch_node(state: ZephyrAgentState) -> Dict[str, Any]:
    print("\n🛠️ [LLM Patch] 正在生成精確修補區塊...")
    
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0)

    # 動態讀取專案內的原始碼檔案，提供給 LLM 作為上下文
    workspace_path = state.get("workspace_path", "")
    project_files_content = ""
    if os.path.exists(workspace_path):
        for root, dirs, files in os.walk(workspace_path):
            for file in files:
                # 僅讀取 C 原始碼與設定檔
                if file.endswith(".c") or file.endswith(".conf") or file.endswith("CMakeLists.txt"):
                    filepath = os.path.join(root, file)
                    rel_path = os.path.relpath(filepath, workspace_path)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            project_files_content += f"\n--- {rel_path} ---\n{f.read()}\n"
                    except Exception:
                        pass

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一位頂尖的嵌入式軟體工程師，專精於修復 Zephyr RTOS 的程式碼。
請根據錯誤日誌與提供的專案原始碼，輸出修復程式碼。

【嚴格格式要求】
你必須使用以下的 SEARCH/REPLACE 區塊格式來修改檔案。
絕對不要在區塊外加上 ``` 程式碼區塊符號。
SEARCH 區塊內的程式碼必須與原始檔案「一模一樣」（包含縮排）。

<檔案的相對路徑>
<<<<<<<< SEARCH
<要被替換的原始程式碼>
========
<修復後的新程式碼>
>>>>>>>> REPLACE"""),
        ("human", """[目前專案原始碼與設定檔內容]
{project_files}

[檢索到的知識圖譜上下文]
{context}

[目前的錯誤日誌]
{error_log}

請開始生成修補區塊：""")
    ])

    chain = prompt | llm
    response = chain.invoke({
        "project_files": project_files_content,
        "context": state.get("retrieved_context", "無"),
        "error_log": state.get("current_error_log", "")
    })
    
    patch_text = response.content
    print("   ↳ 成功生成修補區塊！")
    return {
        "patch_content": patch_text,
        "messages": [f"Patch Expert 已生成修補方案。"]
    }

def devops_node(state: ZephyrAgentState) -> Dict[str, Any]:
    current_iter = state.get("iterations", 0) + 1
    workspace_path = state.get("workspace_path")
    patch_content = state.get("patch_content", "")
    
    print(f"\n⚙️ [DevOps Expert] 啟動第 {current_iter} 次迭代的測試流程...")
    
    # 1. 應用修補程式
    applier = PatchApplier(workspace_path=workspace_path)
    patch_result = applier.apply_patches(patch_content)
    
    if not patch_result["success"]:
        print("   ❌ 修補應用失敗！格式錯誤或找不到匹配的原始碼。")
        return {
            "current_error_log": f"Patch Application Failed:\n{patch_result['error']}",
            "error_type": "patch_format_error",
            "iterations": current_iter
        }
        
    print(f"   ✅ 修補成功應用至: {patch_result.get('applied_files', [])}")

    # 2. 建置與執行期驗證
    docker_cmd = (
        f"docker run --rm -i -v {os.path.abspath(workspace_path)}:/workspace:ro "
        "-w /workspace zephyr-sandbox "
        "bash -c 'west build -b qemu_x86 -d /tmp/build -p always -t run .'"
    )
    
    print("   🔨 開始在隔離容器中編譯並執行 QEMU...")
    oracle = QemuOracle(timeout=15)
    eval_result = oracle.evaluate(docker_cmd)
    
    if eval_result["status"] == "success":
        print("   🎉 執行期驗證通過！")
        return {
            "current_error_log": "Zephyr OS successfully booted.",
            "error_type": "success",
            "iterations": current_iter,
            "final_status": "resolved"
        }
        
    print(f"   💥 測試失敗 (狀態: {eval_result['status']})，正在過濾日誌...")
    log_filter = LogFilter()
    return {
        "current_error_log": log_filter.compress_log(eval_result["log"]),
        "error_type": eval_result["status"],
        "iterations": current_iter
    }

# ==========================================
# 3. 定義邊界與建立狀態機 (與原本相同)
# ==========================================
def route_after_analyzer(state: ZephyrAgentState) -> str:
    return "goto_knowledge" if len(state.get("search_keywords", [])) > 0 else "goto_patch"

def route_after_devops(state: ZephyrAgentState) -> str:
    if state.get("error_type") == "success" or state.get("iterations", 0) >= state.get("max_iterations", 5):
        return "finish"
    return "retry"

def build_zephyr_graph() -> StateGraph:
    workflow = StateGraph(ZephyrAgentState)
    workflow.add_node("Analyzer", analyzer_node)
    workflow.add_node("Knowledge", knowledge_node)
    workflow.add_node("Patch", patch_node)
    workflow.add_node("DevOps", devops_node)

    workflow.set_entry_point("Analyzer")
    workflow.add_conditional_edges("Analyzer", route_after_analyzer, {"goto_knowledge": "Knowledge", "goto_patch": "Patch"})
    workflow.add_edge("Knowledge", "Patch")
    workflow.add_edge("Patch", "DevOps")
    workflow.add_conditional_edges("DevOps", route_after_devops, {"finish": END, "retry": "Analyzer"})
    
    return workflow.compile()