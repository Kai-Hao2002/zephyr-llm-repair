# dataset/scripts/verify_cases.py
import os
import json
import logging
import subprocess

# 引入自訂工具
import sys
# 確保能讀取到專案根目錄的 tools 模組
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from tools.log_filter import LogFilter
from tools.qemu_oracle import QemuOracle

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("CaseVerifier")

class ZephyrCaseVerifier:
    """
    自動化驗證探勘到的 Zephyr Bug 案例。
    將每個 broken_commit 放入 Docker 沙盒中編譯與執行，
    過濾出能夠在 QEMU 中穩定重現錯誤的黃金案例 (Golden Cases)。
    """
    def __init__(self, json_path: str):
        self.json_path = os.path.abspath(json_path)
        self.output_path = os.path.join(os.path.dirname(self.json_path), "verified_zephyr_bugs.json")
        self.log_filter = LogFilter()
        self.oracle = QemuOracle(timeout=300)
        
        if not os.path.exists(self.json_path):
            raise FileNotFoundError(f"找不到資料集檔案: {self.json_path}")

    def verify_all_cases(self):
        with open(self.json_path, "r", encoding="utf-8") as f:
            cases = json.load(f)
            
        logger.info(f"📂 載入 {len(cases)} 筆候選 Bug 案例，準備進行 QEMU 驗證...")
        verified_cases = []

        for case in cases:
            logger.info(f"\n" + "="*50)
            logger.info(f"🧪 正在驗證案例: {case['id']} ({case['title']})")
            logger.info(f"   ↳ 目標 Commit: {case['broken_commit']}")
            
            # 測試目標應用程式 (預設使用 hello_world，您也可以根據 Bug 模組動態調整)
            # 在真實論文實驗中，這裡通常會指定該 Bug 負責的 tests/ 子目錄
            target_app = case.get("target_app", "samples/hello_world")
            board = case.get("board", "qemu_x86")
            
            logger.info(f"   ↳ 目標 App: {target_app} | 開發板: {board}")
            
            # 2. 將 board 參數也傳遞進去
            result = self._run_sandbox_test(case['broken_commit'], target_app, board)

            # 我們期望的結果是「失敗」(因為這是 broken_commit)
            # 如果它莫名其妙成功了，代表這個 bug 需要特定硬體，或者 hello_world 沒觸發到它，我們必須捨棄。
            if result["status"] == "success":
                logger.warning(f"   ⚠️ 案例 {case['id']} 居然編譯且執行成功！這代表它是隱性 Bug 或依賴特定硬體，捨棄此案例。")
                continue
                
            logger.info(f"   ✅ 成功捕捉到錯誤特徵 (狀態: {result['status']})！這是一個完美的評估案例。")
            
            # 壓縮並儲存初始錯誤日誌，這將是 LLM 的起點
            compressed_log = self.log_filter.compress_log(result["log"])
            case["initial_error_log"] = compressed_log
            case["error_type"] = result["status"]
            verified_cases.append(case)
            
            # 隨時存檔，避免中斷
            self._save_verified_cases(verified_cases)

        logger.info("\n" + "="*50)
        logger.info(f"🎉 驗證完畢！共篩選出 {len(verified_cases)}/{len(cases)} 個高品質的 QEMU 可重現案例。")

    def _run_sandbox_test(self, commit_sha: str, target_app: str, board: str) -> dict:
        """
        修改 Docker 指令以接收動態的 target_app 與 board
        """
        docker_cmd = (
            f"docker run --rm -i zephyr-sandbox bash -c '"
            f"cd /zephyrproject/zephyr && "
            f"git fetch origin {commit_sha} && "
            f"git checkout {commit_sha} && "
            f"west update --narrow && "
            f"cd /zephyrproject/zephyr/{target_app} && "
            f"west build -b {board} -p always -t run"
            f"'"
        )
        
        # 呼叫我們先前寫好的 Test Oracle 來監控輸出
        return self.oracle.evaluate(docker_cmd)

    def _save_verified_cases(self, cases: list):
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(cases, f, indent=4, ensure_ascii=False)

if __name__ == "__main__":
    json_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cases", "zephyr_bugs.json"))
    verifier = ZephyrCaseVerifier(json_path)
    verifier.verify_all_cases()