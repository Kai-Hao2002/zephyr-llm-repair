#!/usr/bin/env bash
# 每天的排程：先跑 Proposed 直到 embedding 每日配額用完 (run_batch.sh exit 2)，
# 接著跑 baseline 直到下一次配額重置 (太平洋時間午夜過 10 分鐘)，再回到
# Proposed。Proposed 全部跑完後，baseline 一路跑到結束。
# Daily schedule: run Proposed until the embedding daily quota runs out
# (run_batch.sh exit 2), then run the baseline until the next quota reset
# (10 minutes past Pacific midnight), then back to Proposed. Once Proposed is
# done, the baseline runs to completion.
#
# Usage: tools/run_daily_schedule.sh <baseline pipeline> <baseline out_dir> <work_dir> [extra evaluate.py args...]
# Env: PROPOSED_DATASET / BASELINE_DATASET override each batch's dataset file.
set -u
baseline="$1"; baseline_out="$2"; work_dir="$3"; shift 3
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_dir"

next_reset() {
    TZ=America/Los_Angeles date -j -v+1d -f "%Y-%m-%d %H:%M:%S" "$(TZ=America/Los_Angeles date +%Y-%m-%d) 00:10:00" +%s
}

proposed_done=0
baseline_done=0
while [ $proposed_done -eq 0 ] || [ $baseline_done -eq 0 ]; do
    if [ $proposed_done -eq 0 ]; then
        DATASET="${PROPOSED_DATASET:-$repo_dir/dataset/cases/final_dataset.json}" \
            tools/run_batch.sh proposed eval_runs/v2/proposed "$work_dir" "$@"
        code=$?
        case $code in
            0) proposed_done=1; echo "=== $(date '+%F %T') Proposed finished" ;;
            2) echo "=== $(date '+%F %T') Proposed stopped on the daily quota" ;;
            *) echo "=== $(date '+%F %T') Proposed batch failed with $code; stopping"; exit $code ;;
        esac
    fi
    deadline=$(next_reset)
    [ $proposed_done -eq 1 ] && deadline=""
    if [ $baseline_done -eq 0 ]; then
        DEADLINE="$deadline" DATASET="${BASELINE_DATASET:-$repo_dir/dataset/cases/final_dataset.json}" \
            tools/run_batch.sh "$baseline" "$baseline_out" "$work_dir" "$@"
        code=$?
        case $code in
            0) baseline_done=1; echo "=== $(date '+%F %T') $baseline finished" ;;
            3) ;;
            *) echo "=== $(date '+%F %T') $baseline batch failed with $code; stopping"; exit $code ;;
        esac
    fi
    # baseline 在配額重置前就跑完了：等到重置再回到 Proposed。
    # The baseline finished before the quota reset: wait for it, then back to Proposed.
    if [ $proposed_done -eq 0 ] && [ "$(date +%s)" -lt "$deadline" ]; then
        echo "=== $(date '+%F %T') waiting for the quota reset at $(date -r "$deadline" '+%F %T')"
        sleep $(( deadline - $(date +%s) ))
    fi
done
echo "=== $(date '+%F %T') schedule complete"
