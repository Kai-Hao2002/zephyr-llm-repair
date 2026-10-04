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

## 已知限制與缺陷（2026-10-04 稽核）

見 `final_dataset_v3.json` 的稽核報告（對話紀錄 / 指導教授報告）。摘要：
- 6 筆 `dts_break_phandle` 把標籤改成 `<label>_broken_ref`，錯誤訊息直接露出這個字尾。
- 5 筆 v2 自製測試的名稱含 `offbyone` / `oob`，透露錯誤類型。
- kconfig 與 compound 的 Kconfig 部分全部是 `kconfig_invert_depends`（`depends on !X`），c_syntax 有 14/19 是刪右大括號。
- 19 筆 runtime 案例依賴排程/時序（`c_api_substitute`、`thread_priority_swap`），注入版本只重現過一次，未量測 flakiness。
- 模組固定為 image 快照（2026-08），早期 commit 搭配的不是當時的模組版本；只涵蓋 2026-03-17 之後（SDK 1.0 限制）。
