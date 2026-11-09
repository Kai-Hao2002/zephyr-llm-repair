# OpenHands 修改紀錄

OpenHands SDK 本身不修改。這裡記錄 baseline 的每次設定變動，論文引用時以這份為準。

2026-10-18 之前的映像與網路隔離建置步驟見 `tools/openhands_image/README.md`。

## 2026-11-04：統一 workspace 時間戳，記錄快取 token 數

- **Commit：** 見本檔同一筆提交（`git log -- docs/openhands_changes.md`）。
- **原因：** 使用者要求（Gemini 4 主實驗 OpenHands 太貴，查原因時發現）。
  Gemini 4 跑的前 15 筆裡，最貴的 3 筆佔 82% 輸入 token，agent 在這 3 筆都花了大量步數用
  `find -newermt`、`find -printf %T@`、`stat` 找最近改過的檔案。
  準備 workspace 時，checkout、注入和刪除 `.orig` 備份會讓被注入的檔案和它的目錄時間戳比其他檔案新，
  `docker cp` 會保留這些時間戳；例如 `sys_mutex/src` 目錄的時間戳比其他目錄新約 100 秒。
  這是只有 OpenHands 能用的線索：Proposed、Agentless、B1 到 B3 沒有 shell，看不到時間戳。
- **改動：**
  - `core/openhands_pipeline.py`：交給 agent 之前，把 workspace 所有檔案和目錄的 atime、mtime
    設成同一個固定時間（2026-08-30 00:00 UTC）。ctime 無法設定，但會依走訪順序全部重設，和注入位置無關。
    改檔偵測仍用 mtime 加大小，agent 改過的檔案照樣抓得到（`tests/test_openhands_pipeline.py` 有測）。
  - `tools/openhands_runner.py`：多記 `cache_read_tokens`、`cache_write_tokens`、`reasoning_tokens`，
    從 SDK 的 `accumulated_token_usage` 讀。快取命中的 token 已含在 `input_tokens` 內，用來估實際費用。
- **沒有變動：** 任務描述、工具、步數 50、時間 40 分鐘、LLM 逾時 900 秒、SDK 1.55.1、映像
  `zephyr-oh-agent:1.55.1-z2`、網路白名單、API key 處理都沒有變動。沒有開對話壓縮。
- **影響的結果：** 2026-11-03 跑的 `eval_runs/v4_main/openhands/` 15 筆用的是舊 workspace，要用新設定重跑。
  2.5 Flash r3 的 OpenHands 結果也是舊 workspace，論文引用時要註明。
- **試跑：** 用舊設定最貴的 3 筆先試跑，結果在 `eval_runs/v4_main/pilot_ts/openhands/`。

## 2026-10-31：LLM 逾時 300 秒改為 900 秒

- **Commit：** 見本檔同一筆提交（`git log -- docs/openhands_changes.md`）。
- **原因：** 不是助教回饋的項目，是換用 Gemini 4（助教回饋）後發現的問題。
  `gemini-4-argon` 有些請求超過 300 秒，SDK 把逾時值放進 `X-Server-Timeout`，伺服器到期回 504，重試也一樣。
  開發集 Base 的 `inject_c_uac2_desc_typo_macro` 在 300 秒下連續兩輪失敗，900 秒下一次成功。
- **改動範圍：** 所有 pipeline 共用 `core/llm_provider.PATCH_LLM_TIMEOUT = 900`，讓比較條件一致。
- **OpenHands 相關檔案：**
  - `core/openhands_pipeline.py`：呼叫 runner 時加 `--llm-timeout 900`。
  - `tools/openhands_runner.py` 沒有改，預設值仍是 300，但會被上面的參數覆蓋。
- **前後差異：** LiteLLM 的單次呼叫逾時由 300 秒變為 900 秒。任務描述全文沒有變動。
- **SDK 版本：** `openhands-sdk`、`openhands-tools`、`openhands-workspace`、`openhands-agent-server` 皆為 1.55.1，沒有變動。
- **Docker 映像：** `zephyr-oh-agent:1.55.1-z2`，image ID
  `sha256:d1a817e0a33e6ac2d66ac43fa34d9c326f6e246f6843939eaa17091037dfdf10`，沒有變動。Dockerfile 沒有變動。
- **新增工具或指令：** 無。
- **網路白名單：** 沒有變動，只放行目前 LLM API 的 host。
- **API key 處理：** 沒有變動。
- **預算：** 沒有變動，50 步、40 分鐘。單次呼叫逾時變長，但仍受 40 分鐘上限約束。
- **結果目錄：** `eval_runs/v4_main/openhands/`、`eval_runs/v4_main/real/openhands/`。
- **run_meta：** 新增 `patch_llm_timeout`。`openhands` 欄位維持映像、image ID、步數、時間上限。
- **試跑：** `eval_runs/v4_main/pilot/openhands/` 是 300 秒時跑的（commit 419f09c 之前的設定），2 筆都修好，
  過程沒有逾時。

## 2026-11-08：跨模型 (Claude、Qwen3) 的 runner 參數

- **為什麼：** 助教提供 claude-sonnet-5-5 與 qwen.qwen3-235b-a22b-2507 的金鑰，OpenHands 要能用這兩個模型跑。
- **OpenHands 相關檔案：**
  - `tools/openhands_runner.py`：新增 `--temperature` (預設 `0.0`，`none` = 不送) 與 `--base-url` (預設不設)；
    兩個值記進輸出的 `temperature`、`base_url`。
  - `core/openhands_pipeline.py`：Claude 傳 `--temperature none` (claude-sonnet-5-5 拒絕非預設溫度)；Qwen 用 LiteLLM
    字串 `openai/qwen.qwen3-235b-a22b-2507` 加 `--base-url https://llm-proxy.seai.cit.tum.de`，網路白名單放行
    `llm-proxy.seai.cit.tum.de:443`。金鑰改由 `core.llm_provider.get_api_key()` 取得 (Claude 可用 `Claude_API_KEY`，
    Qwen 可用 `Bedrock_API_KEY`)。
- **前後差異：** Gemini 的呼叫完全不變 (仍送 temperature 0、不設 base_url)。任務描述全文沒有變動。
- **SDK 版本、Docker 映像、Dockerfile、工具、步數與時間上限：** 都沒有變動。
- **網路白名單：** 仍只放行目前供應商的 LLM API host (新增 Qwen 代理的 host)。
- **結果目錄：** `eval_runs/x_claude/`、`eval_runs/x_qwen/`。
- **試跑：** 尚未完成 (Claude 金鑰回 credit balance too low；Qwen 代理在目前網路連不上)。

## 2026-11-08: runner arguments for the cross-model runs (Claude, Qwen3)

- `tools/openhands_runner.py` gains `--temperature` (default `0.0`; `none` sends no temperature) and `--base-url`.
- `core/openhands_pipeline.py` passes `--temperature none` for Claude and the proxy base URL plus the
  `openai/` LiteLLM prefix for Qwen; the egress allowlist adds `llm-proxy.seai.cit.tum.de:443`.
- Gemini calls are unchanged. SDK versions, image, tools, step and time limits are unchanged.
