# main.py
import os
import logging
import time
from dotenv import load_dotenv

# 引入自訂模組 (Import custom modules)
from env_manager.west_executor import WestExecutor
from tools.log_filter import LogFilter
from core.state import create_initial_state
from core.workflow import build_zephyr_graph

# 載入環境變數 (Load environment variables)
load_dotenv()

# 設定日誌格式 (Configure logging)
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("Main")

def setup_broken_zephyr_project(workspace_path: str):
    """
    自動生成一個帶有語法錯誤的 Zephyr 專案供測試使用。
    Automatically generates a broken Zephyr project for testing.
    """
    logger.info(f"📁 準備測試專案於: {workspace_path}")
    os.makedirs(os.path.join(workspace_path, "src"), exist_ok=True)

    # 1. CMakeLists.txt (標準 Zephyr 配置)
    with open(os.path.join(workspace_path, "CMakeLists.txt"), "w") as f:
        f.write("cmake_minimum_required(VERSION 3.20.0)\n")
        f.write("find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n")
        f.write("project(hello_world)\n")
        f.write("target_sources(app PRIVATE src/main.c)\n")

    # 2. prj.conf (空配置即可)
    with open(os.path.join(workspace_path, "prj.conf"), "w") as f:
        f.write("# Empty config\n")

    # 3. 故意寫錯的 src/main.c (少了一個分號)
    # Intentionally broken src/main.c (missing a semicolon)
    broken_c_code = """#include <zephyr/kernel.h>

int main(void) {
    printk("Hello World! Auto-debugging is awesome!\\n")
    return 0;
}
"""
    with open(os.path.join(workspace_path, "src", "main.c"), "w") as f:
        f.write(broken_c_code)

def run_phase_zero_build(workspace_path: str) -> str:
    """
    執行首次建置，故意讓它失敗以取得初始錯誤日誌。
    Executes the initial build, intentionally failing it to get the raw error log.
    """
    logger.info("\n🚧 [Phase 0] 執行首次建置以獲取錯誤特徵...")
    executor = WestExecutor(target_project_path=workspace_path)
    result = executor.build_project(board="qemu_x86")
    
    log_filter = LogFilter()
    compressed_log = log_filter.compress_log(result["output"] + "\n" + result["error"])
    
    logger.info("   ↳ 首次建置失敗 (符合預期)！擷取到的錯誤日誌:")
    print("-" * 40)
    print(compressed_log)
    print("-" * 40)
    
    return compressed_log

def main():
    print("="*60)
    print("🚀 Zephyr Multi-Agent Auto-Repair System (E2E Test)")
    print("="*60)

    # 1. 設定測試環境 (Setup Test Environment)
    test_workspace = os.path.abspath("./test_e2e_workspace")
    setup_broken_zephyr_project(test_workspace)

    # 2. 觸發初始錯誤 (Trigger Initial Error)
    initial_log = run_phase_zero_build(test_workspace)

    if "Fallback" in initial_log and not "error" in initial_log.lower():
        logger.error("❌ 無法捕捉到預期的編譯錯誤，請檢查 Docker 環境。")
        return

    # 3. 初始化 LangGraph 狀態 (Initialize LangGraph State)
    logger.info("\n🧠 [System] 初始化全域狀態與 LangGraph...")
    # 這個 demo 場景是把整個 hello_world app 直接放在 workspace 根目錄，
    # board/target_app 明確帶入舊有的預設值，行為與修改前完全相同——只是
    # 現在這兩個值是外部傳入而不是深藏在 devops_node 裡。
    # This demo scenario puts the whole hello_world app right at the
    # workspace root; board/target_app are passed explicitly with the old
    # defaults so behavior is unchanged from before — the difference is
    # these values are now supplied from the caller instead of buried
    # inside devops_node.
    state = create_initial_state(
        workspace_path=test_workspace,
        initial_log=initial_log,
        max_iters=3,
        board="qemu_x86",
        target_app="."
    )

    graph = build_zephyr_graph()

    # 4. 啟動除錯迴圈 (Start Debugging Loop)
    logger.info("🔄 [System] 開始執行多代理人閉環除錯...\n")
    start_time = time.time()
    
    # 使用 stream 逐節點印出進度
    for step_event in graph.stream(state):
        for node_name, updated_state in step_event.items():
            print(f"\n✅ 節點 [{node_name}] 執行完畢.")
            
            # 檢查是否已達到結束狀態
            if updated_state.get("final_status") == "resolved":
                print("\n" + "🌟"*20)
                print("🏆 任務成功！Zephyr 專案已修復並通過 QEMU 測試！")
                print("🌟"*20)
                break
            elif updated_state.get("final_status") == "failed_max_retries":
                print("\n💀 達到最大重試次數，任務失敗。")
                break

    end_time = time.time()
    print(f"\n⏱️ 總耗時: {end_time - start_time:.2f} 秒")

if __name__ == "__main__":
    main()