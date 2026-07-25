# tools/qemu_oracle.py
import pexpect
import logging
import re
import subprocess
from typing import Dict, Any, Optional

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

        # 某些真實硬體板子不支援 QEMU/native 模擬，west 會印出這句話然後直接
        # 結束，跟目標 commit 是否有 bug 完全無關。若不特別排除，這種情況會
        # 落入 EOF 分支被誤判為「重現成功」。
        # Some real hardware boards don't support QEMU/native emulation — west
        # just prints this and exits, regardless of whether the target commit
        # has a bug. Without excluding it explicitly, this falls into the EOF
        # branch and gets misclassified as a successful repro.
        self.unsupported_patterns = [
            r"Emulation/Simulation not supported with this board",
        ]

        # Docker daemon 本身斷線/崩潰時，docker CLI 會印出這類訊息，容器連線
        # 被意外切斷，pexpect 會收到 EOF，很容易被誤判為「建置失敗、正常結束」。
        # 這是基礎設施本身出問題，跟目標 commit 有沒有 bug 完全無關，必須排除。
        # 這個檢查不受 in_build_phase 限制，因為 daemon 隨時可能斷線。
        # docker CLI prints messages like these when the Docker daemon itself
        # disconnects/crashes, cutting the container connection and giving
        # pexpect an EOF that's easily misread as "build failed, exited
        # normally". This is an infrastructure failure unrelated to whether
        # the target commit has a bug, and must be excluded. Not gated behind
        # in_build_phase since the daemon can drop the connection at any time.
        self.docker_infra_error_patterns = [
            r"error waiting for container",
            r"Cannot connect to the Docker daemon",
            r"Error response from daemon",
            r"context deadline exceeded",
        ]
        self.docker_infra_error_regex = [re.compile(p) for p in self.docker_infra_error_patterns]

        # west/git 在抓取模組時 (git fetch/checkout/west update) 印出的歷史 commit
        # 訊息，內容完全不受我們控制，字面上可能剛好包含 "Bus Fault"、"Hello World"
        # 等字樣 (例如某個歷史 commit 的 subject 是 "Fix Bus Fault during raw TX")。
        # 在真正進入 west build 之前比對 crash/success 特徵，會被這種雜訊誤觸發。
        # 所以只在看到這個 west build 啟動的標記行之後，才開始比對特徵。
        # west/git print historical commit messages while fetching modules
        # (git fetch/checkout/west update), content we don't control that can
        # literally contain "Bus Fault", "Hello World", etc. (e.g. a historical
        # commit subject like "Fix Bus Fault during raw TX"). Matching crash/
        # success patterns before west build actually starts gets false-triggered
        # by this noise. So only start matching after seeing this build-start marker.
        self.build_start_marker = re.compile(r"west build: generating a build system")

        # 將上述字串編譯為正規表示式以加速匹配
        # Compile patterns to regex for faster matching
        self.success_regex = [re.compile(p) for p in self.success_patterns]
        self.crash_regex = [re.compile(p) for p in self.crash_patterns]
        self.unsupported_regex = [re.compile(p) for p in self.unsupported_patterns]

    def evaluate(self, command: str, container_name: Optional[str] = None) -> Dict[str, Any]:
        """
        執行指令並監聽 QEMU 輸出。

        :param container_name: 若指令有帶 `--name <container_name>` 啟動 Docker 容器，
            傳入這個名稱可以確保 timeout/例外發生時真的把容器殺掉，而不是只依賴
            `child.close(force=True)` 終止本地 pexpect 子進程 (docker run 這個 CLI
            進程收到 SIGKILL 時不一定來得及通知 daemon 停掉容器，導致殭屍容器持續
            佔用 CPU/網路，拖垮後續所有驗證直到全部逾時)。
        Executes the command and listens to the QEMU output.

        :param container_name: If the command starts a Docker container with
            `--name <container_name>`, pass it here to guarantee the container
            is actually killed on timeout/exception — not just relying on
            `child.close(force=True)` to terminate the local pexpect child
            (the `docker run` CLI process may not have time to tell the daemon
            to stop the container before it's SIGKILLed, leaking a zombie
            container that keeps eating CPU/network and stalls every
            subsequent verification until they all time out).
        """
        self.logger.info("啟動 Test Oracle 並監控 QEMU 輸出... (Starting Test Oracle to monitor QEMU...)")
        
        # 由於我們需要監測 Docker 的輸出，使用 pexpect.spawn
        # Using pexpect.spawn to monitor Docker output interactively
        # encoding='utf-8' 確保我們處理的是字串而不是 Bytes
        try:
            child = pexpect.spawn(command, encoding='utf-8', timeout=self.timeout)
            
            captured_log = ""
            result = {"status": "unknown", "log": "", "error_signature": None}
            in_build_phase = False

            # 持續讀取每一行 (Iterate line by line)
            while True:
                try:
                    # 每次讀取一行 (\r\n 處理跨平台換行)
                    child.expect(r'\r?\n')
                    line = child.before.strip()
                    if line:
                        captured_log += line + "\n"
                        # print(f"[QEMU] {line}") # 測試時可以解開註解以觀察即時輸出

                        # -1. 檢查 Docker daemon 本身是否斷線/崩潰 (不受 build phase 限制)
                        for pattern in self.docker_infra_error_regex:
                            if pattern.search(line):
                                self.logger.error(f"偵測到 Docker 基礎設施錯誤，與目標 commit 無關 (Docker infrastructure error detected, unrelated to the target commit): {pattern.pattern}")
                                result["status"] = "docker_infra_error"
                                result["error_signature"] = pattern.pattern
                                break

                        if result["status"] == "docker_infra_error":
                            break

                        if not in_build_phase:
                            if self.build_start_marker.search(line):
                                in_build_phase = True
                            else:
                                # 還在 git fetch/west update 階段，跳過特徵比對，
                                # 避免被歷史 commit 訊息誤觸發。
                                # Still in the git fetch/west update phase — skip
                                # pattern matching to avoid false triggers from
                                # historical commit messages.
                                continue

                        # 0. 檢查該板子是否根本不支援模擬 (Check for an unsupported board)
                        for pattern in self.unsupported_regex:
                            if pattern.search(line):
                                self.logger.warning(f"此開發板不支援模擬，與目標 commit 無關 (Board doesn't support emulation, unrelated to the target commit): {pattern.pattern}")
                                result["status"] = "unsupported_board"
                                result["error_signature"] = pattern.pattern
                                break

                        if result["status"] == "unsupported_board":
                            break

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

            # 保險機制：直接對 Docker daemon 下達 kill，不管本地子進程有沒有
            # 成功終止容器都確保清乾淨 (--rm 容器被 kill 後會自動移除)。
            # Safety net: kill the container directly via the Docker daemon
            # regardless of whether the local child process managed to stop it
            # (a `--rm` container is auto-removed once killed).
            if container_name:
                try:
                    subprocess.run(
                        ["docker", "kill", container_name],
                        capture_output=True, timeout=10
                    )
                except Exception:
                    pass

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