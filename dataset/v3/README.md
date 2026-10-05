# v3 資料集工作目錄

最終資料集：`dataset/cases/final_dataset_v3.json`（139 筆，由 `dataset/scripts/assemble_v3.py` 產生）。
這個目錄放的是產生它的過程檔，全部可由腳本重現。

## 檔案

| 路徑 | 內容 | 產生者 |
|---|---|---|
| `commits.txt` | 24 個每週 base commit（2026-03-18..08-26，`<sha> <日期>`） | 手動 (`git log --first-parent --before`) |
| `smoke_commits.sh` | 各日期能否用現有 image 編譯的煙霧測試 | 手動 |
| `candidates/pilot_candidates*.json` | 試做的 10 筆與兩次改排 commit | 手動 |
| `candidates/migrated_candidates.json` | 從 v2 搬來的 71 筆候選；`migrated_batch*.json` 為分批輸入 | `build_v3_candidates.py` |
| `candidates/new_candidates*.json` | 40 個新 app 的候選（runtime 注入點由 `tools/probe_mutants.py` 探測） | 手動 + 探測 |
| `candidates/*_retry*.json` | 驗證失敗後的重試（換 commit / 板子 / target_test / operator），以 `retry_of` 指回原案例 | 手動 |
| `validation/*_validation*.json` | 只保留通過的驗證紀錄 | `tools/validate_v3_cases.py` |
| `validation/*_rejected_attempts.json` | 被拒的嘗試與原因（保留作紀錄） | 同上 |
| `gold/gold_check.json` | 標準答案 patch 走完整評測流程的結果（119/119 resolved） | `tools/gold_patch_check.py` |
| `gold/gold_check_first_attempt_failures.json` | 兩筆第一次因環境失敗、重跑通過的紀錄 | 同上 |
| `candidates/compound_replace.json`、`candidates/relabel_ltc2959.json` | 2026-10-05 稽核：取代 4 筆單一注入的 compound、ltc2959 重新驗證 | 手動 |
| `gold/compound_fix_gold.json` | 上述 5 筆的標準答案檢查 | `tools/gold_patch_check.py` |
| `candidates/real_bug_candidates.json`、`validation/real_bug_validation.json`、`gold/real_bug_gold.json`、`stability/real_bug_flaky_check.json` | 真實 bug 對照集（見下） | 手動挖掘 + 同上工具 |
| `eval/eval_pilot_cases.json` | 正式評測試跑用的 2 筆 | 手動 |

原始 build/run log 不在 repo 內：`~/zephyr-eval-work/v3_{pilot,migrate,new,fix,expand,qemucfg,compfix,real}/logs/`。
`assemble_v3.py` 會從這些 log 重新壓縮 `initial_error_log`。

## 驗證流程（每筆都通過）

1. `validate_v3_cases.py`：檔案存在、注入可套用、注入後重現預期失敗（runtime 類必須失敗在 target_test）、
   原始版本 target_test PASS；環境與評測一致（image 內模組快照、不跑 west update）。
2. `assemble_v3.py`：app 與注入檔不重複、每筆有 target_test、runtime 類初始 log 含失敗訊息。
3. `gold_patch_check.py`：把注入檔換回原始內容後，走 `prepare_broken_workspace` → 受保護檔案 →
   `evaluate_repair_attempt`，119/119 判為 resolved。

## 稽核與修正（2026-10-04）

| 問題 | 處理 | 結果 |
|---|---|---|
| 6 筆 `dts_break_phandle` 的 `_broken_ref` 字尾出現在 dtc 錯誤訊息 | 改成自然的拼字錯誤標籤（`dts_break_phandle:<label>=><typo>`） | 已修，log 中不再出現 |
| 5 筆 v2 自製測試名稱含 `offbyone`/`oob` | v3 專用副本 `scripts/injection_assets_v3/`，改成中性名稱（v2 原檔不動） | 已修 |
| Kconfig 部分全是 `depends on !X`；c_syntax 14/19 刪右大括號 | 新增 `kconfig_typo_depends`、`c_typo_identifier`、`c_remove_semicolon` 指定敘述句；改 16 筆 | Kconfig 部分 14/5/4，c_syntax 7/6/4/2 |
| 19 筆時序/排程類 runtime 案例可能不穩定 | `tools/flaky_check.py`：注入版跑 3 次、原始版再跑 2 次 | 19/19 穩定（`stability/flaky_check.json`） |

修正後的 27 筆以 `_fx` 結尾（`retry_of` 指回原案例，`fix_reason` 記錄原因），全部重新驗證並通過標準答案檢查。
最終 119 筆的標準答案檢查：119/119 resolved（`gold/gold_check.json`）。

## 2026-10-04 晚間補強

- 非時序類 44 筆 runtime 也做了穩定性檢查（注入版 3 次、原始版 1 次）：runtime 全部 63/63 穩定。
- 模組版本抽樣（6 筆，`west update` 對齊 case commit 的 manifest，2–41 個模組版本不同）：結果全部相同（`stability/module_check.json`）。
- compound 的 Kconfig 部分改 4 筆為 `kconfig_typo_depends`；補 kconfig adt7420、compound dma_emul。
- 依使用者要求把 dts、compound 各補到 18 筆（新 app：gpio_hogs、pwm_api、adc_rescale、flow_meter、hc-sr04、rtc/shell、led_api、ina228；
  regulator、gnss_api、eeprom/shell、can/shell、comparator/shell）。多出的 1 筆備用 dts 列在 `validation/excluded.json`。
- 最終 134 筆：runtime 63、c_syntax 19、compound 18、dts 18、kconfig 16；QEMU 27.6%；標準答案檢查 134/134。

## 2026-10-05 補強

- **測試完整性檢查**：30 筆 runtime 的注入點在測試程式本身（豁免還原），agent 可刪 assertion 或改 skip 來通過。
  `core/protected_files.check_test_integrity` 在評分時比對 assertion 與 skip/pass 數量，削弱即判 `test_integrity_violation`。
  v2 離線稽核：沒有任何修好的案例做過這種事。作弊測試（刪掉 early_sleep 的 4 個 assertion）原本整個套件會通過，現在被擋下；
  30 筆標準答案在新檢查下仍 30/30 修好。
- **QEMU 上的設定類案例**：新增 kconfig nvs/zms（qemu_x86）、mem_attr_heap（qemu_cortex_m3）、dts settings/retention、
  compound reset/mmio（qemu_cortex_m3）。coredump 類測試會刻意 crash、ext2/littlefs/flash_common 需要 tests.yaml 的額外設定，不適用。
- 最終 139 筆：runtime 63、c_syntax 19、kconfig 19、dts 19、compound 19；QEMU 30.2%；標準答案 139/139。

## 2026-10-05 稽核修正

| 問題 | 處理 |
|---|---|
| compound 中 4 筆只有單一 DTS 注入、執行期失敗（device_power_domains、devicetree/api pinctrl、power_states_api、uac2），與其他 15 筆 Kconfig+DTS 雙注入不同 | 列入 `validation/excluded.json`，換成 4 個新 app 的雙注入案例：akm09918c、bmi160、gpio_keys、gpio_kbd_matrix（sbs_gauge 因 tests.yaml 需要 `CONFIG_EMUL` 被拒） |
| ltc2959 的建置期 `static assertion failed` 被 oracle 的 `ASSERTION FAIL`（不分大小寫）判成 crash | `QemuOracle` 不再對編譯器診斷行（`file:line:col: error:`、internal compiler error）比對 crash 特徵；重新驗證後為 `eof_no_boot` |
| `v3_c_heap_kasan_brace` 的初始 log 過濾後仍有 91 KB（少一個右大括號引發 446 筆連鎖錯誤） | `LogFilter` 對完全相同的編譯錯誤行去重，相異錯誤超過 20 筆時保留前 15 筆與最後 5 筆（gcc 最後的 `expected ... at end of input` 指出真正位置）；現在 4.8 KB。另有 7 筆的 log 因去重變短，內容沒有遺失 |

另外，`LogFilter` 會濾掉 Docker Desktop CLI 在容器非 0 結束時附加的提示（`What's next:` / `Debug this container error with Gordon → docker ai ...`）；
重組後主資料集 6 筆、真實 bug 對照集 1 筆的初始 log 少了這兩行，其他欄位不變。

compound 現在 19 筆全部是 Kconfig + DTS 雙注入、全部在建置期失敗：
typo+remove_compatible 8、invert+remove_compatible 6、typo+break_phandle 3、invert+break_phandle 1、invert+corrupt_reg 1。

## 真實 bug 對照集（2026-10-05）

`dataset/cases/real_bugs_v3.json`，11 筆，由 `dataset/scripts/assemble_real_v3.py` 產生。用途是外部效度對照
（合成注入上的 pipeline 排名在真實 bug 上是否一致），**不併入主資料集**，主資料集維持 100% 合成注入。

- 來源：image 內 git 歷史 2026-03-17..08-30 中，同時修改原始碼（≤2 個 .c/.h、≤40 行）與測試的修正 commit
  共 34 個，篩掉 pytest/console harness、unit_testing、build-only、功能新增、近乎重複的 BT controller 修正後
  15 個做預檢，11 個重現（3 個修正前後結果相同，1 個板子不適用）。
- 案例構成（Defects4J 式）：`broken_commit` = 上游修正 commit F；注入 = `restore_blob`（`tools/mutate_inject.py`）
  把 F 修改的原始碼換回 F^ 的內容，測試維持 F 的版本（含回歸測試）；標準答案 = F；`upstream` 欄位記錄 F、F^、標題。
  category 一律為 `real_bug`。
- 驗證：`validate_v3_cases.py` 雙向驗證 11/11（失敗都在 target_test，即修正 commit 新增/修改的測試）；
  標準答案 11/11（`gold/real_bug_gold.json`）；穩定性 11/11（`stability/real_bug_flaky_check.json`）。
- 組成：native_sim 8、qemu_x86 3；assertion 9、Segmentation fault 1（video）、kernel panic 1（CAP）；
  初始 log 含被修檔案路徑 1/11。
- 限制：數量小，只能看趨勢；修正是公開的，模型可能看過（以 B1 結果輔助說明）。

## 仍存在的限制

- 30 筆 runtime 的錯誤在測試程式本身（時序、優先權、double free），修的是測試碼而非 RTOS 程式碼。
- 非 runtime 的 76 筆 target_test 全部是自動挑的（套件中第一個 PASS 的測試，防刪測試用，與錯誤不直接相關）。
- 初始 log 是否寫出注入檔完整路徑：runtime 22/63（其中 19 筆是注入在測試檔、assertion 行本身印出路徑）、
  c_syntax 11/19、kconfig 7/19、dts 4/19、compound 4/19，各類別難度不能直接比；分析時從資料重算，不要抄這裡。
- gcc 的 `did you mean '...'?` 提示出現在 14 筆（c_syntax 6、compound 4、kconfig 2、dts 2），不只 `c_typo_identifier`。
- QEMU 案例集中在 runtime（42 筆 QEMU 中 28 筆）；compound/dts/kconfig 幾乎都在 native_sim，板子效應無法與類別分開分析。
- runtime 的 operator 以 off-by-one 為主（23/63）。
- 少數 runtime 的初始訊號很弱（只有暫存器傾印或只有 `Segmentation fault`，看不到 assertion）。
- 設定類在 QEMU 上只有 6 筆（測試附帶的 QEMU overlay 很少）。
- 模組固定為 image 快照，時間範圍 2026-03-17 之後（ARM 2026-05-07 之後）；抽樣顯示不影響結果。
- 全部為人工注入，沒有真實的歷史 bug；8 筆依賴我們自己寫的測試檔。
