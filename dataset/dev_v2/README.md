# 開發集候選（2026-10-30）

來源：`dataset/cases/final_dataset.json` 扣掉 `final_dataset_v3.json` 的 `v2_source`，共 76 筆。
這裡只放驗證結果。**開發集的最終範圍還沒定，評測程式也還沒使用這些檔案。**

- `dev76_candidates.json`：76 筆候選。
- `dev_validation.json`：72 筆的驗證紀錄，環境與 v3 相同（`zephyr-sandbox` 映像內的模組快照）。
- `validate_dev_wrapper.py`：呼叫 `tools/validate_v3_cases.py`，只多一項處理：注入檔由 `extra_files` 提供時算作存在。
- `dev_clean_ids.json`：通過驗證，且排除與測試集相同的 bug 和有洩漏的案例後，剩下的 59 筆。

| 階段 | 筆數 | runtime | c_syntax | compound | kconfig | dts |
|---|---|---|---|---|---|---|
| 候選 | 76 | 29 | 19 | 14 | 10 | 4 |
| 驗證通過 | 68 | 27 | 16 | 14 | 8 | 3 |
| 排除 8 筆相同 bug、1 筆洩漏 | 59 | 23 | 16 | 13 | 6 | 1 |

未通過驗證的 8 筆：
- `samples/hello_world` 4 筆，沒有 ztest，沒做驗證（其中 2 筆抽樣時原始版本逾時）；
- 2 筆注入後的失敗不在 target_test；
- 2 筆 kconfig 的原始版本就沒有成功。

與測試集仍有重疊（59 筆中）：同檔案且同 operator 家族 10 筆、同注入檔 17 筆、同 target_app 42 筆。
33 筆的 target_test 是驗證時自動挑的，資料集欄位尚未更新。沒有做標準答案檢查和穩定性檢查。
原始 log：`~/zephyr-eval-work/v3_dev/logs/`。
