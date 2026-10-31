# OpenHands 修改紀錄

OpenHands SDK 本身不修改。這裡記錄 baseline 的每次設定變動，論文引用時以這份為準。

2026-10-18 之前的映像與網路隔離建置步驟見 `tools/openhands_image/README.md`。

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
