# tools/patch_applier.py
import os
import re
import logging
from typing import Dict, Any, List

_NOT_A_PATH_PREFIXES = (">>>>", "<<<<", "====", "---", "+++", "@@", "diff ")
_SEARCH_MARKER_RE = re.compile(r"^<<<<<<<<\s*SEARCH\s*$", re.MULTILINE)


class PatchApplier:
    """
    解析 LLM 輸出的 SEARCH/REPLACE 區塊，並精確修改目標檔案。
    Parses SEARCH/REPLACE blocks output by the LLM and precisely modifies the target files.
    """
    def __init__(self, workspace_path: str):
        """
        :param workspace_path: Zephyr 專案的根目錄絕對路徑 (Absolute path to the Zephyr project root)
        """
        self.workspace_path = os.path.abspath(workspace_path)
        logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
        self.logger = logging.getLogger(__name__)

        # 定義 Aider 風格的區塊正則表達式
        # 匹配格式 (Matching format):
        # <filepath>
        # <<<<<<<< SEARCH
        # <original code>
        # ========
        # <new code>
        # >>>>>>>> REPLACE
        self.block_pattern = re.compile(
            r"([^\r\n]+)\r?\n"                     # Capture Filepath
            r"<<<<<<<<\s*SEARCH\r?\n"              # SEARCH header
            r"(.*?)\r?\n"                          # Capture original code (Search block)
            r"========\r?\n"                       # Divider
            r"(.*?)\r?\n"                          # Capture new code (Replace block)
            r">>>>>>>>\s*REPLACE",                 # REPLACE footer
            re.DOTALL | re.MULTILINE
        )

    def apply_patches(self, llm_output: str) -> Dict[str, Any]:
        """
        從 LLM 的輸出中提取所有修改區塊並應用到實體檔案。
        Extracts all modification blocks from the LLM's output and applies them to physical files.
        """
        # 尋找所有符合的區塊 (Find all matching blocks)
        matches = self.block_pattern.findall(llm_output)
        
        if not matches:
            self.logger.warning("No valid SEARCH/REPLACE blocks found in LLM output.")
            return {
                "success": False,
                "error": "FormatError: LLM output did not contain valid <<<<<<<< SEARCH / ======== / >>>>>>>> REPLACE blocks."
            }

        applied_files = []
        errors = []
        # 每個檔案第一次被修改前的內容 (StaticCheck 用來只回報這次 patch 新增的 cppcheck 問題)。
        # Each file's content before this patch first changed it (StaticCheck uses it to report only
        # cppcheck findings the patch introduced).
        original_contents: Dict[str, str] = {}

        for match in matches:
            filepath_raw, search_block, replace_block = match
            
            # 清理檔案路徑 (通常 LLM 可能會在路徑前後加上空格或 Markdown 的 backticks)
            # Clean filepath (LLMs might add spaces or backticks around the path)
            filepath = filepath_raw.strip().strip("`").strip()
            # prompt 範本用 "<relative path of the file>" 表示路徑，模型常連角括號一起照抄
            # (2.5-flash：B2 15 筆、B3 12 筆、Proposed 16 筆因此套用失敗)；去掉包住整個路徑的一對角括號
            # (2026-10-13)。"<<<<" 這類區塊標記不受影響。
            # The prompt template writes the path as "<relative path of the file>" and models often copy
            # the brackets (2.5-flash: 15 B2, 12 B3, 16 Proposed cases failed to apply because of it); strip
            # one pair of brackets wrapping the whole path (2026-10-13). Block markers like "<<<<" are untouched.
            if filepath.startswith("<") and filepath.endswith(">") and not filepath.startswith("<<"):
                filepath = filepath[1:-1].strip()
            
            # 組合絕對路徑 (Assemble absolute path)
            full_path = os.path.join(self.workspace_path, filepath)

            # 驗證檔案是否存在 (Verify file exists)
            if not os.path.exists(full_path):
                err_msg = f"TargetFileNotFound: Target file '{filepath}' not found."
                # 解析規則不變 (2026-10-08 決定)；「檔名」其實是區塊標記或 diff 標頭時補一句說明，
                # 否則 agent 只看到 "Target file '>>>>>>>> REPLACE' not found" 不知道錯在哪 (2026-10-13)。
                # Parsing is unchanged (decided 2026-10-08); when the "path" is really a block marker or a
                # diff header, explain why, since "Target file '>>>>>>>> REPLACE' not found" alone is
                # unhelpful (2026-10-13).
                if filepath.startswith(_NOT_A_PATH_PREFIXES):
                    err_msg += (" Each SEARCH/REPLACE block must be immediately preceded by its own line containing "
                                "only the file's relative path (repeat the path for every block, even in the same "
                                "file); do not use diff headers such as '--- a/file' or '+++ b/file'.")
                self.logger.error(err_msg)
                errors.append(err_msg)
                continue

            # 讀取並修改檔案 (Read and modify file)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()

                # 統一換行符號為 \n 以確保匹配成功 (Normalize newlines for accurate matching)
                normalized_content = file_content.replace('\r\n', '\n')
                normalized_search = search_block.replace('\r\n', '\n')
                normalized_replace = replace_block.replace('\r\n', '\n')

                # 嚴格匹配檢查 (Strict match check)
                if normalized_search not in normalized_content:
                    err_msg = (
                        f"SearchMatchFailed: No exactly matching SEARCH block was found in '{filepath}'.\n"
                        f"Make sure the content of the SEARCH block is 100% identical to the original code in the file (including indentation)."
                    )
                    self.logger.error(err_msg)
                    errors.append(err_msg)
                    continue
                
                # 執行替換 (Execute replacement)
                # 限制只替換第一次出現的位置，避免誤傷重複的程式碼片段
                new_content = normalized_content.replace(normalized_search, normalized_replace, 1)
                original_contents.setdefault(filepath, file_content)

                with open(full_path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(new_content)

                self.logger.info(f"Successfully patched: {filepath}")
                applied_files.append(filepath)

            except Exception as e:
                err_msg = f"FileWriteError: Cannot read/write file '{filepath}'. Error: {str(e)}"
                self.logger.error(err_msg)
                errors.append(err_msg)

        # 彙整結果 (Summarize results)
        # 沒有路徑行的區塊 (例如同一個檔名底下連續寫第二個區塊) 不會被 block_pattern 解析到，之前會被
        # 靜默略過、整體仍回報成功，agent 以為全部套用了。現在明確報錯 (2026-10-13)；能解析的區塊照常套用。
        # Blocks without a path line (e.g. a second block under the same file name) are not captured by
        # block_pattern; they used to be skipped silently while the patch reported success, so the agent
        # believed everything applied. Now an explicit error (2026-10-13); parsable blocks still apply.
        orphan_count = len(_SEARCH_MARKER_RE.findall(llm_output)) - len(matches)
        if orphan_count > 0:
            err_msg = (f"FormatError: {orphan_count} SEARCH/REPLACE block(s) had no file path line and were not "
                       f"applied. Each SEARCH/REPLACE block must be immediately preceded by its own line containing "
                       f"only the file's relative path (repeat the path for every block, even in the same file).")
            self.logger.error(err_msg)
            errors.append(err_msg)

        if errors:
            return {
                "success": False,
                "applied_files": applied_files,
                "original_contents": original_contents,
                "error": "\n".join(errors)
            }
        
        return {
            "success": True,
            "applied_files": applied_files,
            "original_contents": original_contents,
            "error": ""
        }

    def apply_full_file(self, filepath: str, content: str) -> Dict[str, Any]:
        """
        B1 (Zero-Shot LLM) baseline 專用：整檔覆寫，不是 SEARCH/REPLACE——
        B1 依 Table 2 定義完全看不到原始檔案內容，沒辦法產生需要逐字元比對
        SEARCH 區塊的 patch，只能整檔輸出它猜測修正後的樣子。

        只允許覆寫這次 workspace 裡真的存在的檔案，不接受用這個管道新建
        檔案：檔案是否存在本身不洩漏 injection 資訊 (Zephyr 原始碼樹的檔案
        清單是公開資訊，B1 沒被告知的是內容跟哪個檔案真的壞了)，但接受
        任意路徑新建檔案會讓模型亂猜路徑寫出垃圾檔案，汙染 workspace。

        For the B1 (Zero-Shot LLM) baseline: overwrites the whole file
        rather than SEARCH/REPLACE — per Table 2's definition B1 never sees
        the original file content, so it can't produce a patch requiring an
        exact-text SEARCH match; it can only output its guess at the fixed
        file in full.

        Only overwrites a file that genuinely already exists in this
        workspace; refuses to create new files through this path — a
        file's mere existence doesn't leak injection info (Zephyr's file
        listing is public; what B1 isn't told is which file's content is
        actually broken), but allowing arbitrary new paths would let the
        model write junk files at made-up locations.
        """
        filepath = filepath.strip().strip("`").strip()
        full_path = os.path.join(self.workspace_path, filepath)

        if not os.path.exists(full_path):
            err_msg = f"TargetFileNotFound: Target file '{filepath}' not found."
            self.logger.error(err_msg)
            return {"success": False, "applied_files": [], "error": err_msg}

        try:
            with open(full_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            self.logger.info(f"Successfully overwrote full file: {filepath}")
            return {"success": True, "applied_files": [filepath], "error": ""}
        except Exception as e:
            err_msg = f"FileWriteError: Cannot read/write file '{filepath}'. Error: {str(e)}"
            self.logger.error(err_msg)
            return {"success": False, "applied_files": [], "error": err_msg}

# ==========================================
# 測試區塊 (Testing Block)
# ==========================================
if __name__ == "__main__":
    # 建立一個測試環境 (Create a test environment)
    test_dir = "./test_workspace"
    os.makedirs(test_dir, exist_ok=True)
    
    # 建立一個假的 C 檔案 (Create a dummy C file)
    test_file_path = os.path.join(test_dir, "src/main.c")
    os.makedirs(os.path.dirname(test_file_path), exist_ok=True)
    with open(test_file_path, "w", encoding="utf-8") as f:
        f.write("#include <zephyr/kernel.h>\n\nvoid main(void) {\n    printk(\"Buggy Code\");\n}\n")

    # 模擬 LLM 輸出的修補指令 (Simulate LLM patch output)
    dummy_llm_output = """
I found the error. You need to add the correct quotes and semicolon. Please apply the following change:

src/main.c
<<<<<<<< SEARCH
void main(void) {
    printk("Buggy Code");
}
========
void main(void) {
    printk("Fixed Code!");
}
>>>>>>>> REPLACE

Please recompile after making the change.
    """

    print("=== Original File Content ===")
    with open(test_file_path, "r") as f:
        print(f.read())

    print("=== Executing Patch ===")
    applier = PatchApplier(workspace_path=test_dir)
    result = applier.apply_patches(dummy_llm_output)

    if result["success"]:
        print("\n✅ Patch Successful!")
        print("=== New File Content ===")
        with open(test_file_path, "r") as f:
            print(f.read())
    else:
        print("\n❌ Patch Failed:")
        print(result["error"])
        
    # 清理測試檔案 (Clean up)
    import shutil
    shutil.rmtree(test_dir)