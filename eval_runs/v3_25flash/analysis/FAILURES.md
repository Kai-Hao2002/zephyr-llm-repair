# v3 失敗原因分析：依類別（gemini-2.5-flash，2026-10-12）

資料來源是 `failure_by_category.json`（`tools/failure_by_category.py`）和 `taxonomy.json`（`tools/failure_taxonomy.py`）。
這裡只看未修好的案例，用的是原始判定。

名詞：
- **規格檔**：target_app 底下、注入檔以外的檔案（測試程式、`prj.conf`、`CMakeLists.txt`、overlay）。agent 改了會在每次 build 前被還原。
- **錯誤沒變**：最後一輪留下的第一個錯誤，和初始錯誤相同（比對時去掉路徑和行號）。

## 先看跨類別的共同原因

1. **只改到規格檔，被還原後同樣的錯誤原封不動地回來，agent 不知道修改被還原了。**
   - 為了不洩漏哪個檔案是注入檔，評測端不告訴 agent 哪些檔案被還原。
   - 結果是 agent 只看到同樣的錯誤，常常反覆用同一招，或換另一個規格檔改。
   - 這是 B3 最主要的失敗原因（49/78），也是 Proposed 的第一大原因（17/51）。
   - 失敗案例中，最後錯誤和初始錯誤完全相同的：kconfig 有 B2 10/12、B3 12/14、Proposed 8/9；runtime 有 B3 23/35。
2. **錯誤訊息指向的地方，不是注入的地方。** 例如：
   - Kconfig 被關掉時，看到的是連結錯誤；
   - DTS 的 `compatible` 被刪掉時，看到的是 `__device_dts_ord_…` 未宣告；
   - 核心程式的 off-by-one，看到的是測試檔裡的 assertion。

   模型照著症狀去改看得到的檔案（多半是規格檔），沒有往回追到真正的原因。
3. **B3 沒有檢索。** 它只看得到 log 提到的檔案和 target_app 底下的檔案，注入在核心程式（kernel/lib/drivers/subsys）時根本看不到注入檔。
4. **Proposed 特有：StaticCheck（cppcheck）誤報。**
   - cppcheck 不認得 Zephyr 的巨集，例如 `DT_FOREACH_CHILD_STATUS_OKAY_SEP(..., (,))` 和 token pasting，常報 `syntaxError` 或 `unknownMacro`。
   - patch 因此一再被打回重寫，沒有機會 build。
   - 影響範圍：20 筆案例出現過 StaticCheck 失敗，其中 14 筆最後沒修好，7 筆有 3 輪以上卡在這裡，總共吃掉 44 輪。
5. **patch 格式錯誤**：B2 只有一次機會，格式錯了就直接失敗（B2 有 30 筆案例出現格式錯誤）。
   常見的形式是用 `--- 路徑` 這種 diff 格式開頭，或一個檔名底下寫多個區塊。

## c_syntax（19 筆；失敗：B2 10、B3 4、Proposed 7）

這是最容易的一類：19 筆中有 11 筆的初始 log 直接寫出注入檔路徑，gcc 也常附上 `did you mean`。

失敗原因：
- **錯誤出現在別的檔案（右大括號被刪的案例）。**
  - 測試檔少了一個 `}`，gcc 卻在之後 include 的 `ztest_test.h` 報 `initializer element is not constant` 或 `expected declaration ... at end of input`。
  - B2 和 Proposed 都去改 `ztest_test.h`，例如 ringbuffer 那筆。B2 那 6 筆「改錯地方」的失敗都屬於這種。
- **巨集拼錯（`c_typo_macro`，2 筆）。**
  - gcc 的錯誤是 `pasting ")" and "_STATUS_" does not give a valid preprocessing token`。
  - Proposed 這 2 筆都有改到正確的檔案，但修正版本被 cppcheck 誤報擋下（adc_api 有 4 輪、counter_capture 有 5 輪），從頭到尾沒有 build 過。
  - B3 沒有 StaticCheck，這 2 筆都修好了。這是 c_syntax 唯一一類 B3 贏 Proposed 的原因（15 對 12）。
- **初始 log 的第一行是無關的 Kconfig 警告**（4 筆，例如 heap_kasan、kernel_queue）。
  - log filter 把 `... was assigned the value` 這種警告放在真正的編譯錯誤前面。
  - 這 4 筆修好的數量是 B2 1、B3 3、Proposed 2，比其他 c_syntax 略低。
- B2 有 4 筆是單次輸出的格式錯誤。

## kconfig（19 筆；失敗：B2 12、B3 14、Proposed 9）

- **失敗案例幾乎從來沒碰到 Kconfig 檔**：B2 12/12、B3 14/14、Proposed 8/9。
  - 它們改的是 `prj.conf`（加 `CONFIG_X=y`）、`CMakeLists.txt`，甚至改測試程式，這些全部被還原。
  - 例如 bbram_emul 那筆：Proposed 5 輪都在改 `CMakeLists.txt`，連結錯誤 5 輪都沒變。
- **決定成敗的關鍵是 log 裡有沒有 Kconfig 警告。**
  - Kconfig 警告長這樣：`FLASH_MAP (defined at subsys/storage/flash_map/Kconfig:10) was assigned the value 'y' but got 'n'`，會寫出 Kconfig 檔名和行號。
  - 有這個警告的 8 筆：B2 修好 5、B3 4、Proposed 7。
  - 沒有警告的 11 筆，錯誤只是連結失敗、`#error`、找不到標頭檔：B2 只修好 2、B3 1、Proposed 3。
- 就算 log 寫出了 Kconfig 的位置，模型還是可能只去 `prj.conf` 把值設成 y。
  例如 flash_map：Proposed 5 輪都改 `prj.conf`，沒去看 `depends on` 為什麼不成立。
- 不同 operator 的修復率差異不大：`invert_depends` 3/3/3、`remove_select` 2/0/4、`typo_depends` 2/2/3（依序是 B2/B3/Proposed）。

## dts（19 筆；失敗：B2 8、B3 6、Proposed 4）

- `dts_break_phandle`（9 筆）三者都全修好：dtc 的錯誤直接寫出 overlay 的檔名和行號。
- **失敗幾乎都是 `dts_remove_compatible`**：B2 7、B3 6、Proposed 4（共 8 筆）。
  - 節點少了 `compatible` 之後，dtc 不會報錯。真正出錯的地方在後面，例如：
    - `__device_dts_ord_DT_N_S_flow_meter_..._ORD undeclared`；
    - 測試自己的 `#error "HC-SR04 not enabled"`；
    - `I2C_EMUL ... was assigned the value 'y' but got 'n'`。
  - 這些錯誤都不會提到 overlay。
- 模型改的是測試的 `main.c` 和 `prj.conf`，全部被還原。例如 B3 的 hc_sr04 第 2 輪其實有改到 overlay，但同時也改了 `main.c`，之後 3 輪都去改 `prj.conf`。

## compound（19 筆，Kconfig + DTS 同時注入；失敗：B2 18、B3 19、Proposed 14）

這是最難的一類：B3 0/19、B2 1/19、Proposed 5/19。

- **典型的失敗模式：只修好其中一半。** 失敗案例中改到哪一半：

  | | 兩半都沒改到 | 只改到 DTS | 只改到 Kconfig | 兩半都改到 |
  |---|---|---|---|---|
  | B2 | 9 | 8 | 1 | 0 |
  | B3 | 5 | 12 | 0 | 2 |
  | Proposed | 1 | 9 | 3 | 1 |

- **為什麼總是修 DTS 那一半：**
  1. dtc 比 Kconfig 和 C 編譯更早執行。DTS 錯誤（`dts_corrupt_reg`、`dts_break_phandle`）會最先出現，而且寫出 target_app 底下的 overlay，模型看得到。
  2. 修好 DTS 之後，Kconfig 的 `depends on` 被反轉，驅動程式沒被編進去，錯誤就變成連結階段的 `undefined reference`。
     例如 can_shell、bq40z50：錯誤不提 Kconfig，模型接著去改 `CMakeLists.txt` 或 `prj.conf`（被還原），或改 emulator 的原始碼。
  3. 如果 DTS 這一半是刪 `compatible`，連第一步都沒有明顯的訊號。
- B2 只有一輪，本來就不可能分兩階段修，B3 則看不到 Kconfig 檔。
  Proposed 能修好 5 筆，靠的是檢索找到 `drivers/.../Kconfig`；它有 3 筆失敗是只改到 Kconfig 那一半。
- 修好 compound 的 Proposed 案例中，有 1 筆是用 `default y if` 取代 `depends on`，屬於替代修法（見 REPORT 第 4 節）。

## runtime_crash（63 筆；失敗：B2 41、B3 35、Proposed 17）

依注入位置分成兩組，失敗的樣貌完全不同：

| | 注入在測試程式（30 筆） | 注入在核心程式（33 筆） |
|---|---|---|
| B2 失敗 | 12（8 筆改到了注入檔，但修法錯誤） | 29（13 筆 patch 沒套用成功，15 筆只改了測試、被還原） |
| B3 失敗 | 5（4 筆改到了注入檔，但修法錯誤） | 30（28 筆只改了測試、被還原） |
| Proposed 失敗 | 3 | 14（10 筆只改了測試、被還原） |

- **注入在測試程式**：失敗的不多，原始判定下也容易「修好」。但這一組正是改測試語意集中的地方（見 REPORT 第 4 節），用嚴格規格判定後三者都只剩 13～14 筆。
- **注入在核心程式**：初始訊號是測試裡的 assertion（例如 `ret not equal to -EINVAL`）、`Segmentation fault`，或只有暫存器傾印，真正的原因在驅動或 kernel 裡。
  - B3 沒有檢索，看不到這些檔案，只能去改測試，30 筆中有 28 筆是這樣失敗的。
  - Proposed 靠檢索和 Analyzer 的關鍵字，33 筆修好 19 筆。剩下 14 筆失敗中，10 筆也是只改了測試。
  - 還有一種情況是改到「同名的錯誤檔案」。例如 clock_getres 那筆改的是測試 app 裡的 `clock.c`，不是 `subsys/portability/posix/options/clock.c`。
- **operator 的難度**：`runtime_remove_null_check`（10 筆，多半在核心程式）B2 和 B3 都 0，Proposed 5；`runtime_off_by_one`（23 筆）B2 4、B3 3、Proposed 14。
- **Proposed 的 StaticCheck 誤報**也出現在 runtime：bmi160 那筆有 4 輪、flash_sim 那筆有 5 輪被 cppcheck 擋下。
- 有 14 筆 runtime 的初始 log 第一行是無關的 Kconfig 警告，但真正的 assertion 也在 log 裡，修復率和其他 runtime 案例差不多（B2 5、B3 8、Proposed 9）。

## B1（139 筆全部失敗）

B1 只看得到 log，要猜檔案並整檔重寫。失敗的分布如下：
- 46 筆路徑猜錯，檔案不存在；
- 42 筆只改到規格檔；
- 36 筆改對檔案，但整檔重寫後編譯失敗；
- 15 筆改到別的檔案。

就算改對了檔案，模型也不知道原始內容，重寫的檔案和原本差太多。

## 可以改進的方向（供討論；都會動到評測程式或 pipeline，需要另外決定）

1. **StaticCheck**：把 cppcheck 的 `syntaxError` 和 `unknownMacro` 降為警告，或改成只檢查 patch 新增的行。這會改變 Proposed 本身，屬於方法的變更，不能只套用在下一輪模型上。
2. **還原通知**：告訴 agent「你對規格檔的修改已被還原，請改其他地方」，但不列出檔名。這可以減少重複，但也會洩漏一點資訊（被還原的檔案就不是注入檔）。
3. **log filter**：讓真正的編譯錯誤排在 Kconfig 警告前面。這會改變資料集的 `initial_error_log`，必須重新組資料集，還要重跑。
4. 論文中可以直接用上面的分析，說明「錯誤訊號的位置」和「能不能看到根因檔案」是主要的難度來源，並作為各 pipeline 差異的解釋。
