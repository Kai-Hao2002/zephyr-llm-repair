# dataset/scripts/mine_commits.py
import os
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

    def search_merged_bug_prs(self, max_results: int = 30) -> list:
        """
        搜尋已合併 (is:merged)、標籤包含 bug (label:bug) 的 Pull Requests。
        """
        logger.info(f"🔍 開始搜尋 {self.repo} 中的 Bug 案例...")
        
        # GitHub Search API 查詢語法
        query = f"repo:{self.repo} is:pr is:merged label:bug"
        url = f"https://api.github.com/search/issues?q={query}&sort=updated&order=desc&per_page={max_results}"

        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            logger.error(f"搜尋失敗: {response.text}")
            return []

        items = response.json().get("items", [])
        logger.info(f"✅ 成功找到 {len(items)} 個潛在的 PR。")
        return items

    def filter_and_extract_pr_details(self, pr_items: list) -> list:
        """
        過濾 PR，只保留修改過 .c, .conf, Kconfig 或 .dts/.overlay 檔案的案例，
        並提取其損壞提交 (Broken Commit) 與黃金修補 (Golden Patch)。
        """
        valid_cases = []
        
        for item in pr_items:
            pr_number = item["number"]
            logger.info(f"⏳ 正在分析 PR #{pr_number}: {item['title']}")
            
            # 獲取 PR 詳細資訊 (包含 base/head commit)
            pr_url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}"
            pr_resp = requests.get(pr_url, headers=self.headers)
            
            if pr_resp.status_code != 200:
                continue
                
            pr_data = pr_resp.json()
            broken_commit = pr_data["base"]["sha"]  # 分支起點 (尚未修復的狀態)
            fixed_commit = pr_data["head"]["sha"]   # 修復後的狀態
            
            # 獲取修改的檔案列表
            files_url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/files"
            files_resp = requests.get(files_url, headers=self.headers)
            
            if files_resp.status_code != 200:
                continue
                
            files_data = files_resp.json()
            modified_files = [f["filename"] for f in files_data]
            
            # 過濾條件：我們只對嵌入式相關的核心檔案感興趣
            # 排除只修改 README 或文件 (.rst, .md) 的 PR
            has_relevant_files = any(
                f.endswith(".c") or f.endswith(".h") or 
                f.endswith(".conf") or "Kconfig" in f or 
                f.endswith(".dts") or f.endswith(".dtsi") or f.endswith(".overlay")
                for f in modified_files
            )
            
            if has_relevant_files:
                logger.info(f"   🎯 找到相關檔案！加入資料集。")
                valid_cases.append({
                    "id": f"bug_{pr_number}",
                    "title": item["title"],
                    "url": item["html_url"],
                    "broken_commit": broken_commit,
                    "fixed_commit": fixed_commit,
                    "modified_files": modified_files
                })
            else:
                logger.info(f"   ⏭️ 無相關檔案，跳過此 PR。")
                
            # 避免觸發 API 限制
            time.sleep(1)

        return valid_cases

    def save_dataset(self, cases: list, output_path: str):
        """將提取的案例儲存為 JSON 檔案"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(cases, f, indent=4, ensure_ascii=False)
        logger.info(f"💾 資料集已儲存至: {output_path} (共 {len(cases)} 筆)")

if __name__ == "__main__":
    miner = ZephyrBugMiner()
    
    # 1. 搜尋最新的 30 個 bug PR
    raw_prs = miner.search_merged_bug_prs(max_results=30)
    
    # 2. 深入分析並過濾出適合的案例
    valid_cases = miner.filter_and_extract_pr_details(raw_prs)
    
    # 3. 儲存結果
    output_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cases", "zephyr_bugs.json"))
    miner.save_dataset(valid_cases, output_file)