# tools/qemu_oracle.py
import pexpect
import logging
import re
from typing import Dict, Any

class QemuOracle:
    """
    監聽 QEMU 執行期輸出，判斷系統是否成功啟動或發生崩潰。
    Monitors QEMU runtime output to determine if the system booted successfully or crashed.
    """
    def __init__(self, timeout: int = 15):
        """
        :param timeout: 啟動 QEMU 後等待輸出的最大秒數 (Maximum seconds to wait for output)
        """
        self.timeout = timeout
        logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
        self.logger = logging.getLogger(__name__)

        # 定義預期的成功字串 (Zephyr 啟動特徵)
        # Expected success signatures (Zephyr boot signatures)
        self.success_patterns = [
            r"\*\*\* Booting Zephyr OS",
            r"Booting Zephyr OS",
            r"Hello World" # 針對 Hello World 範例的額外檢查
        ]

        # 定義 Zephyr 常見的 Fatal Error 特徵
        # Common Zephyr Fatal Error signatures
        self.crash_patterns = [
            r"Kernel Panic",
            r"Fatal fault",
            r"ASSERTION FAIL",
            r"Usage Fault",
            r"Bus Fault",
            r"CPU Page Fault"
        ]

        # 將上述字串編譯為正規表示式以加速匹配
        # Compile patterns to regex for faster matching
        self.success_regex = [re.compile(p) for p in self.success_patterns]
        self.crash_regex = [re.compile(p) for p in self.crash_patterns]

    def evaluate(self, command: str) -> Dict[str, Any]:
        """
        執行指令並監聽 QEMU 輸出。
        Executes the command and listens to the QEMU output.
        """
        self.logger.info("啟動 Test Oracle 並監控 QEMU 輸出... (Starting Test Oracle to monitor QEMU...)")
        
        # 由於我們需要監測 Docker 的輸出，使用 pexpect.spawn
        # Using pexpect.spawn to monitor Docker output interactively
        # encoding='utf-8' 確保我們處理的是字串而不是 Bytes
        try:
            child = pexpect.spawn(command, encoding='utf-8', timeout=self.timeout)
            
            captured_log = ""
            result = {"status": "unknown", "log": "", "error_signature": None}

            # 持續讀取每一行 (Iterate line by line)
            while True:
                try:
                    # 每次讀取一行 (\r\n 處理跨平台換行)
                    child.expect(r'\r?\n')
                    line = child.before.strip()
                    if line:
                        captured_log += line + "\n"
                        # print(f"[QEMU] {line}") # 測試時可以解開註解以觀察即時輸出
                        
                        # 1. 檢查是否發生崩潰 (Check for crash)
                        for pattern in self.crash_regex:
                            if pattern.search(line):
                                self.logger.error(f"偵測到執行期崩潰 (Runtime crash detected): {pattern.pattern}")
                                result["status"] = "crash"
                                result["error_signature"] = pattern.pattern
                                break
                        
                        # 如果已經崩潰，跳出迴圈
                        if result["status"] == "crash":
                            break

                        # 2. 檢查是否成功啟動 (Check for success)
                        for pattern in self.success_regex:
                            if pattern.search(line):
                                self.logger.info("偵測到成功啟動特徵！ (Successful boot signature detected!)")
                                result["status"] = "success"
                                break
                                
                        if result["status"] == "success":
                            break

                except pexpect.TIMEOUT:
                    # 如果 QEMU 卡住且超過指定時間沒有新輸出
                    self.logger.warning(f"QEMU 執行超時 ({self.timeout}s). (QEMU execution timed out.)")
                    result["status"] = "timeout"
                    break
                
                except pexpect.EOF:
                    # 程式自然結束 (例如建置失敗根本沒啟動 QEMU)
                    # Process exited normally (e.g., build failed, QEMU never started)
                    self.logger.info("進程已結束 (Process exited).")
                    result["status"] = "eof_no_boot"
                    break

        finally:
            # 確保強制關閉子進程，避免殭屍容器殘留
            # Ensure the child process is forcefully closed to prevent zombie containers
            if child and child.isalive():
                child.close(force=True)
            
            result["log"] = captured_log
            return result

# ==========================================
# 測試區塊 (Testing Block)
# ==========================================
if __name__ == "__main__":
    oracle = QemuOracle(timeout=10)
    
    # 這裡我們模擬上一階段的 west_executor 產生的 docker 啟動指令
    # 注意：我們加入了 -t run 來要求 west 建置完畢後直接啟動 QEMU
    # Note: We added `-t run` to instruct west to run QEMU immediately after building.
    test_project_path = "/絕對路徑/到您的/hello_world" # <--- 請修改為您的絕對路徑 (Must be absolute path)
    
    # -i 允許互動模式，這對 pexpect 抓取 stdout 很重要
    docker_cmd = (
        f"docker run --rm -i -v {test_project_path}:/workspace:ro "
        "-w /workspace zephyr-sandbox "
        "bash -c 'west build -b qemu_x86 -d /tmp/build -p always -t run .'"
    )
    
    print("=== 執行 QEMU 閉環驗證測試 (Testing QEMU Closed-Loop Oracle) ===")
    eval_result = oracle.evaluate(docker_cmd)
    
    print("\n--- 驗證結果 (Evaluation Result) ---")
    print(f"狀態 (Status): {eval_result['status']}")
    if eval_result['error_signature']:
        print(f"捕捉到的錯誤特徵 (Error Signature): {eval_result['error_signature']}")
    print("部分日誌 (Partial Log):")
    # 只印出最後 300 個字元避免洗頻
    print(eval_result['log'][-300:])