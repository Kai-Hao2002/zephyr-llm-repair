#!/bin/bash
# 新 baseline 定義 (2026-11-hybrid) 的 B2/B3 試跑：kconfig、runtime、compound 各 1 筆，Gemini 4。
# 設定和 tools/v4/v4job.sh 相同 (B3 的 GATE=1 對應 Proposed 的 OPT=1)。結果放 eval_runs/v4_main/pilot_b23/。
# Pilot of B2/B3 under the 2026-11-hybrid definition: one kconfig, one runtime, one compound case, Gemini 4,
# same settings as v4job.sh (B3 GATE=1 matches Proposed OPT=1).
cd "$(dirname "$0")/../.." || exit 1
cases=tools/v4/b23pilot_cases.json
venv/bin/python3 - "$cases" <<'E'
import json, sys
ids = ["v3_kconfig_fat_filesystem_elm_depends_93db4e5_fx", "v3new_runtime_json_bcf31c2",
       "v3_compound_adc_emul_kconfig_dts_b30a5a9_fx"]
cases = {c["id"]: c for c in json.load(open("dataset/cases/final_dataset_v3.json"))}
json.dump([cases[i] for i in ids], open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
E
export DATASET=$PWD/$cases ZEPHYR_GEMINI_MODEL=gemini-4-argon ZEPHYR_PROPOSED_OPT=0
for p in b2 b3; do
  [ $p = b3 ] && export ZEPHYR_B3_GATE=1
  tools/run_with_key_rotation.sh $p $PWD/eval_runs/v4_main/pilot_b23/$p ~/zephyr-eval-work/v4_pilot_b23_$p --single-model
done
