#!/usr/bin/env bash
# 逐筆跑一條 pipeline 的全部案例：每筆單獨呼叫一次 evaluate.py，結果寫成
# <out_dir>/<case_id>.json，跑完立刻刪掉該筆 workspace (每筆約 475 MB，
# 151 筆一起留著會塞爆磁碟)。已經有結果檔的案例直接跳過，所以中斷後重跑
# 同一條指令就會續跑；要重跑某筆 (例如 transient_api 錯誤)，刪掉它的結果檔
# 再跑一次即可。遇到 daily_quota 錯誤就停下整批 (見 evaluate.py)。
#
# Runs every case of one pipeline one at a time: one evaluate.py call per
# case, results written to <out_dir>/<case_id>.json, and that case's
# workspace deleted right after (~475 MB each; keeping all 151 would fill
# the disk). Cases that already have a result file are skipped, so rerunning
# the same command resumes; to rerun a case (e.g. a transient_api error),
# delete its result file and run again. Stops on a daily_quota error (see
# evaluate.py).
#
# Usage: tools/run_batch.sh <pipeline> <out_dir> <work_dir> [extra evaluate.py args...]
set -u

pipeline="$1"; out_dir="$2"; work_dir="$3"; shift 3
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
dataset="${DATASET:-$repo_dir/dataset/cases/final_dataset.json}"
python="$repo_dir/venv/bin/python3"

mkdir -p "$out_dir" "$work_dir"
case_ids=$("$python" -c "import json,sys; print('\n'.join(c['id'] for c in json.load(open(sys.argv[1]))))" "$dataset")

cd "$repo_dir"
for case_id in $case_ids; do
    result="$out_dir/$case_id.json"
    [ -f "$result" ] && continue
    echo "=== $(date '+%F %T') [$pipeline] $case_id"
    runs_dir="$work_dir/$pipeline"
    "$python" evaluate.py --pipeline "$pipeline" --dataset "$dataset" --case-id "$case_id" \
        --runs-dir "$runs_dir" --results-out "$result.tmp" "$@" > "$out_dir/$case_id.log" 2>&1
    rm -rf "$runs_dir"
    if [ ! -f "$result.tmp" ]; then
        echo "!!! $case_id produced no result file (see $out_dir/$case_id.log); stopping"
        exit 1
    fi
    mv "$result.tmp" "$result"
    summary=$("$python" -c "import json,sys; r=json.load(open(sys.argv[1]))[0]; print(r['final_status'], r.get('error_kind') or '')" "$result")
    echo "    -> $summary"
    case "$summary" in *daily_quota*) echo "!!! daily quota exhausted; stopping"; rm -f "$result"; exit 2;; esac
done
echo "=== $(date '+%F %T') [$pipeline] all cases done"
