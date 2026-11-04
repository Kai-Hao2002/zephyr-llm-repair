#!/bin/bash
# 優化版 Proposed + 2.5 Flash：先跑分層抽樣的一半 (每類各一半，固定種子)，和 r3 無圖檢索版比較。
# 結果和 opt25.sh 同目錄，之後跑 opt25.sh 會跳過這些案例。每日額度用完就每小時重試。
cd ~/zephyr-llm-repair || exit 1
half=tools/v4/opt25_half_cases.json
venv/bin/python3 - "$half" <<'E'
import json, math, random, sys
cases = json.load(open("dataset/cases/final_dataset_v3.json"))
by_cat = {}
for c in cases:
    by_cat.setdefault(c["category"], []).append(c)
rng, picked = random.Random(20261101), []
for cat in sorted(by_cat):
    rng.shuffle(by_cat[cat])
    picked += by_cat[cat][:math.ceil(len(by_cat[cat]) / 2)]
rng.shuffle(picked)
json.dump(picked, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
E
export DATASET=$PWD/$half FREE_TIER=1 ZEPHYR_GEMINI_MODEL=gemini-2.5-flash ZEPHYR_PROPOSED_OPT=1
export GEMINI_EMBEDDING_API_KEY=$(venv/bin/python3 -c "from dotenv import dotenv_values; print(dotenv_values('.env').get('GEMINI_EMBEDDING_API_KEY') or '')")
while :; do
  tools/run_with_key_rotation.sh proposed $PWD/eval_runs/v4_25flash_opt/proposed ~/zephyr-eval-work/v4_25flash_opt_half --single-model
  rc=$?
  [ $rc -ne 2 ] && break
  sleep 3600
done
echo "=== $(date '+%F %T') opt25 half finished rc=$rc"
exit $rc
