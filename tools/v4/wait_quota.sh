#!/bin/bash
# 每小時探測一次 API，額度恢復就結束 (exit 0)，最多試 96 次。
cd ~/zephyr-llm-repair
for i in $(seq 1 96); do
  date '+%F %T'
  venv/bin/python tools/canary_gemini.py gemini-4-argon > /tmp/v4_canary.$$ 2>&1
  rc=$?
  cut -c1-90 /tmp/v4_canary.$$; rm -f /tmp/v4_canary.$$
  if [ $rc -eq 0 ]; then echo CANARY_OK; exit 0; fi
  sleep 3600
done
echo GAVE_UP; exit 1
