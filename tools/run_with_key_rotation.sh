#!/bin/bash
# tools/run_with_key_rotation.sh <pipeline> <out_dir> <work_dir> [evaluate.py args...]
# 依序用 .env 的 GEMINI_API_KEY、GEMINI_API_KEY2 跑 tools/run_batch.sh；某把碰到每日額度
# (run_batch.sh 結束碼 2，且已刪掉那筆未完成的結果) 就換下一把重跑。金鑰只放在環境變數，不印出。
# Runs tools/run_batch.sh with GEMINI_API_KEY, then GEMINI_API_KEY2 from .env; on a daily-quota
# stop (exit 2, unfinished result already removed) it switches to the next key. Keys are never printed.
set -u
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
for name in GEMINI_API_KEY GEMINI_API_KEY2; do
    key=$("$repo_dir/venv/bin/python3" -c "import sys; from dotenv import dotenv_values; print(dotenv_values(sys.argv[1]).get(sys.argv[2]) or '')" "$repo_dir/.env" "$name")
    [ -z "$key" ] && continue
    echo "=== $(date '+%F %T') using $name"
    GEMINI_API_KEY="$key" "$repo_dir/tools/run_batch.sh" "$@"
    rc=$?
    [ $rc -ne 2 ] && exit $rc
    echo "=== $(date '+%F %T') $name hit its daily quota"
done
echo "!!! all keys hit their daily quota"
exit 2
