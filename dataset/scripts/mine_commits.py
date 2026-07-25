# dataset/scripts/mine_commits.py
import os
import re
import requests
import json
import logging
import time
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("GitHubMiner")

# 執行期崩潰關鍵字 (用於從 PR 標題/內文判斷是否為 runtime crash 案例)
# Runtime-crash keywords used to classify PRs from their title/body
CRASH_KEYWORDS = [
    "panic", "crash", "fault", "assert", "hang", "oops",
    "deadlock", "overflow", "corrupt", "use-after-free", "null pointer",
]

# 用於猜測開發板的架構關鍵字 -> board 對照表
# Architecture keyword -> board mapping used to guess a QEMU target
ARCH_BOARD_MAP = [
    (re.compile(r"arch/xtensa|/xtensa/|xtensa"), "qemu_xtensa"),
    (re.compile(r"arch/riscv|/riscv/"), "qemu_riscv32"),
    (re.compile(r"arch/arm64|/arm64/"), "qemu_cortex_a53"),
    (re.compile(r"arch/arm|/arm/"), "qemu_cortex_m3"),
    (re.compile(r"arch/x86|/x86/"), "qemu_x86"),
]

# 板級目錄修改 (boards/<vendor>/<board_name>/...) 應該直接用該板子本身當作
# --board 參數，而不是套用架構關鍵字對照表——否則像 native_sim 這種通用板子
# 根本不會載入該廠商板子的 DTS，導致誤判「建置成功」。
# A board-directory edit (boards/<vendor>/<board_name>/...) should use that
# board itself as --board, not the arch keyword map — a generic board like
# native_sim never loads the vendor board's DTS, causing false "build succeeded".
BOARD_PATH_RE = re.compile(r"boards/[^/]+/([^/]+)/")

# 預先註冊的合成錯誤注入目錄 (在任何修補實驗開始前就固定下來，避免事後
# 針對系統調整)。每一筆指定：分類、目標檔案 (相對於 Zephyr repo 根目錄)、
# mutation operator (定義在 tools/mutate_inject.py)，以及用來觸發/驗證這個
# mutation 的 target_app 與 board。target_app/board 特意選成「一定會載入/
# 編譯到目標檔案」的組合 (例如板子自己的 DTS 就用該板子本身建置)，避免真實
# 挖礦時遇到的 target_app/board 猜錯問題。
#
# 這份清單只是起點，不保證每一筆都能通過 verify_cases.py 的雙向驗證閘——
# 通不過的會被自動捨棄，這跟真實挖礦案例的篩選邏輯一致。
#
# A pre-registered catalog of synthetic fault-injection candidates (fixed
# before any repair experiments run, so it isn't tuned post hoc). Each entry
# specifies: category, target file (relative to the Zephyr repo root), the
# mutation operator (defined in tools/mutate_inject.py), and the
# target_app/board used to trigger/verify it. target_app/board are chosen so
# the target file is guaranteed to be loaded/compiled (e.g. a board's own
# DTS is built for that exact board), sidestepping the target-guessing
# problem seen with real mining.
#
# This is a starting catalog, not a guarantee — entries that fail the
# verify_cases.py two-sided gate are discarded automatically, same as mined
# candidates.
INJECTION_CATALOG = [
    # --- Kconfig Dependency and Configuration Conflicts ---
    # samples/hello_world 在 native_sim 上實際 =y 的 libc/logging 符號其實是
    # picolibc + 沒有啟用 CONFIG_LOG，所以原本 target 在 lib/libc/Kconfig /
    # subsys/logging/Kconfig 上的 mutation 根本沒被建置圖用到 (改了等於沒
    # 改，變成「意外建置成功」)。POSIX_ARCH_CONSOLE 才是這個 board 真正靠
    # `depends on ARCH_POSIX` + `select CONSOLE_HAS_DRIVER` 撐起 Hello
    # World 主控台輸出的符號 (已用一次 recon build 的 .config 確認
    # CONFIG_POSIX_ARCH_CONSOLE=y)，改動它保證會被建置圖實際看到。
    # The libc/logging symbols mutated before weren't actually =y for
    # samples/hello_world on native_sim (it uses picolibc, CONFIG_LOG is
    # off), so those mutations never touched anything the build graph used
    # — silently succeeding. POSIX_ARCH_CONSOLE is what actually carries
    # Hello World's console output on this board (confirmed via a recon
    # build's .config: CONFIG_POSIX_ARCH_CONSOLE=y), so mutating it is
    # guaranteed to be exercised.
    # 已實測過 target_app=samples/hello_world 和 target_app=tests/subsys/fs/fcb
    # 兩種組合，兩者都對 POSIX_ARCH_CONSOLE 的 mutation 完全沒反應
    # (west build -t run 依然乾淨成功)。native_sim 上的 stdout 顯然是透過某個
    # 不受這個 Kconfig 開關控制的底層機制送到終端機，這兩筆還沒找到能真正
    # 讓建置/執行失敗的 kconfig mutation 目標，需要之後換一個完全不同的
    # 切入點 (例如改動一個會讓 CMake/Kconfig 本身組態檢查失敗、而不是只影響
    # 某個 driver 是否被編譯進去的符號)。
    # Tried both target_app=samples/hello_world and
    # target_app=tests/subsys/fs/fcb — neither reacts to a POSIX_ARCH_CONSOLE
    # mutation at all (west build -t run still succeeds cleanly either way).
    # stdout on native_sim evidently reaches the terminal through some
    # mechanism this Kconfig switch doesn't gate. These two still need a
    # genuinely different kconfig mutation target — something that fails
    # CMake/Kconfig's own configuration validation, not just whether one
    # driver gets compiled in.
    {
        "id_suffix": "kconfig_libc_stdout",
        "category": "kconfig",
        "target_file": "drivers/console/Kconfig",
        "operator": "kconfig_invert_depends:POSIX_ARCH_CONSOLE",
        "target_app": "samples/hello_world",
        "board": "native_sim",
    },
    {
        "id_suffix": "kconfig_logging_select",
        "category": "kconfig",
        "target_file": "drivers/console/Kconfig",
        "operator": "kconfig_remove_select:POSIX_ARCH_CONSOLE",
        "target_app": "samples/hello_world",
        "board": "native_sim",
    },
    # --- Device Tree (DTS) Node Errors ---
    # 同樣道理：native_sim.dts 根節點的 `compatible = "zephyr,posix"` 沒有
    #任何 binding 真的去檢查它，刪掉不影響建置。flashcontroller0 節點的
    # `compatible = "zephyr,sim-flash"` 則是 tests/subsys/fs/fcb 用來產生
    # storage_partition flash 裝置的必要 binding，刪掉會讓 flash_area_open
    # 找不到底層裝置。
    # Root-node `compatible` isn't checked by any binding, so removing it
    # doesn't affect the build. flashcontroller0's `compatible =
    # "zephyr,sim-flash"` is the binding that produces the storage_partition
    # flash device used by tests/subsys/fs/fcb; removing it breaks
    # flash_area_open's underlying device.
    {
        "id_suffix": "dts_native_sim_compatible",
        "category": "dts",
        "target_file": "boards/native/native_sim/native_sim.dts",
        "operator": "dts_remove_compatible:zephyr,sim-flash",
        "target_app": "tests/subsys/fs/fcb",
        "board": "native_sim",
    },
    {
        "id_suffix": "dts_native_sim_phandle",
        "category": "dts",
        "target_file": "boards/native/native_sim/native_sim.dts",
        "operator": "dts_break_phandle",
        "target_app": "samples/hello_world",
        "board": "native_sim",
    },
    # --- C Syntax and Macro Errors ---
    {
        "id_suffix": "c_hello_world_semicolon",
        "category": "c_syntax",
        "target_file": "samples/hello_world/src/main.c",
        "operator": "c_remove_semicolon",
        "target_app": "samples/hello_world",
        "board": "native_sim",
    },
    {
        "id_suffix": "c_hello_world_brace",
        "category": "c_syntax",
        "target_file": "samples/hello_world/src/main.c",
        "operator": "c_remove_closing_brace",
        "target_app": "samples/hello_world",
        "board": "native_sim",
    },
    # --- Runtime Crashes ---
    # tests/subsys/fs/fcb 已在既有的挖礦驗證中確認過能在 native_sim 上
    # 完整建置並跑完整組 ztest (見 bug_111891 的驗證紀錄)，因此是執行期
    # 崩潰類別最有把握的注入目標：mutation 一定會被測試套件實際執行到。
    # tests/subsys/fs/fcb was already confirmed (via bug_111891's mined-case
    # verification) to fully build and run its ztest suite on native_sim, so
    # it's the most reliable injection target for the runtime-crash category
    # — the mutation is guaranteed to actually be exercised by the tests.
    # 樸素套用 runtime_off_by_one 抓到的第一個「真正」比較式是
    # fcb_append() 裡的一個安全餘裕檢查 (sector->fs_size < ...)，改嚴格一格
    # 只會讓函式更早回傳 -ENOSPC，屬於不痛不癢的保守方向，實測完全不影響
    # ztest 套件的結果 (PROJECT EXECUTION SUCCESSFUL)。真正會被
    # fcb_test_rotate/fcb_test_append 等測試實際命中、且改壞了會出問題的，
    # 是 fcb_new_sector() 迴圈邊界 `while (i++ < cnt)`——用 postinc_loop_bound
    # hint 鎖定這個特定寫法。
    # The first "real" comparison the naive scan finds is a safety-margin
    # check in fcb_append() (sector->fs_size < ...); tightening it by one
    # merely makes the function return -ENOSPC a bit earlier — harmless, and
    # empirically doesn't affect the ztest suite's outcome at all (still
    # PROJECT EXECUTION SUCCESSFUL). The loop bound `while (i++ < cnt)` in
    # fcb_new_sector() is what's actually exercised by
    # fcb_test_rotate/fcb_test_append and breaks things when perturbed — the
    # postinc_loop_bound hint pins the mutation to that specific idiom.
    {
        "id_suffix": "runtime_fcb_offbyone",
        "category": "runtime_crash",
        "target_file": "subsys/fs/fcb/fcb_append.c",
        "operator": "runtime_off_by_one:postinc_loop_bound",
        "target_app": "tests/subsys/fs/fcb",
        "board": "native_sim",
    },
    {
        "id_suffix": "runtime_fcb_nullcheck",
        "category": "runtime_crash",
        "target_file": "subsys/fs/fcb/fcb_getnext.c",
        "operator": "runtime_remove_null_check",
        "target_app": "tests/subsys/fs/fcb",
        "board": "native_sim",
    },
]


class ZephyrBugMiner:
    """
    自動探勘 Zephyr 官方儲存庫中的真實 Bug 案例，用於建構 Zephyr-Eval 基準測試。
    """
    def __init__(self):
        self.repo = "zephyrproject-rtos/zephyr"
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {GITHUB_TOKEN}" if GITHUB_TOKEN else ""
        }
        if not GITHUB_TOKEN:
            logger.warning("未偵測到 GITHUB_TOKEN，API 請求將受到嚴格的速率限制 (60次/小時)。")
        # 快取 GitHub contents API 查詢結果，避免對同一路徑重複發送請求
        self._path_exists_cache = {}

    def search_merged_bug_prs(self, max_results: int = 150, merged_after: str = "2026-03-20") -> list:
        """
        搜尋已合併 (is:merged)、標籤包含 bug (label:bug) 的 Pull Requests。
        支援分頁 (每頁最多 100 筆)，以取得足夠的候選案例。
        Searches for merged PRs labeled `bug`, paginating (100/page) to gather
        enough raw candidates.

        :param merged_after: 只保留這個日期之後合併的 PR (預設 2026-03-20，也就是
            Zephyr commit d204d248769 把最低 Zephyr SDK 需求 bump 到 1.0 之後幾天)。
            太舊的 commit 需要舊版 SDK (如 0.16)，會在我們固定用新版 SDK 的沙盒環境裡
            於 CMake 設定階段就失敗，這是環境版本不合造成的假陽性，不是真正的 bug 重現。
            Only keep PRs merged after this date (default 2026-03-20, a few days
            after Zephyr commit d204d248769 bumped the minimum Zephyr SDK
            requirement to 1.0). Older commits need an older SDK (e.g. 0.16) and
            will fail at CMake configure in our fixed-SDK-version sandbox — an
            environment-version false positive, not a real bug repro.
        """
        logger.info(f"🔍 開始搜尋 {self.repo} 中的 Bug 案例 (目標: {max_results} 筆，merged>={merged_after})...")

        query = f"repo:{self.repo} is:pr is:merged label:bug merged:>={merged_after}"
        items = []
        per_page = 100
        page = 1

        while len(items) < max_results:
            url = (
                f"https://api.github.com/search/issues?q={query}"
                f"&sort=updated&order=desc&per_page={per_page}&page={page}"
            )
            response = requests.get(url, headers=self.headers)
            if response.status_code != 200:
                logger.error(f"搜尋失敗 (page {page}): {response.text}")
                break

            page_items = response.json().get("items", [])
            if not page_items:
                break

            items.extend(page_items)
            logger.info(f"   ↳ 第 {page} 頁: 累計 {len(items)} 個潛在的 PR。")
            page += 1

            # GitHub Search API 的速率限制較嚴格 (30/分鐘)，稍作停頓
            time.sleep(2)

        logger.info(f"✅ 共找到 {len(items)} 個潛在的 PR。")
        return items[:max_results]

    def filter_and_extract_pr_details(self, pr_items: list, max_modified_files: int = 3) -> list:
        """
        過濾 PR，只保留修改過 .c, .conf, Kconfig 或 .dts/.overlay 檔案，
        且修改檔案數量精簡 (預設 <=3) 的案例，以確保黃金修補程式聚焦、易於評估。
        並提取其損壞提交 (Broken Commit) 與黃金修補 (Golden Patch)，
        同時猜測錯誤分類、目標測試應用程式與開發板。

        Filters PRs to ones touching .c/.conf/Kconfig/.dts/.overlay files with a
        small, focused changeset (<=3 files by default), extracts the broken/fixed
        commits, and guesses the bug category, target_app, and board.
        """
        valid_cases = []

        for item in pr_items:
            pr_number = item["number"]
            logger.info(f"⏳ 正在分析 PR #{pr_number}: {item['title']}")

            pr_url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}"
            pr_resp = requests.get(pr_url, headers=self.headers)
            if pr_resp.status_code != 200:
                continue
            pr_data = pr_resp.json()
            broken_commit = pr_data["base"]["sha"]
            fixed_commit = pr_data["head"]["sha"]

            files_url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/files"
            files_resp = requests.get(files_url, headers=self.headers)
            if files_resp.status_code != 200:
                continue
            files_data = files_resp.json()
            modified_files = [f["filename"] for f in files_data]

            has_relevant_files = any(
                f.endswith(".c") or f.endswith(".h") or
                f.endswith(".conf") or "Kconfig" in f or
                f.endswith(".dts") or f.endswith(".dtsi") or f.endswith(".overlay")
                for f in modified_files
            )

            if not has_relevant_files:
                logger.info("   ⏭️ 無相關檔案，跳過此 PR。")
                time.sleep(0.5)
                continue

            if len(modified_files) > max_modified_files:
                logger.info(f"   ⏭️ 修改檔案數過多 ({len(modified_files)} > {max_modified_files})，跳過以確保修補聚焦。")
                time.sleep(0.5)
                continue

            category = self._guess_category(modified_files, item.get("title", ""), item.get("body", "") or "")
            board = self._guess_board(modified_files)
            target_app = self._guess_target_app(modified_files)

            logger.info(f"   🎯 找到相關檔案！分類: {category} | 開發板: {board} | 目標 App: {target_app}")
            valid_cases.append({
                "id": f"bug_{pr_number}",
                "title": item["title"],
                "url": item["html_url"],
                "broken_commit": broken_commit,
                "fixed_commit": fixed_commit,
                "modified_files": modified_files,
                "category": category,
                "target_app": target_app,
                "board": board,
            })

            # 避免觸發 API 限制
            time.sleep(1)

        return valid_cases

    def _guess_category(self, modified_files: list, title: str, body: str) -> str:
        """
        依照修改檔案類型與 PR 標題/內文關鍵字，猜測錯誤分類。
        Guesses the bug category from modified file types and PR title/body keywords.

        注意：真正的「C 語言語法錯誤」極少出現在已合併的歷史紀錄中 (CI 會在合併前擋下)，
        因此這裡的 'c_bug' 代表已修復的 C 邏輯/執行期錯誤，語法錯誤案例建議另外用合成注入方式產生。
        Note: literal C *syntax* errors almost never appear in merged history (CI blocks
        them pre-merge), so 'c_bug' here means a fixed C logic/runtime bug. True syntax-error
        cases should be generated synthetically instead of mined.
        """
        text = f"{title} {body}".lower()
        has_dts = any(f.endswith((".dts", ".dtsi", ".overlay")) for f in modified_files)
        has_kconfig = any("kconfig" in f.lower() or f.endswith(".conf") for f in modified_files)
        has_crash_kw = any(kw in text for kw in CRASH_KEYWORDS)
        has_c = any(f.endswith((".c", ".h")) for f in modified_files)

        if has_dts:
            return "dts"
        if has_kconfig:
            return "kconfig"
        if has_crash_kw and has_c:
            return "runtime_crash"
        if has_c:
            return "c_bug"
        return "other"

    def _guess_board(self, modified_files: list) -> str:
        """
        依修改檔案路徑猜測適合的開發板。
        Guesses a suitable board from the modified file paths.

        優先順序 (Priority order):
        1. 若修改的是 boards/<vendor>/<board_name>/ 底下的檔案，直接用該板子本身
           (該 bug 通常就是那塊板子特有的設定問題，換成別的板子根本不會重現)。
        2. 否則依架構關鍵字對照表猜測 QEMU 板子。
        3. 都猜不到則退回 native_sim。
        """
        for f in modified_files:
            match = BOARD_PATH_RE.search(f)
            if match:
                return match.group(1)

        joined = " ".join(modified_files).lower()
        for pattern, board in ARCH_BOARD_MAP:
            if pattern.search(joined):
                return board
        return "native_sim"

    def _is_buildable_app(self, path: str) -> bool:
        """
        檢查該路徑是否為「可直接建置」的 Zephyr 應用程式根目錄，
        判斷依據是該路徑底下是否存在 CMakeLists.txt (單純的父目錄不算)。
        Checks whether a path is a directly-buildable Zephyr app root by
        checking for a CMakeLists.txt at that exact path (a plain parent
        directory without one is not buildable via `west build <dir>`).
        """
        if path in self._path_exists_cache:
            return self._path_exists_cache[path]
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}/CMakeLists.txt"
        resp = requests.get(url, headers=self.headers)
        exists = resp.status_code == 200
        self._path_exists_cache[path] = exists
        return exists

    def _guess_target_app(self, modified_files: list) -> str:
        """
        嘗試在 tests/ 底下尋找與修改檔案路徑對應、且真的可建置 (含 CMakeLists.txt) 的測試應用程式，
        找不到則退回 samples/hello_world (無法重現的案例會在 verify_cases.py 階段被自動捨棄)。
        Tries to find a tests/ directory mirroring the modified file's path that is
        actually buildable (has a CMakeLists.txt), falling back to samples/hello_world
        (unreproducible cases are discarded automatically during verify_cases.py).
        """
        for f in modified_files:
            parts = f.split("/")
            # 由最深的目錄逐層往上嘗試 (例如 subsys/fs/fcb/fcb.c -> tests/subsys/fs/fcb -> tests/subsys/fs)
            for depth in range(len(parts) - 1, 0, -1):
                candidate = "tests/" + "/".join(parts[:depth])
                if self._is_buildable_app(candidate):
                    return candidate
        return "samples/hello_world"

    def _resolve_main_commit(self) -> str:
        """解析 main 分支目前的 tip commit SHA，作為所有合成注入案例共用的固定 baseline。"""
        url = f"https://api.github.com/repos/{self.repo}/commits/main"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json()["sha"]

    def generate_injection_candidates(self, catalog: list = None) -> list:
        """
        根據預先註冊的 mutation catalog (INJECTION_CATALOG)，產生合成錯誤
        注入候選案例。不需要呼叫 GitHub PR 搜尋 API，只需要解析一次
        baseline commit (main 分支目前的 tip)，所有案例共用同一個 commit，
        徹底避開挖礦時遇到的 SDK/Python 版本漂移問題。

        每筆候選都還沒經過驗證——實際能不能用，交給
        verify_cases.py 的雙向驗證閘 (FaultInjector) 判斷。

        Generates synthetic fault-injection candidates from the pre-registered
        mutation catalog. Doesn't need the PR search API — just resolves the
        baseline commit once (the current tip of main), shared by every
        candidate, entirely avoiding the SDK/Python version drift problem
        seen during mining. Each candidate is unverified until it passes
        verify_cases.py's two-sided gate (FaultInjector).
        """
        if catalog is None:
            catalog = INJECTION_CATALOG

        baseline_commit = self._resolve_main_commit()
        logger.info(f"📌 使用 baseline commit: {baseline_commit}")

        cases = []
        for entry in catalog:
            case_id = f"inject_{entry['id_suffix']}"
            cases.append({
                "id": case_id,
                "title": f"[Injected] {entry['category']}: {entry['operator']} on {entry['target_file']}",
                "category": entry["category"],
                "broken_commit": baseline_commit,
                "fixed_commit": baseline_commit,
                "target_app": entry["target_app"],
                "board": entry["board"],
                "injection": {
                    "target_file": entry["target_file"],
                    "operator": entry["operator"],
                },
            })

        logger.info(f"🧬 產生了 {len(cases)} 筆合成注入候選案例 (尚未驗證)。")
        return cases

    def save_dataset(self, cases: list, output_path: str):
        """將提取的案例儲存為 JSON 檔案"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(cases, f, indent=4, ensure_ascii=False)
        logger.info(f"💾 資料集已儲存至: {output_path} (共 {len(cases)} 筆)")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mine merged bug-fix PRs from the Zephyr repo, or generate synthetic fault-injection candidates, for Zephyr-Eval.")
    parser.add_argument("--mode", choices=["mine", "inject"], default="mine",
                        help="'mine' (default): search real GitHub PRs. 'inject': generate synthetic fault-injection candidates from the pre-registered INJECTION_CATALOG.")
    parser.add_argument("--max-results", type=int, default=150, help="[mine mode] Number of raw PRs to search before filtering")
    parser.add_argument("--max-modified-files", type=int, default=3, help="[mine mode] Skip PRs touching more than this many files")
    parser.add_argument("--output", default=None, help="Output JSON filename under dataset/cases/ (default: zephyr_bugs.json for mine, zephyr_injected_candidates.json for inject)")
    parser.add_argument("--exclude-existing", default=None, help="[mine mode] JSON filename under dataset/cases/ whose ids should be skipped (avoids re-fetching PRs already mined)")
    args = parser.parse_args()

    miner = ZephyrBugMiner()
    from collections import Counter

    if args.mode == "inject":
        valid_cases = miner.generate_injection_candidates()
        output_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cases", args.output or "zephyr_injected_candidates.json"))
        miner.save_dataset(valid_cases, output_file)
        counts = Counter(c["category"] for c in valid_cases)
        logger.info(f"📊 分類統計: {dict(counts)}")
        logger.info("⚠️ 這些是尚未驗證的候選案例，請接著執行 verify_cases.py 跑雙向驗證閘。")
    else:
        exclude_ids = set()
        if args.exclude_existing:
            exclude_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cases", args.exclude_existing))
            if os.path.exists(exclude_path):
                with open(exclude_path, "r", encoding="utf-8") as f:
                    exclude_ids = {c["id"] for c in json.load(f)}
                logger.info(f"🚫 將排除 {len(exclude_ids)} 個已存在於 {args.exclude_existing} 的候選 PR。")

        raw_prs = miner.search_merged_bug_prs(max_results=args.max_results)
        if exclude_ids:
            before = len(raw_prs)
            raw_prs = [item for item in raw_prs if f"bug_{item['number']}" not in exclude_ids]
            logger.info(f"   ↳ 排除後剩 {len(raw_prs)}/{before} 個待分析的 PR。")

        valid_cases = miner.filter_and_extract_pr_details(raw_prs, max_modified_files=args.max_modified_files)

        output_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cases", args.output or "zephyr_bugs.json"))
        miner.save_dataset(valid_cases, output_file)

        # 分類統計 (Category breakdown)
        counts = Counter(c["category"] for c in valid_cases)
        logger.info(f"📊 分類統計: {dict(counts)}")
