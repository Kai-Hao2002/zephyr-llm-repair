# Proposed 失敗案例逐筆分析與優化方向（v3 r2，gemini-2.5-flash，2026-10-18）

Proposed 在 r2 有 61/139 筆沒修好。資料來源是 `failure_by_category.json` 和各案例的 trajectory。

表格欄位說明：
- **各輪狀態**：每一輪結束的狀態。B 是 build 失敗，C 是執行時 crash 或 assertion 失敗，F 是 patch 套用失敗，S 是 StaticCheck 失敗，V 是違反規格（改了測試語意，不算修好）。
- **注入檔是否看得到**：log 表示出現在初始 log，app 表示在 target_app 底下（會出現在上下文裡），retr 表示被檢索到，– 表示都沒有。

## 失敗原因分類（61 筆）

| 原因 | 筆數 | 案例 |
|---|---|---|
| A. 注入在核心程式，找不到注入檔 | 12 | sem、stack、thrd_join、p4wq、cobs、work、mcuboot、clock_getres、eeprom_sim、fcb、can_loopback、bmi160_emul |
| B. compound：只修好一半 | 12 | 只修 DTS 的 10 筆：dac_emul、akm09918c、bq40z50、can_shell、eeprom_shell、gnss_api、gpio_kbd_matrix、gpio_keys、regulator、sy24561。只修 Kconfig 的 2 筆：adc_emul、wm8960 |
| C. kconfig：從沒改過 Kconfig 檔 | 7 | bbram_emul、fat_filesystem、flash_map、gpio_emul、adltc2990、bc12、zms |
| D. 注入在測試檔，反覆違反規格 | 8 | early_sleep、mutex_recursive、sys_mutex、retained_mem、devicetree_init_order、iterable_sections、mslab、sys_sem |
| E. c_syntax：錯誤出現在標頭檔，沒找到注入檔 | 6 | heap_kasan、ringbuffer、counter_capture、lifo_api、sys_util、uuid |
| F. 改到注入檔，但修法錯誤或沒修完 | 16 | runtime 6 筆：event_reset、modem_ubx、backends_tty、pending_fifo、multi_heap、spsc_pbuf。dts 3 筆：espi、ina228、led_api。compound 兩半都改到的 4 筆：i2c_emul、bmi160、bq27z746、reset_mmio。kconfig 1 筆：img_manager。c_syntax 2 筆：thread_apis、timespec |

### A. 注入在核心程式，找不到注入檔（12 筆）

- 初始訊號是測試裡的 assertion、`Segmentation fault` 或 CPU exception，真正的錯誤在 kernel、lib 或 drivers 裡。
- 這 12 筆的注入檔沒有出現在 log，也從沒被檢索到。
- 其中 4 筆（sem、stack、thrd_join、mcuboot），Analyzer 的關鍵字被 log 第一行那個**無關的 Kconfig 警告**帶偏了，例如 `MAX_THREAD_BYTES`、`USERSPACE`、`STACK_SENTINEL`、`STDOUT_CONSOLE`。這些警告在原始 build 裡也存在，但 log filter 把它們排在 assertion 前面。注入在測試檔的 sys_sem、sys_mutex 也有同樣的情況。
- 其他案例的關鍵字太籠統（`memory`、`queue`、`CPU`），或只有模組名稱，所以檢索沒能找到 `kernel/sem.c`、`lib/os/p4wq.c` 這類實作檔。

### B. compound：只修好一半（12 筆）

- DTS 錯誤最先出現（dtc 比 Kconfig 和編譯更早執行），而且 overlay 在 target_app 底下，模型看得到，所以先修 DTS。
- DTS 修好後，錯誤變成連結階段的 `undefined reference` 或找不到標頭檔。這類錯誤不會說明是哪個 CONFIG 把驅動關掉了。
- Kconfig 檔多半有被檢索到（retr），但模型沒有去改。

### C. kconfig：從沒改過 Kconfig 檔（7 筆）

- 症狀是連結失敗、`#error`、找不到標頭檔，或 `was assigned the value 'y' but got 'n'`。
- 模型改的是 `prj.conf` 或 `CMakeLists.txt`，全部被還原，5 輪都是同樣的錯誤。
- 其中 flash_map 那筆的警告已經寫出 `Kconfig:10`，模型還是只去 `prj.conf` 設值，沒有去看 `depends on` 為什麼不成立。

### D. 注入在測試檔，反覆違反規格（8 筆）

- 嚴格規格檢查擋下後，模型下一輪仍然在改測試的期望值或情境，例如 early_sleep 和 mslab 都是 5 輪全部 V。
- 原因是違規時給 agent 的訊息很籠統，而且不能說出是哪個檔案（說了就洩漏注入位置）。

### E. c_syntax：錯誤出現在標頭檔，沒找到注入檔（6 筆）

- 測試檔少了 `}` 或巨集寫錯時，gcc 會在之後 include 的 `ztest_test.h`、`util_internal.h`、`mm.h` 報錯。
- 原始 build log 其實有 ninja 的 `FAILED: CMakeFiles/app.dir/src/test_lifo_contexts.c.obj`，直接寫出出錯的編譯單元，就是被注入的檔案。但 **LogFilter 把這一行濾掉了**，模型只看到標頭檔裡的錯誤。

### F. 改到注入檔，但修法錯誤或沒修完（16 筆）

- 定位成功，但修正內容不對，屬於模型能力的問題。
- 例如 spsc_pbuf 改了別處；event_reset 和 pending_fifo 改了時序，但沒有改回原本的 API 或優先權。

## Proposed 可能的優化方向

估計受惠的案例數是依上面的分類粗算，**不代表一定修得好**。

| # | 優化 | 主要針對 | 估計涉及 | 屬於 |
|---|---|---|---|---|
| 1 | **Kconfig 相依解釋工具**（確定性，不用 LLM）：遇到連結錯誤、未宣告的 `DT_N_*`、找不到驅動標頭檔時，依序找出：缺少的符號 → 定義它的 .c 檔 → CMakeLists 裡控制這個檔案的 `CONFIG_X` → 定義 `CONFIG_X` 的 Kconfig 檔、它的 `depends on`/`select`，以及 build 目錄 `.config` 裡相依符號的實際值。最後把「CONFIG_X=n，因為 `depends on !DT_HAS_...` 不成立，定義在 drivers/.../Kconfig:5」這樣的說明交給 Analyzer 和 Patch Expert | C、B（kconfig、compound） | 約 19 筆 | Knowledge / DevOps Expert |
| 2 | **DTS 解釋工具**：遇到 `__device_dts_ord_..._ORD undeclared` 或 `DT_HAS_<compat>_ENABLED` 不成立時，從 build 目錄的 `zephyr.dts` 列出相關節點的 compatible 和 status，並指出沒有 compatible 的節點 | dts、compound 的 DTS 那一半 | 約 5 筆 | DevOps Expert |
| 3 | **保留出錯的編譯單元**：DevOps Expert 從原始 log 擷取 ninja 的 `FAILED: ...c.obj`，換成原始碼路徑後交給 Analyzer | E（c_syntax） | 6 筆 | DevOps Expert（LogFilter） |
| 4 | **從測試追到實作**：runtime 失敗時，從失敗的測試函式和 assertion 那一行，找出它呼叫的 API（例如 `k_sem_take`、`cobs_encode`），定位定義所在的檔案並加入檢索。另外，在 Analyzer 的 prompt 中說明，runtime crash 應以 assertion 或 crash 訊號為主，忽略無關的 Kconfig 警告 | A（runtime 核心程式） | 約 12～16 筆 | Analyzer / Knowledge |
| 5 | **規格檔被還原的提醒**（教授的建議，見另外的設計說明） | D、以及 B、C 裡反覆改規格檔的輪次 | 8 筆以上 | Supervisor（記憶） |
| 6 | **啟用 Graph RAG**：repo 裡有 `zephyr_graph_cache.full.pkl`，但每次都顯示「Graph cache not found」，代表知識圖譜從來沒有載入。這和第 1、2 項的需求重疊（Kconfig 和 DTS 的關係） | B、C | 未知 | Knowledge Expert |

**方法上的注意事項（重要）**
- 這些優化是看著這 139 筆的失敗結果設計出來的。如果改完後再用同一批案例評估，結果會偏樂觀，屬於拿測試集調整方法。
- 建議的做法：
  - 把優化後的版本視為 Proposed v2，明確寫成「依失敗分析改進」；
  - 用真實 bug 對照集，加上沒有參與失敗分析的模型（例如 3.8-flash）的結果來檢驗；
  - 或另外準備一批新案例當作 held-out set。
- 每一項優化都應該做 ablation，分開報告各自的效果。
