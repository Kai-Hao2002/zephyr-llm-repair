#!/bin/bash
# 優化版 Proposed + gemini-2.5-flash，139 + 13 筆，與 r3 比較用。沒有圖檢索，FREE_TIER=1 和 r3 相同。
# 額度用完由 run_v3_freetier.sh 等重置；已有結果的案例會跳過。
cd ~/zephyr-llm-repair || exit 1
export ZEPHYR_PROPOSED_OPT=1
# load_dotenv 不覆蓋既有變數，之後改 .env 也不會換掉 embedding 金鑰
export GEMINI_EMBEDDING_API_KEY=$(venv/bin/python3 -c "from dotenv import dotenv_values; print(dotenv_values('.env').get('GEMINI_EMBEDDING_API_KEY') or '')")
for stage in main real; do
  MODEL=gemini-2.5-flash OUT_ROOT=eval_runs/v4_25flash_opt STAGE=$stage PIPELINES=proposed tools/run_v3_freetier.sh
  rc=$?
  if [ $rc -ne 0 ]; then echo "=== $(date '+%F %T') $stage exited $rc; stopping"; exit $rc; fi
  git add -f eval_runs/v4_25flash_opt
  git commit -qm "Optimized Proposed on gemini-2.5-flash: $stage

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- eval_runs/v4_25flash_opt
done
echo "=== $(date '+%F %T') opt25 finished"
