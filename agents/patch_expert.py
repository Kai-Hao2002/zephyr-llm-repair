# agents/patch_expert.py
"""
Patch Expert：根據錯誤日誌、圖譜檢索上下文，以及範圍限縮過的專案原始碼，
生成嚴格的 <<<<SEARCH/>>>>REPLACE 修補區塊。

Patch Expert: generates strict <<<<SEARCH/>>>>REPLACE patch blocks from the
error log, retrieved graph context, and a scoped slice of the project's
source.
"""
import os
import re
from typing import Dict, Any, List

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from core.state import ZephyrAgentState

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
    決定要餵給 Patch LLM 哪些檔案的內容——不能 os.walk 整個 workspace_path：
    workspace_path 是完整的 Zephyr checkout，對真實資料集案例來說是 3.7 萬
    個檔案、11MB+ 文字，單一次呼叫就會打穿 LLM 供應商的 token 配額 (2026-08-30
    實測：inject_c_hello_world_brace 這種最簡單的案例都能把免費層 2,000,000
    token/分鐘的額度直接打爆)。

    改成只收兩種來源：
    1. current_error_log 裡明確以 /zephyrproject/zephyr/... 開頭提到的檔案
       路徑 (編譯期錯誤 — c_syntax/kconfig/dts 這幾類 — gcc/CMake 的診斷
       訊息一定會用容器內的絕對路徑指出出錯的檔案)。
    2. target_app 目錄本身 (含子目錄) 底下的原始碼/設定檔——通常是個位數
       到幾十個檔案，遠比整棵樹小得多。

    已知的定位缺口 (2026-08-30/31 兩次真實 pilot 都踩到，Phase 3 Hybrid RAG
    才該解決，不是這裡要處理的範圍)：
    - runtime_crash 這類日誌只有 "Segmentation fault" 之類崩潰特徵、完全
      沒有檔案路徑可循的案例 (`inject_runtime_fcb_nullcheck`)。
    - 就算日誌裡有路徑，也可能只是 Zephyr 產生出來的通用巨集標頭檔
      (`include/zephyr/devicetree.h` 之類)，不是真正該改的 Kconfig/DTS
      原始檔 (`inject_compound_adc_emul_kconfig_dts`)。
    這兩種情況，這兩種來源都定位不到真正該修的檔案 (通常在 target_app 之外
    的 drivers/subsys/boards 裡)。

    Decides which files' content to feed the Patch LLM — can't os.walk the
    entire workspace_path: workspace_path is a full Zephyr checkout, which
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

    Known localization gap (hit by two real pilots on 2026-08-30/31; this
    is Phase 3's (Hybrid RAG) problem, out of scope here):
    - runtime_crash logs that are just a crash signature ("Segmentation
      fault") with no file path at all (`inject_runtime_fcb_nullcheck`).
    - Logs that do cite a path, but it's one of Zephyr's generated,
      generic DT-macro headers (`include/zephyr/devicetree.h`, etc.), not
      the actual Kconfig/DTS source responsible
      (`inject_compound_adc_emul_kconfig_dts`).
    In both cases neither source can locate the real file to fix (usually
    outside target_app, in drivers/subsys/boards).
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

    # 跨迭代記憶——見 agents/supervisor.py 的 record_attempt_outcome 與
    # core/state.py 的 attempt_history 說明：沒有這段之前，Patch 完全不
    # 知道前幾次已經試過什麼、為什麼沒用，實測觀察到 LLM 會在同一批看得到
    # 的檔案間反覆打轉。列進 prompt 讓它至少知道「這幾個方向已經證明
    # 無效」。
    # Cross-iteration memory — see agents/supervisor.py's
    # record_attempt_outcome and core/state.py's attempt_history for the
    # rationale: without this, Patch had zero memory of what earlier
    # iterations tried or why they failed, and was observed empirically to
    # circle between the same visible files. Listed in the prompt so it at
    # least knows which directions are already shown not to work.
    attempt_history = state.get("attempt_history", [])
    attempt_history_text = "\n".join(attempt_history) if attempt_history else "（這是第一次嘗試，尚無歷史紀錄）"

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
        ("human", """[之前已經嘗試過的修補與結果]
{attempt_history}
（判讀原則：若某筆紀錄是「套用失敗」，代表 SEARCH 區塊的文字沒有跟檔案內容逐字元比對成功——原始碼完全沒有被改動，請對照下方最新的原始碼內容重新逐字元、含縮排確認，修改方向本身不一定有錯，不需要因此更換方向；若某筆紀錄是「套用成功但建置/執行仍失敗」，代表這個修改方向已經證明無效，請換一個不同的方式或檔案。）

[目前專案原始碼與設定檔內容]
{project_files}

[檢索到的知識圖譜上下文]
{context}

[目前的錯誤日誌]
{error_log}

請開始生成修補區塊：""")
    ])

    chain = prompt | llm
    response = chain.invoke({
        "attempt_history": attempt_history_text,
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
