# tools/log_filter
import re
import logging
from typing import List

class LogFilter:
    """
    分層日誌過濾器。
    負責將冗長的 Zephyr 建置日誌壓縮為「最小重現日誌 (Minimal Repro Log)」，
    僅保留對 LLM 代理人有除錯價值的 Fatal Errors 與上下文。
    
    Hierarchical Log Filter.
    Compresses verbose Zephyr build logs into a "Minimal Repro Log",
    retaining only Fatal Errors and context valuable for LLM debugging.
    """
    def __init__(self):
        logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
        self.logger = logging.getLogger(__name__)

        # 定義核心錯誤特徵的正規表示式 (Regex patterns for core errors)
        
        # 1. C 語言 / GCC / Clang 編譯錯誤 (e.g., src/main.c:12:3: error: undefined reference)
        self.c_error_re = re.compile(r"(?i)^.*:\d+:\d+:\s+error:\s+.*")
        
        # 2. CMake 配置錯誤 (e.g., CMake Error at CMakeLists.txt:10)
        self.cmake_error_re = re.compile(r"^CMake Error.*:")
        
        # 3. Device Tree (DTC) 錯誤 (e.g., Error: zzz.dts:45.1-10 syntax error)
        self.dts_error_re = re.compile(r"(?i)^(?:Error|devicetree error).*")
        
        # 4. Kconfig 設定衝突錯誤 (e.g., warning: <symbol> (defined at Kconfig:15) ...)
        # 注意：在 Kconfig 中，嚴重衝突有時會以 warning 顯示，但導致後續失敗，因此特定 warning 也要抓取
        self.kconfig_error_re = re.compile(r"(?i).*(?:Kconfig|prj\.conf).*:\d+:\s+(?:error|warning):\s+.*")
        
        # 5. Ninja 建置中斷提示
        self.ninja_fatal_re = re.compile(r"^ninja: build stopped:.*")

    def compress_log(self, raw_log: str) -> str:
        """
        輸入原始編譯日誌字串，回傳高密度的錯誤摘要。
        Takes raw build log string, returns a high-density error summary.
        """
        if not raw_log or not raw_log.strip():
            return "No log provided."

        # 新增：用於清除 ANSI 顏色代碼的正則表達式
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
        lines = raw_log.splitlines()
        extracted_lines: List[str] = []
        capturing_cmake_stack = False 

        for line in lines:
            # 清除顏色代碼並去除前後空白
            clean_line = ansi_escape.sub('', line).strip()
            
            if not clean_line:
                continue

            # --- 處理 CMake 的連續 Call Stack ---
            if capturing_cmake_stack:
                if clean_line.startswith("Call Stack (most recent call first):") or clean_line.startswith("CMake Error"):
                    extracted_lines.append(clean_line)
                    continue
                elif clean_line.startswith("-"): 
                    extracted_lines.append(clean_line)
                    continue
                else:
                    capturing_cmake_stack = False 

            # --- 核心特徵比對 (使用 clean_line) ---
            if self.cmake_error_re.match(clean_line):
                extracted_lines.append("\n[CMake Error Detected]")
                extracted_lines.append(clean_line)
                capturing_cmake_stack = True
            
            elif self.c_error_re.match(clean_line):
                extracted_lines.append("\n[C/C++ Compilation Error Detected]")
                extracted_lines.append(clean_line)
            
            elif self.dts_error_re.match(clean_line):
                extracted_lines.append("\n[DeviceTree Error Detected]")
                extracted_lines.append(clean_line)
            
            elif self.kconfig_error_re.match(clean_line):
                extracted_lines.append("\n[Kconfig/Configuration Error Detected]")
                extracted_lines.append(clean_line)
                
            elif self.ninja_fatal_re.match(clean_line):
                extracted_lines.append("\n[Ninja Build Stopped]")
                extracted_lines.append(clean_line)

        compressed_output = "\n".join(extracted_lines).strip()
        
        if not compressed_output:
            self.logger.warning("未匹配到標準錯誤特徵，回傳日誌尾端作為備用 (No standard patterns matched, returning tail).")
            fallback_lines = [ansi_escape.sub('', l).strip() for l in lines[-20:]]
            return "[Fallback Raw Tail]\n" + "\n".join(fallback_lines)

        return compressed_output


# ==========================================
# 測試區塊 (Testing Block)
# ==========================================
if __name__ == "__main__":
    # 模擬一份超過 50 行的冗長 Zephyr 編譯日誌 (Simulated verbose Zephyr build log)
    dummy_raw_log = """
[1/10] Built target offsets
[2/10] Building C object zephyr/CMakeFiles/zephyr.dir/lib/os/dec.c.obj
-- Zephyr version: 3.5.0
-- Found Python3: /usr/bin/python3 (found suitable exact version "3.10.12")
-- Found west (found suitable version "1.1.0", minimum required is "0.7.1")
-- Board: qemu_x86
[INFO] Generating Devicetree...
[INFO] Parsing Kconfig...
warning: MY_CUSTOM_SYMBOL (defined at Kconfig:45) has direct dependencies FALSE with value y
[3/10] Building C object zephyr/CMakeFiles/zephyr.dir/src/main.c.obj
/workspace/src/main.c:15:5: error: expected ';' before 'return'
   15 |     printk("Hello World")
      |                         ^
      |                         ;
   16 |     return 0;
      |     ~~~~~~
[INFO] Compiler progress: 30%
CMake Error at CMakeLists.txt:42 (add_subdirectory):
  add_subdirectory given source "src/broken_module" which is not an existing
  directory.
Call Stack (most recent call first):
  /zephyr/CMakeLists.txt:50 (include)
ninja: build stopped: subcommand failed.
    """

    log_filter = LogFilter()
    
    print("=== 原始日誌長度 (Raw Log Length): {} 字元 ===".format(len(dummy_raw_log)))
    
    compressed = log_filter.compress_log(dummy_raw_log)
    
    print("\n=== 過濾後的最小重現日誌 (Compressed Minimal Repro Log) ===")
    print(compressed)
    print("\n=== 壓縮後長度 (Compressed Length): {} 字元 ===".format(len(compressed)))