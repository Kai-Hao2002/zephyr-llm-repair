#!/bin/bash
# OpenHands 時間戳修正後的試跑：舊設定最貴的 3 筆，結果放 eval_runs/v4_main/pilot_ts/openhands。
cd ~/zephyr-llm-repair || exit 1
cases=tools/v4/ohpilot_cases.json
venv/bin/python3 - "$cases" <<'E'
import json, sys
ids = ["v3_api_substitute_sys_mutex_test_mutex_134e694", "v3_api_substitute_mutex_recursive_b30a5a9",
       "v3_api_substitute_modem_ubx_thread_yield_9be6ee7"]
cases = {c["id"]: c for c in json.load(open("dataset/cases/final_dataset_v3.json"))}
json.dump([cases[i] for i in ids], open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
E
export DATASET=$PWD/$cases ZEPHYR_GEMINI_MODEL=gemini-4-argon ZEPHYR_PROPOSED_OPT=0
tools/run_with_key_rotation.sh openhands $PWD/eval_runs/v4_main/pilot_ts/openhands ~/zephyr-eval-work/v4_pilot_ts --single-model
