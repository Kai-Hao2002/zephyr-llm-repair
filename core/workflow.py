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

from agents.knowledge_expert import knowledge_expert_node

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

def patch_node(state: ZephyrAgentState) -> Dict[str, Any]:
    print("\n🛠️ [LLM Patch] 正在生成精確修補區塊...")
    
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0)

    # 動態讀取專案內的原始碼檔案，提供給 LLM 作為上下文
    workspace_path = state.get("workspace_path", "")
    project_files_content = ""
    if os.path.exists(workspace_path):
        for root, dirs, files in os.walk(workspace_path):
            for file in files:
                # 讀取 C 原始碼、設定檔，以及 Kconfig/DTS 檔案——後者原本被
                # 漏掉，導致這個 pipeline 結構性地看不到、修不了
                # kconfig/dts/compound 類別的資料集案例 (無論底層 LLM 能力
                # 如何)，見 part 27 稽核 finding 5。Kconfig 檔案本身通常沒有
                # 副檔名 (檔名就是 "Kconfig" 或 "Kconfig.<driver>")，DTS 則用
                # .dts/.dtsi/.overlay。
                # Also read Kconfig/DTS files — previously missing, which
                # made this pipeline structurally blind to (and incapable of
                # fixing) kconfig/dts/compound-category dataset cases
                # regardless of the underlying LLM's ability; see part 27
                # audit finding 5. Kconfig files themselves usually have no
                # extension (named "Kconfig" or "Kconfig.<driver>"); DTS uses
                # .dts/.dtsi/.overlay.
                if (file.endswith(".c") or file.endswith(".conf") or file.endswith("CMakeLists.txt")
                        or file == "Kconfig" or file.startswith("Kconfig.")
                        or file.endswith((".dts", ".dtsi", ".overlay"))):
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
    # board/target_app 之前寫死成 qemu_x86 + workspace 根目錄，只能對付
    # main.py 的單一 demo 場景；現在改成從 state 讀取，才能正確對應資料集
    # 裡任何一筆案例真正的目標板子與 app 子目錄 (例如
    # "tests/subsys/input/longpress" + "native_sim")。target_app 用來組
    # 出容器內要 cd 進去的工作目錄，跟 FaultInjector 建立注入案例時
    # "cd 進 target_app 再 west build ." 的方式一致。
    # board/target_app used to be hardcoded to qemu_x86 + the workspace
    # root, which only worked for main.py's single demo scenario; now read
    # from state so this correctly targets whatever board/app subdirectory
    # a given dataset case actually needs (e.g.
    # "tests/subsys/input/longpress" + "native_sim"). target_app is used
    # to build the in-container working directory, matching how
    # FaultInjector builds injected cases ("cd into target_app, then
    # west build .").
    board = state.get("board", "qemu_x86")
    target_app = state.get("target_app", ".")
    required_pass_test = state.get("required_pass_test")

    docker_cmd = (
        f"docker run --rm -i -v {os.path.abspath(workspace_path)}:/workspace:ro "
        f"-w /workspace/{target_app} zephyr-sandbox "
        f"bash -c 'west build -b {board} -d /tmp/build -p always -t run .'"
    )

    print("   🔨 開始在隔離容器中編譯並執行 QEMU...")
    oracle = QemuOracle(timeout=15)
    eval_result = oracle.evaluate(docker_cmd, required_pass_test=required_pass_test)

    if eval_result["status"] == "missing_required_test":
        print(f"   ⚠️ 套件回報成功，但目標測試 '{required_pass_test}' 沒有真的通過——patch 疑似投機取巧 (刪除/跳過該測試)，不算修復成功。")
        log_filter = LogFilter()
        return {
            "current_error_log": log_filter.compress_log(eval_result["log"])
                + f"\n\n[判定失敗：套件整體成功，但目標測試 '{required_pass_test}' 未見 PASS，patch 疑似繞過而非真正修復。]",
            "error_type": "missing_required_test",
            "iterations": current_iter
        }

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
    workflow.add_node("Knowledge", knowledge_expert_node)
    workflow.add_node("Patch", patch_node)
    workflow.add_node("DevOps", devops_node)

    workflow.set_entry_point("Analyzer")
    workflow.add_conditional_edges("Analyzer", route_after_analyzer, {"goto_knowledge": "Knowledge", "goto_patch": "Patch"})
    workflow.add_edge("Knowledge", "Patch")
    workflow.add_edge("Patch", "DevOps")
    workflow.add_conditional_edges("DevOps", route_after_devops, {"finish": END, "retry": "Analyzer"})
    
    return workflow.compile()