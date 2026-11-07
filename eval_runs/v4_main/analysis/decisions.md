# v4_main 評分決定 / Grading decisions

## 2026-11-07：B1 結構化輸出解析失敗算「未修好」

- **案例**：`v3new_compound_gpio_kbd_matrix_kconfig_dts_577e42a`（B1，Gemini 4）
- **經過**：
  - 第一次執行：Gemini 回 200 OK，但 `with_structured_output` 的 `parsed` 是 None，`final_status=error`，`error='NoneType' object has no attribute 'filepath'`。
  - 個別重跑一次，結果相同，該次呼叫約 14 分鐘。
  - B1 要求模型輸出整份檔案，推測是輸出過長被截斷而無法解析。這是模型輸出失敗，不是暫時性 API 錯誤。
- **決定（使用者 2026-11-07）**：算「未修好」，等同其他 pipeline 的 FormatError。不再重跑，也不改 B1 程式。結果檔保持 `final_status=error`；分析時只有 `resolved` 算成功。B1 = 10/139。
- **另一筆**：`v3new_c_syntax_counter_capture_c79b1e8` 第一次也是同樣的解析錯誤，重跑後得到合法輸出，結果為 `failed_max_retries`。
- **原始紀錄**：兩筆第一次的結果保留在 `eval_runs/v4_main/_transient/b1/`（commit d577fdd）。

## 2026-11-07: B1 structured-output parse failure counts as not resolved

- **Case**: `v3new_compound_gpio_kbd_matrix_kconfig_dts_577e42a` (B1, Gemini 4)
- **What happened**:
  - The call returned 200 OK, but `parsed` was None, so `final_status=error`.
  - An individual rerun gave the same error; that call took about 14 minutes.
  - This is likely a truncated full-file output. It is a model output failure, not a transient API error.
- **Decision (user, 2026-11-07)**: counted as not resolved, like a FormatError in the other pipelines. No further rerun and no change to the B1 code. B1 = 10/139.
- **Other case**: `v3new_c_syntax_counter_capture_c79b1e8` had the same parse error on its first attempt. The rerun produced valid output and the result is `failed_max_retries`.
- **Raw records**: both first attempts are kept in `eval_runs/v4_main/_transient/b1/` (commit d577fdd).
