#!/bin/bash
S=$(dirname "$0")
rm -f ~/zephyr-eval-work/v4_main_STOP
{ for p in proposed openhands agentless b3 b2 b1; do echo main $p; done; for p in proposed openhands agentless b3 b2 b1; do echo real $p; done; echo main proposed_norag; } | xargs -n 2 -P 3 $S/v4job.sh &
sleep 60
python3 $S/v4watch.py &
wait
