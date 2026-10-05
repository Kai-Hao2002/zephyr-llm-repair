# v3 資料集工作目錄

最終資料集：`dataset/cases/final_dataset_v3.json`（119 筆，由 `dataset/scripts/assemble_v3.py` 產生）。
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
| `eval/eval_pilot_cases.json` | 正式評測試跑用的 2 筆 | 手動 |

原始 build/run log 不在 repo 內：`~/zephyr-eval-work/v3_{pilot,migrate,new}/logs/`。
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

## 仍存在的限制

- 30 筆 runtime 的錯誤在測試程式本身（時序、優先權、double free），修的是測試碼而非 RTOS 程式碼。
- 編譯期類別有 67 筆 target_test 是自動挑的（防刪測試用，與錯誤不直接相關）。
- 初始 log 是否寫出注入檔：c_syntax 11/19、kconfig 少數、dts/compound 少數、runtime 0/63，各類別難度不能直接比。
- `c_typo_identifier` 的錯誤會出現 gcc 的 `did you mean '...'?`（4 筆，簡單組）。
- 設定類在 QEMU 上只有 6 筆（測試附帶的 QEMU overlay 很少）。
- 模組固定為 image 快照，時間範圍 2026-03-17 之後（ARM 2026-05-07 之後）；抽樣顯示不影響結果。
- 全部為人工注入，沒有真實的歷史 bug；8 筆依賴我們自己寫的測試檔。
