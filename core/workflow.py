# core/workflow.py
import os
import re
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

def build_devops_docker_cmd(workspace_path: str, board: str, target_app: str) -> str:
    """
    組出 DevOps Expert 用來建置/執行一個案例的 docker 指令。抽成獨立函式，
    讓 evaluate.py 準備好 workspace 之後，可以用完全相同的指令先做一次
    「重現初始失敗」的驗收檢查，而不是各自維護一份容易漂移的複製字串。

    掛載點必須是 /zephyrproject/zephyr，不能是 /workspace：見 devops_node
    內的說明——映像檔的 ZEPHYR_BASE 環境變數在建置當下就烤死指向
    /zephyrproject/zephyr，掛在別的路徑會讓 west build 實際上去用容器內建、
    從未被我們修改過的那份 Zephyr 原始碼。

    Builds the docker command the DevOps Expert uses to build/run a case.
    Factored out so evaluate.py can run the exact same command as a
    "reproduces the initial failure" sanity check right after preparing a
    workspace, instead of maintaining a second, driftable copy of this
    string.

    The mount point must be /zephyrproject/zephyr, not /workspace — see the
    comment inside devops_node: the image bakes ZEPHYR_BASE to point at
    /zephyrproject/zephyr at build time, so mounting anywhere else makes
    west build actually use the container's stock, never-modified Zephyr
    source.

    掛載必須可寫 (不能帶 :ro)：CMake 的 toolchain capability 檢查
    (extensions.cmake 的 zephyr_check_compiler_flag) 會把快取寫進
    <zephyr>/.cache/ToolchainCapabilityDatabase/ ——這個目錄在原始碼樹
    裡面，不是獨立的 build 目錄。掛成唯讀時，west build 在真正編譯到
    app 原始碼之前就會先在這一步失敗 (`file failed to open for writing`)，
    產生的失敗特徵跟 eof_no_boot 這個狀態桶完全一樣，會把「真正的注入
    bug 造成的建置失敗」跟「這個純粹的掛載寫入權限問題」混為一談 (實測
    2026-08-30：inject_c_hello_world_brace 的初始重現檢查一路回報
    eof_no_boot，表面上跟資料集記錄的預期值吻合，但拿掉 :ro 重測後才發現
    先前每一次其實都是卡在這裡，從未真正編譯到那個少一個大括號的
    main.c)。PatchApplier 本來就是在 docker 外面直接寫 host 檔案系統，
    :ro 從未真正提供任何隔離保護，只是讓 CMake 自己的快取寫入失敗；拿掉
    它才會跟 FaultInjector 驗證資料集時 (容器原生、可寫的檔案系統) 的
    建置行為一致。

    The mount must be writable (no :ro): CMake's toolchain-capability check
    (extensions.cmake's zephyr_check_compiler_flag) writes its cache into
    <zephyr>/.cache/ToolchainCapabilityDatabase/ — inside the source tree,
    not a separate build directory. Mounted read-only, west build fails at
    this step (`file failed to open for writing`) before ever reaching the
    app's own source — and that failure lands in the exact same
    "eof_no_boot" bucket as a genuine build-time bug, conflating "the
    injected bug actually broke the build" with "this mount is merely
    read-only" (confirmed 2026-08-30: inject_c_hello_world_brace's initial
    repro-check kept reporting eof_no_boot, superficially matching the
    dataset's recorded expected value, but re-testing without :ro revealed
    every prior run had actually been stuck here the whole time, never once
    reaching the real missing-brace bug in main.c). PatchApplier already
    writes directly to the host filesystem outside docker, so :ro was never
    providing real isolation — only breaking CMake's own cache writes;
    dropping it matches the writable, native container filesystem
    FaultInjector actually verified this dataset against.
    """
    return (
        f"docker run --rm -i -v {os.path.abspath(workspace_path)}:/zephyrproject/zephyr "
        f"-w /zephyrproject/zephyr/{target_app} zephyr-sandbox "
        f"bash -c 'west build -b {board} -d /tmp/build -p always -t run .'"
    )


_LOG_PATH_PREFIX = "/zephyrproject/zephyr/"
_LOG_EXT_PATH_RE = re.compile(re.escape(_LOG_PATH_PREFIX) + r"([\w\-./]+\.(?:c|h|conf|dts|dtsi|overlay))\b")
_LOG_KCONFIG_PATH_RE = re.compile(re.escape(_LOG_PATH_PREFIX) + r"([\w\-./]+/Kconfig(?:\.\w+)?)\b")

# 若比對出來的檔案總大小仍然偏大 (例如 target_app 本身就是個檔案很多的大型
# app)，設一個保守上限並截斷，寧可讓 Patch 用不完整的上下文重試，也不要
# 重演「整棵 zephyr 樹被塞進單一次呼叫、直接打穿供應商配額」的事故 (見
# _collect_relevant_context_paths 的說明)。
# If the matched files' total size is still large (e.g. target_app itself
# happens to be a big app with many files), cap it and truncate rather than
# risk repeating the "the whole zephyr tree in one call" quota-blowing
# incident (see _collect_relevant_context_paths's docstring).
_MAX_PATCH_CONTEXT_CHARS = 300_000


def _is_context_source_file(filename: str) -> bool:
    # 讀取 C 原始碼、設定檔，以及 Kconfig/DTS 檔案——後者原本被漏掉，導致
    # 這個 pipeline 結構性地看不到、修不了 kconfig/dts/compound 類別的
    # 資料集案例 (無論底層 LLM 能力如何)，見 part 27 稽核 finding 5。
    # Kconfig 檔案本身通常沒有副檔名 (檔名就是 "Kconfig" 或
    # "Kconfig.<driver>")，DTS 則用 .dts/.dtsi/.overlay。
    # Also read Kconfig/DTS files — previously missing, which made this
    # pipeline structurally blind to (and incapable of fixing)
    # kconfig/dts/compound-category dataset cases regardless of the
    # underlying LLM's ability; see part 27 audit finding 5. Kconfig files
    # themselves usually have no extension (named "Kconfig" or
    # "Kconfig.<driver>"); DTS uses .dts/.dtsi/.overlay.
    return (filename.endswith(".c") or filename.endswith(".conf") or filename == "CMakeLists.txt"
            or filename == "Kconfig" or filename.startswith("Kconfig.")
            or filename.endswith((".dts", ".dtsi", ".overlay")))


def _collect_relevant_context_paths(workspace_path: str, target_app: str, error_log: str) -> List[str]:
    """
    決定要餵給 Patch LLM 哪些檔案的內容——不能再像過去那樣 os.walk 整個
    workspace_path：workspace_path 現在是完整的 Zephyr checkout (見
    build_devops_docker_cmd 的說明)，對真實資料集案例來說是 3.7 萬個檔案、
    11MB+ 文字，單一次呼叫就會打穿 LLM 供應商的 token 配額 (2026-08-30 實測：
    inject_c_hello_world_brace 這種最簡單的案例都能把免費層 2,000,000
    token/分鐘的額度直接打爆)。

    改成只收兩種來源：
    1. current_error_log 裡明確以 /zephyrproject/zephyr/... 開頭提到的檔案
       路徑 (編譯期錯誤 — c_syntax/kconfig/dts 這幾類 — gcc/CMake 的診斷
       訊息一定會用容器內的絕對路徑指出出錯的檔案)。
    2. target_app 目錄本身 (含子目錄) 底下的原始碼/設定檔——通常是個位數
       到幾十個檔案，遠比整棵樹小得多。

    對 runtime_crash 這類日誌只有 "Segmentation fault" 之類崩潰特徵、完全
    沒有檔案路徑可循的案例，這兩種來源仍然定位不到真正該修的檔案 (通常在
    target_app 之外的 drivers/subsys 裡)——這需要真正的檢索 (Knowledge
    Expert 的圖譜、未來的 Hybrid RAG) 才能解決，不是這裡要處理的範圍。

    Decides which files' content to feed the Patch LLM — can no longer
    os.walk the entire workspace_path like before: workspace_path is now a
    full Zephyr checkout (see build_devops_docker_cmd's docstring), which
    for a real dataset case means ~38k files / 11MB+ of text, enough to
    blow through an LLM provider's token quota in a single call (confirmed
    2026-08-30: even the simplest case, inject_c_hello_world_brace, maxed
    out the free tier's 2,000,000 tokens/minute limit outright).

    Now sourced from just two places:
    1. File paths explicitly mentioned in current_error_log as
       /zephyrproject/zephyr/... (build-time errors — c_syntax/kconfig/dts
       — gcc/CMake diagnostics always cite the failing file by its absolute
       in-container path).
    2. target_app's own directory tree — typically single digits to a few
       dozen files, far smaller than the whole tree.

    For runtime_crash-category cases whose log is just a crash signature
    ("Segmentation fault") with no file path to go on, neither source can
    locate the actual file to fix (usually outside target_app, in
    drivers/subsys) — that needs real retrieval (Knowledge Expert's graph,
    eventually Hybrid RAG), out of scope here.
    """
    candidates = set()
    candidates.update(_LOG_EXT_PATH_RE.findall(error_log))
    candidates.update(_LOG_KCONFIG_PATH_RE.findall(error_log))

    target_app_dir = os.path.join(workspace_path, target_app)
    if os.path.isdir(target_app_dir):
        for root, dirs, files in os.walk(target_app_dir):
            for file in files:
                if _is_context_source_file(file):
                    candidates.add(os.path.relpath(os.path.join(root, file), workspace_path))

    # 只留下真的存在於這次 workspace 裡的路徑 (日誌裡提到的路徑可能因為
    # 之前的 patch 已經被改名/刪除)。
    # Only keep paths that genuinely exist in this workspace (a log-cited
    # path may have been renamed/deleted by an earlier patch attempt).
    return sorted(p for p in candidates if os.path.isfile(os.path.join(workspace_path, p)))


def patch_node(state: ZephyrAgentState) -> Dict[str, Any]:
    print("\n🛠️ [LLM Patch] 正在生成精確修補區塊...")

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0)

    # 動態讀取專案內的原始碼檔案，提供給 LLM 作為上下文——範圍限縮邏輯見
    # _collect_relevant_context_paths。
    workspace_path = state.get("workspace_path", "")
    target_app = state.get("target_app", ".")
    error_log = state.get("current_error_log", "")
    project_files_content = ""
    if os.path.exists(workspace_path):
        total_chars = 0
        for rel_path in _collect_relevant_context_paths(workspace_path, target_app, error_log):
            filepath = os.path.join(workspace_path, rel_path)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            if total_chars + len(content) > _MAX_PATCH_CONTEXT_CHARS:
                print(f"   ⚠️ 上下文已達 {_MAX_PATCH_CONTEXT_CHARS} 字元上限，略過 {rel_path} 及其後續檔案。")
                break
            project_files_content += f"\n--- {rel_path} ---\n{content}\n"
            total_chars += len(content)

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

    # route_after_devops 只靠 iterations >= max_iterations 決定要不要停
    # (goto "finish")，但先前失敗路徑的 return dict 從來沒有寫回
    # final_status——圖確實會停，但 state 永遠停在初始值 "in_progress"，
    # 呼叫端 (main.py/evaluate.py) 沒辦法分辨「真的重試到底仍失敗」跟
    # 「還在跑/某處提早中斷」。所有失敗路徑統一算這個值並寫回。
    # route_after_devops decides whether to stop (goto "finish") purely
    # from iterations >= max_iterations, but every failure-path return dict
    # never wrote final_status back — the graph does stop, but state stays
    # at its initial "in_progress" forever, leaving callers (main.py/
    # evaluate.py) unable to tell "genuinely exhausted retries and failed"
    # apart from "still running/aborted early somewhere". Computed once and
    # included on every failure path below.
    max_iterations = state.get("max_iterations", 5)
    failure_final_status = "failed_max_retries" if current_iter >= max_iterations else "in_progress"

    print(f"\n⚙️ [DevOps Expert] 啟動第 {current_iter} 次迭代的測試流程...")

    # 1. 應用修補程式
    applier = PatchApplier(workspace_path=workspace_path)
    patch_result = applier.apply_patches(patch_content)

    if not patch_result["success"]:
        print("   ❌ 修補應用失敗！格式錯誤或找不到匹配的原始碼。")
        return {
            "current_error_log": f"Patch Application Failed:\n{patch_result['error']}",
            "error_type": "patch_format_error",
            "iterations": current_iter,
            "final_status": failure_final_status
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

    # 見 build_devops_docker_cmd 的說明：掛載點必須是 /zephyrproject/zephyr。
    # See build_devops_docker_cmd's docstring: the mount point must be
    # /zephyrproject/zephyr.
    docker_cmd = build_devops_docker_cmd(workspace_path, board, target_app)

    print("   🔨 開始在隔離容器中編譯並執行 QEMU...")
    # timeout=15 對一次真正的 west build (可能要編譯 Zephyr kernel + app)
    # 來說遠遠不夠，幾乎每次都會提早 timeout，把任何案例都誤判為建置卡住
    # ——FaultInjector 驗證資料集時本來就是用 600s (tools/fault_injector.py)，
    # 這裡跟著對齊，而不是沿用 QemuOracle 建構子預設值 15。
    # timeout=15 is far too short for an actual west build (which may need
    # to compile the Zephyr kernel plus the app) — nearly every run would
    # time out early and get misclassified as stuck. FaultInjector already
    # uses 600s to verify this dataset (tools/fault_injector.py); align
    # with that instead of QemuOracle's constructor default of 15.
    oracle = QemuOracle(timeout=600)
    eval_result = oracle.evaluate(docker_cmd, required_pass_test=required_pass_test)

    if eval_result["status"] == "missing_required_test":
        print(f"   ⚠️ 套件回報成功，但目標測試 '{required_pass_test}' 沒有真的通過——patch 疑似投機取巧 (刪除/跳過該測試)，不算修復成功。")
        log_filter = LogFilter()
        return {
            "current_error_log": log_filter.compress_log(eval_result["log"])
                + f"\n\n[判定失敗：套件整體成功，但目標測試 '{required_pass_test}' 未見 PASS，patch 疑似繞過而非真正修復。]",
            "error_type": "missing_required_test",
            "iterations": current_iter,
            "final_status": failure_final_status
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
        "iterations": current_iter,
        "final_status": failure_final_status
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