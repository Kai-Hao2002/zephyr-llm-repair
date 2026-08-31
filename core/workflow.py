# core/workflow.py
import os
import logging
from typing import Dict, Any

from langgraph.graph import StateGraph, END

# 引入自訂工具鏈與狀態
from core.state import ZephyrAgentState
from tools.patch_applier import PatchApplier
from tools.log_filter import LogFilter
from tools.qemu_oracle import QemuOracle

# 代理人節點的實作邏輯都搬到 agents/*.py 了 (跟 README 描述的架構對齊)，
# workflow.py 現在只負責把它們接成圖，以及 DevOps/建置這個非「代理人」
# 屬性的執行期節點。
# Agent node logic has moved to agents/*.py (matching the architecture
# README.md describes) — workflow.py now only wires them into the graph,
# plus the DevOps/build execution node, which isn't an "agent" persona.
from agents.analyzer import analyzer_node
from agents.knowledge_expert import knowledge_expert_node
from agents.patch_expert import patch_node
from agents.supervisor import route_after_devops

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


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
# 3. 定義邊界與建立狀態機
# ==========================================
def route_after_analyzer(state: ZephyrAgentState) -> str:
    return "goto_knowledge" if len(state.get("search_keywords", [])) > 0 else "goto_patch"

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