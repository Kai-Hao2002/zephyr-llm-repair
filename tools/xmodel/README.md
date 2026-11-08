# 跨模型實驗 / Cross-model runs (2026-11-08)

助教提供兩把金鑰 (.env：`Claude_API_KEY`、`Bedrock_API_KEY`)。The TA provided two keys.

| model | provider (`--model-provider`) | model id | endpoint |
|---|---|---|---|
| claude | anthropic | claude-sonnet-5-5 | api.anthropic.com |
| qwen | qwen | qwen.qwen3-235b-a22b-2507 | https://llm-proxy.seai.cit.tum.de (OpenAI 相容，需 TUM 網路/VPN) |

- 試跑 / pilot（2 筆：c_syntax mbox_usage、kconfig mem_attr_heap，Gemini 4 四條 pipeline 都修好）：
  `tools/xmodel/xjob.sh claude pilot b1`（依序換 agentless、proposed、openhands）。試跑結果不自動 commit。
- 正式 / main：`tools/xmodel/xjob.sh claude main agentless` 等，順序 agentless → proposed → openhands → b1。
  背景執行要用 `nohup ... & disown` + `caffeinate -i -w <pid>`。停止：`touch ~/zephyr-eval-work/x_STOP`。
- 和 Gemini 4 主實驗的差異 (無法避免，論文要註明) / Unavoidable deviations from the Gemini 4 run:
  - Claude 不送 temperature：claude-sonnet-5-5 拒絕非預設值 (anthropic SDK 1.x 也沒有這個參數)。所以 Agentless 的
    候選 patch (原本第 1 個 0、其餘 0.8) 都用模型預設溫度；Analyzer 的 0.1、其他呼叫的 0 也一樣。
    Claude sends no temperature (claude-sonnet-5-5 rejects non-default values).
  - Claude 的 thinking 用模型預設 (adaptive，effort high)，思考 token 算在 output。Thinking is the model default.
  - Claude 結構化輸出 (B1、Analyzer) 用 json_schema (原生 structured output)，因為強制 tool_choice 會回 400。
  - Claude 單次輸出上限 65,536 (= gemini-4-argon 上限)；呼叫用 streaming。Qwen 未設 `ZEPHYR_QWEN_MAX_TOKENS` 時用代理預設。
  - Qwen 結構化輸出用 function calling。
  - Hybrid RAG 的 embedding 仍是 Gemini (gemini-embedding-2)。
- 額度用完 (Anthropic `credit balance is too low`、LiteLLM `Budget has been exceeded`) 視同 daily_quota：整批停、不留結果。
