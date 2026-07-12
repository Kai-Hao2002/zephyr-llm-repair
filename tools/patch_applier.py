# tools/patch_applier.py
import os
import re
import logging
from typing import Dict, Any, List

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
            self.logger.warning("在 LLM 輸出中找不到有效的 SEARCH/REPLACE 區塊。(No valid SEARCH/REPLACE blocks found in LLM output.)")
            return {
                "success": False,
                "error": "FormatError: LLM output did not contain valid <<<<<<<< SEARCH / ======== / >>>>>>>> REPLACE blocks."
            }

        applied_files = []
        errors = []

        for match in matches:
            filepath_raw, search_block, replace_block = match
            
            # 清理檔案路徑 (通常 LLM 可能會在路徑前後加上空格或 Markdown 的 backticks)
            # Clean filepath (LLMs might add spaces or backticks around the path)
            filepath = filepath_raw.strip().strip("`").strip()
            
            # 組合絕對路徑 (Assemble absolute path)
            full_path = os.path.join(self.workspace_path, filepath)

            # 驗證檔案是否存在 (Verify file exists)
            if not os.path.exists(full_path):
                err_msg = f"TargetFileNotFound: 找不到目標檔案 '{filepath}' (Target file not found)."
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
                        f"SearchMatchFailed: 在 '{filepath}' 中找不到完全匹配的 SEARCH 區塊。\n"
                        f"請確保 SEARCH 區塊的內容與檔案中的原始碼 100% 相同 (包含縮排)。"
                    )
                    self.logger.error(err_msg)
                    errors.append(err_msg)
                    continue
                
                # 執行替換 (Execute replacement)
                # 限制只替換第一次出現的位置，避免誤傷重複的程式碼片段
                new_content = normalized_content.replace(normalized_search, normalized_replace, 1)

                with open(full_path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(new_content)

                self.logger.info(f"成功修補檔案 (Successfully patched): {filepath}")
                applied_files.append(filepath)

            except Exception as e:
                err_msg = f"FileWriteError: 無法讀寫檔案 '{filepath}'. 錯誤: {str(e)}"
                self.logger.error(err_msg)
                errors.append(err_msg)

        # 彙整結果 (Summarize results)
        if errors:
            return {
                "success": False,
                "applied_files": applied_files,
                "error": "\n".join(errors)
            }
        
        return {
            "success": True,
            "applied_files": applied_files,
            "error": ""
        }

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
我找到了錯誤。你需要補上正確的引號與分號。請套用以下修改：

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

修改完畢後請重新編譯。
    """

    print("=== 原始檔案內容 (Original File Content) ===")
    with open(test_file_path, "r") as f:
        print(f.read())

    print("=== 執行修補 (Executing Patch) ===")
    applier = PatchApplier(workspace_path=test_dir)
    result = applier.apply_patches(dummy_llm_output)

    if result["success"]:
        print("\n✅ 修補成功！ (Patch Successful!)")
        print("=== 新檔案內容 (New File Content) ===")
        with open(test_file_path, "r") as f:
            print(f.read())
    else:
        print("\n❌ 修補失敗 (Patch Failed):")
        print(result["error"])
        
    # 清理測試檔案 (Clean up)
    import shutil
    shutil.rmtree(test_dir)