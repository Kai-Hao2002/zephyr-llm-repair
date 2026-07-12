# env_manager/west_executer.py
import docker
import os
import logging
from typing import Dict, Any

class WestExecutor:
    """
    負責在隔離的 Docker 容器中執行 Zephyr (west) 指令。
    Responsible for executing Zephyr (west) commands in an isolated Docker container.
    """
    def __init__(self, target_project_path: str, image_name: str = "zephyr-sandbox"):
        """
        初始化執行器
        :param target_project_path: 宿主機上的 Zephyr 專案路徑 (例如 ./dataset/cases/bug_001)
        :param image_name: Docker 映像檔名稱
        """
        self.client = docker.from_env()
        self.image_name = image_name
        # 取得絕對路徑，以便 Docker volume 掛載
        self.target_project_path = os.path.abspath(target_project_path)
        
        if not os.path.exists(self.target_project_path):
            raise FileNotFoundError(f"找不到目標專案路徑 (Project path not found): {self.target_project_path}")
            
        logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
        self.logger = logging.getLogger(__name__)

    def build_project(self, board: str = "qemu_x86") -> Dict[str, Any]:
        """
        執行 west build。
        為了保證決定性 (Deterministic)，我們將 build 目錄指定到容器內的 /tmp/build。
        
        Executes west build. 
        For determinism, the build directory is forced to /tmp/build inside the container.
        """
        self.logger.info(f"開始在 {board} 上建置專案... (Starting build on {board}...)")
        
        # 組裝指令：在 /workspace (掛載的唯讀原始碼) 下執行編譯，但產出放到 /tmp/build
        # 使用 -p always 強制每次都執行 pristine build (徹底清理)
        command = f"west build -b {board} -d /tmp/build -p always ."

        return self._run_in_container(command)

    def _run_in_container(self, command: str) -> Dict[str, Any]:
        """
        啟動一個用完即棄 (Disposable) 的容器來執行指令。
        Starts a disposable container to execute the command.
        """
        try:
            # 啟動容器 (等同於 docker run --rm -v ...)
            container = self.client.containers.run(
                image=self.image_name,
                command=["/bin/bash", "-c", command],
                # 將宿主機的程式碼以唯讀 (ro) 模式掛載，徹底防止污染
                # Mount source code as read-only (ro) to prevent any host contamination
                volumes={
                    self.target_project_path: {'bind': '/workspace', 'mode': 'ro'}
                },
                working_dir="/workspace",
                detach=False,   # 阻塞直到執行完畢 (Block until finished)
                remove=True,    # 執行完自動刪除容器 (Auto-remove after run)
                stdout=True,
                stderr=True
            )
            
            # 若無例外拋出，代表退出碼 (exit code) 為 0，建置成功
            return {
                "success": True,
                "output": container.decode('utf-8'),
                "error": ""
            }
            
        except docker.errors.ContainerError as e:
            # 建置失敗 (Exit code != 0)
            self.logger.error("指令執行失敗 (Command execution failed).")
            # docker-py 的 ContainerError 只有 stderr 屬性 (包含合併的輸出)
            error_output = e.stderr.decode('utf-8') if hasattr(e, 'stderr') and e.stderr else str(e)
            return {
                "success": False,
                "output": error_output,
                "error": ""
            }
        except Exception as e:
            # 其他 Docker 相關錯誤
            self.logger.critical(f"Docker 環境錯誤 (Docker environment error): {str(e)}")
            return {
                "success": False,
                "output": "",
                "error": str(e)
            }

# ==========================================
# 測試區塊 (Testing Block)
# ==========================================
if __name__ == "__main__":
    # 建立一個測試用的 Dummy 專案資料夾 (假設您有一個 zephyr 應用程式在這裡)
    # 這裡請替換為您本地真實的 Zephyr hello_world 範例路徑
    test_project_path = "../dataset/cases/hello_world" 
    
    if os.path.exists(test_project_path):
        executor = WestExecutor(target_project_path=test_project_path)
        
        print("=== 執行純淨建置測試 (Testing Pristine Build) ===")
        result = executor.build_project(board="qemu_cortex_m3")
        
        if result["success"]:
            print("✅ 建置成功! (Build Successful!)")
            # 通常輸出太長，我們只印出最後 500 個字元
            print(result["output"][-500:]) 
        else:
            print("❌ 建置失敗! (Build Failed!)")
            print("=== 錯誤日誌 (Error Log) ===")
            print(result["output"])
            print(result["error"])
    else:
        print(f"請先建立一個測試用的 Zephyr 專案路徑於 {test_project_path}")