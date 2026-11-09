#!/bin/bash
# 等主目錄的真實 bug 批次與 B2/B3 pilot 跑完，commit pilot 結果，再在這個 worktree
# (2026-11-hybrid 定義) 用 Gemini 4 跑 B1/B2/B3 的 139 筆。
# Wait for the real-bug batch (main checkout) and the B2/B3 pilot to finish, commit
# the pilot, then run B1/B2/B3 on the 139 cases with Gemini 4 from this worktree.
# $@ = PIDs to wait for.
cd "$(dirname "$0")/../.."
for pid in "$@"; do while kill -0 "$pid" 2>/dev/null; do perl -e 'select(undef,undef,undef,60)'; done; done
echo "$(date '+%F %T') waited for $*; starting"
git add -f eval_runs/v4_main/pilot_b23 tools/v4/b23pilot.sh tools/v4/b23pilot_cases.json tools/v4/v4b123_after.sh \
    tools/v4/v4job.sh tools/v4/v4watch.py tools/v4/v4usage.py tools/v4/v4main.sh
git commit -qm "B2/B3 baseline definition 2026-11-hybrid: 3-case Gemini 4 pilot; run scripts use this worktree

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
exec "$PWD/tools/v4/v4main.sh" main b1 main b2 main b3
