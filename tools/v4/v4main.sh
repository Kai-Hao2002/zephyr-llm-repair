#!/bin/bash
S=$(dirname "$0")
rm -f ~/zephyr-eval-work/v4_main_STOP
# 先跑完 Proposed、OpenHands、Agentless 的 139 + 13 筆，B1-B3 才開始；無 RAG 最後。
{ for s in main real; do for p in proposed openhands agentless; do echo $s $p; done; done; for s in main real; do for p in b3 b2 b1; do echo $s $p; done; done; echo main proposed_norag; } | xargs -n 2 -P 3 $S/v4job.sh &
sleep 60
python3 $S/v4watch.py &
wait
