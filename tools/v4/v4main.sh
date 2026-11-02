#!/bin/bash
S=$(dirname "$0")
rm -f ~/zephyr-eval-work/v4_main_STOP
# 先跑完 Proposed、OpenHands、Agentless 的 139 + 13 筆，B1-B3 才開始；無 RAG 最後。
# 參數有給時只跑指定的工作，例如 v4main.sh main proposed main openhands main agentless
if [ $# -gt 0 ]; then job_list() { printf '%s %s\n' "$@"; }; else
job_list() { for s in main real; do for p in proposed openhands agentless; do echo $s $p; done; done; for s in main real; do for p in b3 b2 b1; do echo $s $p; done; done; echo main proposed_norag; }; fi
job_list "$@" | xargs -n 2 -P 3 $S/v4job.sh &
sleep 60
python3 $S/v4watch.py &
wait
