#!/bin/bash
# 2.5 Flash 批次的磁碟保護，門檻與 v4watch.py 相同：可用空間低於 6 GB 就停批次。批次結束時自己結束。
while pgrep -f "[o]pt25.sh" > /dev/null; do
  free=$(df -g ~ | tail -1 | awk '{print $4}')
  echo "$(date '+%F %T') free=${free}GB"
  if [ "$free" -lt 6 ]; then
    echo "=== low disk; stopping batch"
    pkill -f "[o]pt25.sh"; pkill -f "run_v3_freetier"; pkill -f "run_with_key_rotation"; pkill -f "run_batch.sh"; pkill -f "evaluate.py --pipeline"
    sleep 3
    docker ps -q | xargs -r docker rm -f
    exit 3
  fi
  sleep 300
done
echo "$(date '+%F %T') batch ended"
