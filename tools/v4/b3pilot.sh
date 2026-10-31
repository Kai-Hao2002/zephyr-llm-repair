#!/bin/bash
cd ~/zephyr-llm-repair
export DATASET=$PWD/dataset/dev_v2/dev_pilot_cases.json ZEPHYR_GEMINI_MODEL=gemini-4-argon ZEPHYR_B3_GATE=1
tools/run_with_key_rotation.sh b3 $PWD/eval_runs/v4_main/pilot/b3_gate ~/zephyr-eval-work/v4_main_pilot_b3 --single-model
echo "RC_b3=$?"
