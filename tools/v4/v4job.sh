#!/bin/bash
# $1 = main|real, $2 = pipeline. 已有結果的案例會跳過，重跑即續跑。
stage=$1; name=$2; p=$name
[ "$name" = proposed_norag ] && p=proposed
cd ~/zephyr-llm-repair
[ -f ~/zephyr-eval-work/v4_main_STOP ] && exit 0
export ZEPHYR_GEMINI_MODEL=gemini-4-argon ZEPHYR_PROPOSED_OPT=0
[ "$p" = proposed ] && export ZEPHYR_PROPOSED_OPT=1
[ "$p" = b3 ] && export ZEPHYR_B3_GATE=1
[ "$name" = proposed_norag ] && export ZEPHYR_HYBRID_RAG=0
if [ "$stage" = real ]; then export DATASET=$PWD/dataset/cases/real_bugs_v3.json; out=$PWD/eval_runs/v4_main/real/$name
else export DATASET=$PWD/dataset/cases/final_dataset_v3.json; out=$PWD/eval_runs/v4_main/$name; fi
tools/run_with_key_rotation.sh $p $out ~/zephyr-eval-work/v4_main_${stage}_$name --single-model > ~/zephyr-eval-work/v4_main_${stage}_$name.out 2>&1
rc=$?
echo "$(date '+%F %T') $stage $name rc=$rc" >> ~/zephyr-eval-work/v4_main_jobs.log
[ -f ~/zephyr-eval-work/v4_main_STOP ] && exit 0
for i in 1 2 3 4 5 6; do
  git add -f "$out" && git commit -qm "Gemini 4 main run: $stage $name

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- "$out" && break
  perl -e 'select(undef,undef,undef,20)'
done
