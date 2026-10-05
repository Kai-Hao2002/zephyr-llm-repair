# tools/log_filter
import re
import logging
from collections import deque
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
        # "fatal error:" (e.g. missing header) must match too, not only plain "error:".
        self.c_error_re = re.compile(r"(?i)^.*:\d+:\d+:\s+(?:fatal\s+)?error:\s+.*")
        
        # 2. CMake 配置錯誤 (e.g., CMake Error at CMakeLists.txt:10)
        self.cmake_error_re = re.compile(r"^CMake Error.*:")
        
        # 3. Device Tree (DTC) 錯誤 (e.g., Error: zzz.dts:45.1-10 syntax error)
        # 舊版只要求開頭是 "Error"，會誤判任何以 error: 開頭的無關訊息
        # (例如 git/curl 網路錯誤 "error: RPC failed; curl 56 ...")。
        # 現在要求明確是 "devicetree error"，或是 dtc 編譯器標準格式
        # "error: xxx.dts(i):行號..."，才算真正的 DTS 錯誤。
        # The old pattern only required a line starting with "Error", which
        # false-matched unrelated messages (e.g. git/curl network errors like
        # "error: RPC failed; curl 56 ..."). Now requires an explicit
        # "devicetree error" or the dtc compiler's standard
        # "error: xxx.dts(i):line..." format.
        self.dts_error_re = re.compile(r"(?i)^(?:devicetree error|error:\s*\S+\.dtsi?:\d+)")
        
        # 4. Kconfig 設定衝突錯誤 (e.g., warning: <symbol> (defined at Kconfig:15) ...)
        # 注意：在 Kconfig 中，嚴重衝突有時會以 warning 顯示，但導致後續失敗，因此特定 warning 也要抓取
        self.kconfig_error_re = re.compile(r"(?i).*(?:Kconfig|prj\.conf).*:\d+:\s+(?:error|warning):\s+.*")
        
        # 5. Ninja 建置中斷提示
        self.ninja_fatal_re = re.compile(r"^ninja: build stopped:.*")

        # 6. Zephyr 自己的 Kconfig 警告格式：不帶 "檔名:行號:" 前綴，而是
        # "warning: SYM (defined at 檔案:行號) was assigned the value 'y' but got 'n'..."，
        # 且說明文字會折成多行。舊的 kconfig_error_re 要求 "檔名:行號: warning:"，
        # 完全對不上這個格式，導致真正的 Kconfig 依賴錯誤被整段丟掉。
        # Zephyr's own Kconfig warning format has no "file:line:" prefix and wraps
        # onto several lines; kconfig_error_re never matched it.
        self.kconfig_warning_start_re = re.compile(r"^warning:\s+\S+.*")
        self.kconfig_warning_end_re = re.compile(
            r"^(?:warning:|error:|--\s|Parsing |Loaded |Merged |Configuration saved|"
            r"Kconfig header|CMake |ninja:|\[\d+/\d+\])"
        )
        self.kconfig_abort_re = re.compile(r"^error:\s+Aborting due to Kconfig warnings")
        self.kconfig_warning_max_lines = 8

        # 7. 連結期錯誤 (e.g., undefined reference to `foo')。同一個符號常被重複上百次，
        # 依符號去重並設上限，避免洗掉真正的線索。
        # Link-time errors; one symbol is often repeated hundreds of times, so
        # dedupe by symbol and cap the total.
        self.link_error_re = re.compile(
            r"(?i)(?:undefined reference to|multiple definition of|cannot find -l|collect2: error)"
        )
        self.link_symbol_re = re.compile(r"(?:undefined reference to|multiple definition of)\s+[`'\"]?([^'`\"\s]+)")
        self.link_error_max_symbols = 15

        # 8. 去重後的 C/C++ 編譯錯誤上限：保留前 c_error_max_head 筆與最後 c_error_max_tail 筆。
        # Cap on distinct C/C++ compilation errors: keep the first head and the last tail entries.
        self.c_error_max_head = 15
        self.c_error_max_tail = 5

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
        kconfig_warning_lines_left = 0
        seen_dedupe = set()
        link_symbols = set()
        link_omitted = 0
        dup_omitted = 0
        c_error_count = 0
        c_error_tail = deque(maxlen=self.c_error_max_tail)

        for line in lines:
            # 清除顏色代碼並去除前後空白
            clean_line = ansi_escape.sub('', line).strip()

            if not clean_line:
                kconfig_warning_lines_left = 0
                continue

            # --- Kconfig 警告的多行延續 ---
            if kconfig_warning_lines_left > 0:
                if not self.kconfig_warning_end_re.match(clean_line):
                    extracted_lines.append(clean_line)
                    kconfig_warning_lines_left -= 1
                    continue
                kconfig_warning_lines_left = 0

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
                # 同一行錯誤 (缺標頭、標頭內的同一個巨集展開錯誤) 會隨每個編譯單元或每個
                # 呼叫點重複出現，只留第一次。
                # The same error line repeats per compilation unit / call site; keep the first.
                if clean_line in seen_dedupe:
                    dup_omitted += 1
                    continue
                seen_dedupe.add(clean_line)
                c_error_count += 1
                block = ["\n[C/C++ Compilation Error Detected]", clean_line]
                if c_error_count <= self.c_error_max_head:
                    extracted_lines.extend(block)
                else:
                    # 少一個右大括號會讓後面每個函式都報錯 (v3_c_heap_kasan_brace: 446 筆、91 KB)；
                    # 保留前段與最後幾筆 (gcc 最後的 "expected ... at end of input" 才指出真正位置)。
                    # A missing brace cascades into hundreds of errors; keep the head and the last
                    # few (gcc's final "expected ... at end of input" points at the real location).
                    c_error_tail.append(block)
            
            elif self.dts_error_re.match(clean_line):
                extracted_lines.append("\n[DeviceTree Error Detected]")
                extracted_lines.append(clean_line)
            
            elif self.kconfig_error_re.match(clean_line):
                extracted_lines.append("\n[Kconfig/Configuration Error Detected]")
                extracted_lines.append(clean_line)

            elif self.kconfig_warning_start_re.match(clean_line):
                extracted_lines.append("\n[Kconfig/Configuration Error Detected]")
                extracted_lines.append(clean_line)
                kconfig_warning_lines_left = self.kconfig_warning_max_lines

            elif self.kconfig_abort_re.match(clean_line):
                extracted_lines.append("\n[Kconfig/Configuration Error Detected]")
                extracted_lines.append(clean_line)

            elif self.link_error_re.search(clean_line):
                m = self.link_symbol_re.search(clean_line)
                key = m.group(1) if m else clean_line
                if key in link_symbols:
                    continue
                if len(link_symbols) >= self.link_error_max_symbols:
                    link_omitted += 1
                    continue
                if not link_symbols:
                    extracted_lines.append("\n[Linker Error Detected]")
                link_symbols.add(key)
                extracted_lines.append(clean_line)
                
            elif self.ninja_fatal_re.match(clean_line):
                extracted_lines.append("\n[Ninja Build Stopped]")
                extracted_lines.append(clean_line)

        if c_error_count > self.c_error_max_head:
            omitted = c_error_count - self.c_error_max_head - len(c_error_tail)
            if omitted:
                extracted_lines.append(f"\n[... {omitted} more distinct compilation errors omitted]")
            for block in c_error_tail:
                extracted_lines.extend(block)
        if link_omitted:
            extracted_lines.append(f"[... {link_omitted} more distinct link errors omitted]")
        if dup_omitted:
            extracted_lines.append(f"[... {dup_omitted} duplicate compilation error lines omitted]")

        compressed_output = "\n".join(extracted_lines).strip()

        # 程式有開機執行 (看得到開機橫幅) 時，一律附上執行期的尾段：建置期規則
        # 可能只命中無害的 Kconfig 警告 (例如 MAX_THREAD_BYTES/USERSPACE)，
        # 舊版因此不再輸出尾段，v2 有 22 筆 runtime_crash 的 assertion 就這樣
        # 被整段丟掉 (見 eval_runs/v2/analysis/failure_report.html 第 3.1 節)。
        # When the program booted, always append the runtime tail: build-phase rules
        # may match only a benign Kconfig warning, and the old code then dropped the
        # tail - hiding the assertion in 22 v2 runtime_crash cases.
        if compressed_output:
            runtime_tail = self._runtime_tail(lines, ansi_escape)
            if runtime_tail:
                compressed_output += "\n\n[Runtime Log Tail]\n" + runtime_tail

        if not compressed_output:
            self.logger.warning("No standard patterns matched, returning tail.")
            fallback_lines = [ansi_escape.sub('', l).strip() for l in lines[-20:]]
            return "[Fallback Raw Tail]\n" + "\n".join(fallback_lines)

        return compressed_output

    _BOOT_BANNER_RE = re.compile(r"\*\*\* Booting Zephyr OS")
    _TEST_START_RE = re.compile(r"^START - \w+")
    _FAILURE_RE = re.compile(r"Assertion failed|ASSERTION FAIL|^FAIL - |ZEPHYR FATAL ERROR|Segmentation fault|Fault")
    runtime_tail_max_lines = 40

    def _runtime_tail(self, lines: List[str], ansi_escape) -> str:
        """開機橫幅之後的輸出：從最後一個 `START - <test>` 開始 (最多 40 行)，
        沒有 ztest 輸出時取最後 20 行；沒開機則回傳空字串。
        Output after the boot banner: from the last `START - <test>` line (max 40
        lines), or the last 20 lines when there is no ztest output; '' if never booted."""
        boot_idx = None
        for i, l in enumerate(lines):
            if self._BOOT_BANNER_RE.search(l):
                boot_idx = i
        if boot_idx is None:
            return ""
        run = [ansi_escape.sub('', l).strip() for l in lines[boot_idx:]]
        run = [l for l in run if l]
        # 有失敗訊息時，從第一個失敗之前最近的 START 開始；否則從最後一個 START 開始。
        # With a failure marker, start at the START line preceding the first failure;
        # otherwise at the last START line.
        first_fail = next((i for i, l in enumerate(run) if self._FAILURE_RE.search(l)), None)
        search_end = first_fail + 1 if first_fail is not None else len(run)
        start = None
        for i, l in enumerate(run[:search_end]):
            if self._TEST_START_RE.match(l):
                start = i
        tail = run[start:] if start is not None else run[-20:]
        if len(tail) > self.runtime_tail_max_lines:
            tail = tail[:self.runtime_tail_max_lines // 2] + ["[...]"] + tail[-self.runtime_tail_max_lines // 2:]
        return "\n".join(tail)


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
    
    print("=== Raw Log Length: {} characters ===".format(len(dummy_raw_log)))
    
    compressed = log_filter.compress_log(dummy_raw_log)
    
    print("\n=== Compressed Minimal Repro Log ===")
    print(compressed)
    print("\n=== Compressed Length: {} characters ===".format(len(compressed)))