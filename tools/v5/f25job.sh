#!/bin/bash
# 以 Gemini 4 主實驗的規則重跑 gemini-2.5-flash (2026-11-08)：$1 = pilot|main|real，$2 = pipeline。
# 規則與 tools/v4/v4job.sh 相同 (新 LogFilter、900 秒、Graph RAG 關、B2/B3 = 2026-11-hybrid、B3 GATE=1、
# OpenHands 統一時間戳 workspace)；執行方式與 2.5 Flash 先前的結果相同 (tools/run_v3_freetier.sh，FREE_TIER=1，
# 碰到每日額度就等重置、暫時性錯誤移到 archive 後單筆重跑)。結果在 eval_runs/v5_25flash/，不覆蓋舊結果。
# 已有結果的案例會跳過，重跑即續跑。停止：touch ~/zephyr-eval-work/v5_STOP。
# Rerun gemini-2.5-flash under the Gemini 4 main-run rules (same settings as tools/v4/v4job.sh), launched the
# way earlier 2.5 Flash runs were (run_v3_freetier.sh, FREE_TIER=1). Results go to eval_runs/v5_25flash/.
stage=$1; p=$2
cd "$(dirname "$0")/../.." || exit 1
[ -f ~/zephyr-eval-work/v5_STOP ] && exit 0
export ZEPHYR_PROPOSED_OPT=0 ZEPHYR_B3_GATE=0
[ "$p" = proposed ] && export ZEPHYR_PROPOSED_OPT=1
[ "$p" = b3 ] && export ZEPHYR_B3_GATE=1
# load_dotenv 不覆蓋既有變數；.env 有 GEMINI_EMBEDDING_API_KEY 時 embedding 用它 / embedding key from .env if set
ek=$(venv/bin/python3 -c "from dotenv import dotenv_values; print(dotenv_values('.env').get('GEMINI_EMBEDDING_API_KEY') or '')")
[ -n "$ek" ] && export GEMINI_EMBEDDING_API_KEY=$ek
out=eval_runs/v5_25flash
log=~/zephyr-eval-work/v5_25flash_${stage}_$p.out
MODEL=gemini-2.5-flash OUT_ROOT=$out STAGE=$stage PIPELINES=$p tools/run_v3_freetier.sh > $log 2>&1
rc=$?
echo "$(date '+%F %T') 2.5flash $stage $p rc=$rc" >> ~/zephyr-eval-work/v5_jobs.log
[ -f ~/zephyr-eval-work/v5_STOP ] && exit 0
[ "$stage" = pilot ] && exit $rc   # 試跑結果先看過再 commit / pilots are reviewed before committing
for i in 1 2 3 4 5 6; do
  git add -f "$out" && git commit -qm "2.5 Flash rerun under Gemini 4 rules: $stage $p

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- "$out" && break
  perl -e 'select(undef,undef,undef,20)'
done
exit $rc
