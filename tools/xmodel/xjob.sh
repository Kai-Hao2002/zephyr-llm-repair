#!/bin/bash
# 跨模型實驗 (2026-11-08)：$1 = claude|qwen，$2 = main|real|rep1|rep2|pilot，$3 = pipeline。
# 設定和 tools/v4/v4job.sh 相同 (Proposed OPT=1、B3 GATE=1、--single-model、900 秒逾時)，只換生成模型；
# Hybrid RAG 的 embedding 仍用 .env 的 GEMINI_API_KEY (tools/run_with_key_rotation.sh 帶入)。
# 已有結果的案例會跳過，重跑即續跑。結果在 eval_runs/x_<model>/<stage>/<pipeline> (main 沒有 stage 層)。
# 預付額度/預算用完 (credit balance is too low / Budget has been exceeded) 會被當成 daily_quota：整批停下、該筆不留結果。
# Cross-model runs (2026-11-08): same settings as tools/v4/v4job.sh, only the generation model changes;
# embeddings still use GEMINI_API_KEY. Finished cases are skipped. Exhausted credit stops the batch.
model=$1; stage=$2; name=$3; p=$name
cd "$(dirname "$0")/../.." || exit 1
[ -f ~/zephyr-eval-work/x_STOP ] && exit 0
case $model in
  claude) prov=anthropic ;;
  qwen) prov=qwen ;;
  *) echo "unknown model $model"; exit 1 ;;
esac
export ZEPHYR_PROPOSED_OPT=0
[ "$p" = proposed ] && export ZEPHYR_PROPOSED_OPT=1
[ "$p" = b3 ] && export ZEPHYR_B3_GATE=1
base=$PWD/eval_runs/x_$model
case $stage in
  main) export DATASET=$PWD/dataset/cases/final_dataset_v3.json; out=$base/$name ;;
  real) export DATASET=$PWD/dataset/cases/real_bugs_v3.json; out=$base/real/$name ;;
  rep1|rep2) export DATASET=$PWD/dataset/v3/eval/repeat_subset_40.json; out=$base/$stage/$name ;;
  pilot) export DATASET=$PWD/tools/xmodel/pilot_cases.json; out=$base/pilot/$name ;;
  *) echo "unknown stage $stage"; exit 1 ;;
esac
log=~/zephyr-eval-work/x_${model}_${stage}_$name.out
tools/run_with_key_rotation.sh $p $out ~/zephyr-eval-work/x_${model}_${stage}_$name \
    --model-provider $prov --single-model > $log 2>&1
rc=$?
echo "$(date '+%F %T') $model $stage $name rc=$rc" >> ~/zephyr-eval-work/x_jobs.log
[ -f ~/zephyr-eval-work/x_STOP ] && exit 0
[ "$stage" = pilot ] && exit $rc   # 試跑結果先看過再 commit / pilots are reviewed before committing
for i in 1 2 3 4 5 6; do
  git add -f "$out" && git commit -qm "Cross-model run ($model): $stage $name

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- "$out" && break
  perl -e 'select(undef,undef,undef,20)'
done
exit $rc
