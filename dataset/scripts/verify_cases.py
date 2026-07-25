# dataset/scripts/verify_cases.py
import os
import json
import logging
import subprocess
import threading
import time
import concurrent.futures

# 引入自訂工具
import sys
# 確保能讀取到專案根目錄的 tools 模組
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from tools.log_filter import LogFilter
from tools.qemu_oracle import QemuOracle
from tools.fault_injector import FaultInjector

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("CaseVerifier")

# 只有明確的「崩潰」或「建置失敗後直接結束」才算是成功重現的黃金案例。
# 'timeout' 與 'unknown' 通常代表 west update 抓取模組太慢或發生非預期狀況，
# 而不是真的重現了該 PR 修的 bug，把它們當成「已驗證」會讓資料集充滿雜訊日誌。
# Only an explicit crash or a build failure that ends the process counts as a
# reproduced golden case. 'timeout'/'unknown' usually mean west update was slow
# fetching modules or something unexpected happened — not a genuine repro —
# so accepting them would pollute the dataset with noisy, non-bug logs.
ACCEPTED_FAILURE_STATUSES = {"crash", "eof_no_boot"}

class ZephyrCaseVerifier:
    """
    自動化驗證探勘到的 Zephyr Bug 案例。
    將每個 broken_commit 放入 Docker 沙盒中編譯與執行，
    過濾出能夠在 QEMU 中穩定重現錯誤的黃金案例 (Golden Cases)。
    """
    def __init__(self, json_path: str, limit: int = None, offset: int = 0, max_workers: int = 3):
        self.json_path = os.path.abspath(json_path)
        self.output_path = os.path.join(os.path.dirname(self.json_path), "verified_zephyr_bugs.json")
        self.log_filter = LogFilter()
        # QemuOracle 沒有跨呼叫共用的可變狀態 (每次 evaluate() 都是全新的區域變數)，
        # 所以可以安全地在多執行緒間共用同一個實例。
        # QemuOracle has no cross-call mutable state (each evaluate() uses fresh
        # locals), so sharing one instance across threads is safe.
        # timeout 300s 對某些候選來說太短：光是 west update 抓取模組
        # 就實測要 ~2 分鐘，加上後續 west build 的 CMake 設定 + 編譯，
        # 總時間常常超過 300 秒，導致明明沒有真正的 bug 卻被誤判為 timeout
        # 而捨棄。拉高到 600 秒給足夠的緩衝。
        # 300s was too short for some candidates: west update alone measured
        # ~2 minutes just fetching modules, and the subsequent west build
        # (CMake configure + compile) often pushes the total past 300s,
        # causing candidates with no real bug to be wrongly discarded as
        # timeouts. Raised to 600s for headroom.
        self.oracle = QemuOracle(timeout=600)
        # 合成注入案例走 FaultInjector 的雙向驗證閘，而不是 git checkout 歷史 commit。
        # Synthetic injected cases go through FaultInjector's two-sided gate
        # instead of checking out a historical commit.
        self.injector = FaultInjector(timeout=600)
        self.limit = limit
        self.offset = offset
        self.max_workers = max_workers
        self._save_lock = threading.Lock()

        if not os.path.exists(self.json_path):
            raise FileNotFoundError(f"找不到資料集檔案: {self.json_path}")

    def verify_all_cases(self):
        with open(self.json_path, "r", encoding="utf-8") as f:
            cases = json.load(f)

        if self.offset:
            cases = cases[self.offset:]
        if self.limit:
            cases = cases[:self.limit]

        # 累加到既有的 verified_zephyr_bugs.json，而不是每次都整個覆寫掉，
        # 這樣分批跑 (--offset/--limit) 才不會把之前跑出來的結果洗掉。
        # Accumulate onto the existing verified_zephyr_bugs.json instead of
        # overwriting it each run, so batching with --offset/--limit doesn't
        # wipe out previously verified cases.
        verified_cases = []
        seen_ids = set()
        if os.path.exists(self.output_path):
            with open(self.output_path, "r", encoding="utf-8") as f:
                verified_cases = json.load(f)
            seen_ids = {c["id"] for c in verified_cases}

        logger.info(f"📂 載入 {len(cases)} 筆候選 Bug 案例 (已累積 {len(verified_cases)} 筆驗證通過)，準備以 {self.max_workers} 個並行 Docker 容器進行 QEMU 驗證...")

        pending_cases = [c for c in cases if c["id"] not in seen_ids]
        skipped = len(cases) - len(pending_cases)
        if skipped:
            logger.info(f"⏭️ 略過 {skipped} 筆已經驗證過的案例 (id 已存在於 verified_zephyr_bugs.json)。")

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_case = {executor.submit(self._verify_one_case, case): case for case in pending_cases}
            for future in concurrent.futures.as_completed(future_to_case):
                case = future_to_case[future]
                try:
                    verified_case = future.result()
                except Exception as e:
                    logger.error(f"❌ 案例 {case['id']} 驗證過程中發生例外: {e}")
                    continue

                if verified_case is not None:
                    with self._save_lock:
                        verified_cases.append(verified_case)
                        # 隨時存檔，避免中斷
                        self._save_verified_cases(verified_cases)

        logger.info("\n" + "="*50)
        logger.info(f"🎉 驗證完畢！共篩選出 {len(verified_cases)}/{len(cases)} 個高品質的 QEMU 可重現案例。")

    def _verify_one_case(self, case: dict):
        """
        驗證單一案例，供 ThreadPoolExecutor 並行呼叫。
        依 case 是否帶有 "injection" 欄位，分派給合成注入或真實挖礦兩條驗證路徑。
        Verifies a single case for parallel execution via ThreadPoolExecutor.
        Dispatches to the synthetic-injection or real-mined verification path
        depending on whether the case carries an "injection" field.
        """
        tag = f"[{case['id']}]"
        if "injection" in case:
            return self._verify_injected_case(case, tag)
        return self._verify_mined_case(case, tag)

    def _verify_injected_case(self, case: dict, tag: str):
        """
        驗證合成注入案例：透過 FaultInjector 的雙向驗證閘 (注入後必須重現
        預期類別的失敗、還原後必須建置並執行成功)。
        Verifies a synthetic fault-injection case via FaultInjector's
        two-sided gate (must reproduce the intended failure when mutated,
        must build and run successfully when reverted).
        """
        injection = case["injection"]
        target_app = case.get("target_app", "samples/hello_world")
        board = case.get("board", "native_sim")
        category = case.get("category", "other")

        logger.info(f"🧬 {tag} 開始驗證注入案例: {case['title']}")
        logger.info(f"   ↳ {tag} 目標檔案: {injection['target_file']} | Operator: {injection['operator']} | App: {target_app} | Board: {board}")

        gate_result = self.injector.inject_and_verify(
            case_id=case["id"],
            baseline_commit=case["broken_commit"],
            target_file=injection["target_file"],
            operator=injection["operator"],
            category=category,
            target_app=target_app,
            board=board,
        )

        if not gate_result["accepted"]:
            logger.warning(f"   ⚠️ {tag} 注入驗證未通過: {gate_result['reason']}")
            return None

        logger.info(f"   ✅ {tag} 注入後成功重現預期失敗、還原後成功建置！這是一個完美的合成評估案例。")

        mutated_result = gate_result["mutated_result"]
        compressed_log = self.log_filter.compress_log(mutated_result["log"])
        case["initial_error_log"] = compressed_log
        case["error_type"] = mutated_result["status"]
        return case

    def _verify_mined_case(self, case: dict, tag: str):
        """
        驗證真實挖礦案例：checkout broken_commit 後直接建置，
        期望重現明確的崩潰或建置失敗。
        Verifies a real mined case: checks out broken_commit and builds it
        directly, expecting an explicit crash or build failure.
        """
        logger.info(f"🧪 {tag} 開始驗證: {case['title']} | Commit: {case['broken_commit'][:10]}")

        # 測試目標應用程式 (預設使用 hello_world，您也可以根據 Bug 模組動態調整)
        target_app = case.get("target_app", "samples/hello_world")
        board = case.get("board", "qemu_x86")
        logger.info(f"   ↳ {tag} 目標 App: {target_app} | 開發板: {board}")

        result = self._run_sandbox_test(case['id'], case['broken_commit'], target_app, board)

        # 我們期望的結果是明確的「崩潰」或「建置失敗」(因為這是 broken_commit)。
        # 'success' 代表 hello_world 沒觸發到這個 bug；'timeout'/'unknown' 通常只是
        # west update 抓取模組太慢或環境問題，兩者都不是真正重現，必須捨棄。
        if result["status"] not in ACCEPTED_FAILURE_STATUSES:
            if result["status"] == "success":
                logger.warning(f"   ⚠️ {tag} 居然編譯且執行成功！這代表它是隱性 Bug 或依賴特定硬體，捨棄此案例。")
            elif result["status"] == "unsupported_board":
                logger.warning(f"   ⚠️ {tag} 目標板子 '{board}' 不支援模擬 (QEMU/native)，無法驗證執行期行為，捨棄此案例。")
            elif result["status"] == "docker_infra_error":
                logger.warning(f"   ⚠️ {tag} Docker daemon 本身斷線/崩潰，與目標 commit 無關，捨棄此案例 (建議稍後重跑)。")
            else:
                logger.warning(f"   ⚠️ {tag} 狀態為 '{result['status']}' (非明確崩潰/建置失敗特徵，可能只是逾時或環境問題)，捨棄以避免雜訊污染資料集。")
            return None

        logger.info(f"   ✅ {tag} 成功捕捉到錯誤特徵 (狀態: {result['status']})！這是一個完美的評估案例。")

        # 壓縮並儲存初始錯誤日誌，這將是 LLM 的起點
        compressed_log = self.log_filter.compress_log(result["log"])
        case["initial_error_log"] = compressed_log
        case["error_type"] = result["status"]
        return case

    def _run_sandbox_test(self, case_id: str, commit_sha: str, target_app: str, board: str) -> dict:
        """
        修改 Docker 指令以接收動態的 target_app 與 board。
        限制每個容器的 CPU/記憶體用量，避免並行執行時互相搶佔資源導致 OOM。
        Limits each container's CPU/memory so concurrent runs don't starve each other.

        每個容器給一個獨一無二的名稱 (帶上時間戳記，避免同一案例重跑時撞名)，
        讓 QemuOracle 在 timeout/例外時能明確 `docker kill` 掉它，不會變成
        殭屍容器持續佔用資源、拖慢後續所有驗證。
        Each container gets a unique name (timestamped, so re-running the same
        case doesn't collide) so QemuOracle can explicitly `docker kill` it on
        timeout/exception, instead of leaking a zombie container that eats
        resources and stalls every later verification.
        """
        container_name = f"verify_{case_id}_{int(time.time() * 1000)}"
        docker_cmd = (
            f"docker run --rm -i --name {container_name} --cpus=2 --memory=2400m zephyr-sandbox bash -c '"
            f"cd /zephyrproject/zephyr && "
            f"git fetch origin {commit_sha} && "
            f"git checkout {commit_sha} && "
            f"west update --narrow && "
            f"cd /zephyrproject/zephyr/{target_app} && "
            f"west build -b {board} -p always -t run"
            f"'"
        )

        # 呼叫我們先前寫好的 Test Oracle 來監控輸出
        return self.oracle.evaluate(docker_cmd, container_name=container_name)

    def _save_verified_cases(self, cases: list):
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(cases, f, indent=4, ensure_ascii=False)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verify mined Zephyr bug candidates against the zephyr-sandbox Docker image.")
    parser.add_argument("--input", default="zephyr_bugs.json", help="Candidate JSON filename under dataset/cases/ (default: zephyr_bugs.json)")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N candidates (for running the pool in batches)")
    parser.add_argument("--limit", type=int, default=None, help="Only verify the first N candidates after --offset (useful for timing pilots)")
    parser.add_argument("--workers", type=int, default=3, help="Number of concurrent Docker containers to run")
    args = parser.parse_args()

    json_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cases", args.input))
    verifier = ZephyrCaseVerifier(json_path, limit=args.limit, offset=args.offset, max_workers=args.workers)
    verifier.verify_all_cases()