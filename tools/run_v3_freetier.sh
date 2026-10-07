#!/usr/bin/env bash
# v3 評測啟動腳本 (免費層金鑰用)，模型與輸出目錄可指定：
#   1. 等到 START_AT (epoch 秒，預設立刻)；
#   2. 檢查輸出目錄裡既有結果的 run_meta.models.pro 都等於 MODEL，否則中止 (不同模型的結果不混放)；
#   3. 用 tools/canary_gemini.py 探測同一個 MODEL (看結束碼)，失敗就隔 30 分鐘再試，每天最多 3 次，
#      仍失敗就等下一次額度重置 (太平洋時間午夜過 5 分鐘)；
#   4. FREE_TIER=1、ZEPHYR_GEMINI_MODEL=MODEL 依序跑 PIPELINES (tools/run_with_key_rotation.sh，--single-model)；
#      碰到每日額度 (exit 2) 就等下一次重置再接著跑；
#   5. 每輪結束把 transient_api / llm_hang 的結果移到 archive (不當成結果)，下一輪單筆重跑；
#   6. 全部都有正式結果時寫出 <out>/<STAGE>_DONE 並結束。
# v3 eval launcher for free-tier keys with a selectable model and output root: refuses to mix models in
# one output dir, canary-gated on the same model, FREE_TIER=1, resumes after daily quota resets,
# archives transient results for single-case reruns.
#
# Env:
#   MODEL     (required) e.g. gemini-2.5-flash
#   OUT_ROOT  (required) e.g. eval_runs/v3_25flash
#   STAGE     pilot | main | real   (default main)
#             pilot -> dataset/v3/eval/eval_pilot_cases.json, results OUT_ROOT/pilot/<p>/
#             main  -> dataset/cases/final_dataset_v3.json,   results OUT_ROOT/<p>/
#             real  -> dataset/cases/real_bugs_v3.json,       results OUT_ROOT/real/<p>/
#   PIPELINES (default "b1 b2 b3 proposed")
#   START_AT  (epoch seconds) delay the first attempt
#
# Usage: MODEL=gemini-2.5-flash OUT_ROOT=eval_runs/v3_25flash STAGE=main PIPELINES="b1 b2 b3" \
#          nohup caffeinate -i tools/run_v3_freetier.sh >> ~/zephyr-eval-work/v3_25flash_main.log 2>&1 &
set -u
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_dir"
python="$repo_dir/venv/bin/python3"
: "${MODEL:?set MODEL}"; : "${OUT_ROOT:?set OUT_ROOT}"
STAGE="${STAGE:-main}"
pipelines="${PIPELINES:-b1 b2 b3 proposed}"
case "$OUT_ROOT" in /*) ;; *) OUT_ROOT="$repo_dir/$OUT_ROOT" ;; esac
case "$STAGE" in
    pilot) export DATASET="$repo_dir/dataset/v3/eval/eval_pilot_cases.json"; res_root="$OUT_ROOT/pilot" ;;
    main)  export DATASET="$repo_dir/dataset/cases/final_dataset_v3.json"; res_root="$OUT_ROOT" ;;
    real)  export DATASET="$repo_dir/dataset/cases/real_bugs_v3.json"; res_root="$OUT_ROOT/real" ;;
    *) echo "unknown STAGE $STAGE"; exit 64 ;;
esac
work_dir="$HOME/zephyr-eval-work/v3_eval_work_$(basename "$OUT_ROOT")_$STAGE"
export FREE_TIER=1
export ZEPHYR_GEMINI_MODEL="$MODEL"

log() { echo "=== $(date '+%F %T') [$MODEL $STAGE] $*"; }

next_reset() {
    # 下一個太平洋時間 00:05 (台灣 15:05)。Next 00:05 Pacific time.
    local today now t
    today=$(TZ=America/Los_Angeles date +%Y-%m-%d)
    t=$(TZ=America/Los_Angeles date -j -f "%Y-%m-%d %H:%M:%S" "$today 00:05:00" +%s)
    now=$(date +%s)
    [ "$t" -le "$now" ] && t=$(TZ=America/Los_Angeles date -j -v+1d -f "%Y-%m-%d %H:%M:%S" "$today 00:05:00" +%s)
    echo "$t"
}

sleep_until() {
    local t="$1"
    log "sleeping until $(date -r "$t" '+%F %T')"
    while [ "$(date +%s)" -lt "$t" ]; do sleep 60; done
}

# 既有結果的模型必須等於 MODEL。Existing results must come from MODEL.
check_model() {
    "$python" - "$res_root" "$MODEL" $pipelines <<'EOF'
import glob, json, os, sys
root, model = sys.argv[1], sys.argv[2]
bad = []
for p in sys.argv[3:]:
    for f in glob.glob(os.path.join(root, p, "*.json")):
        got = json.load(open(f))[0].get("run_meta", {}).get("models", {}).get("pro")
        if got != model:
            bad.append(f"{p}/{os.path.basename(f)}: {got}")
if bad:
    print("results from another model in this output dir:\n  " + "\n  ".join(bad))
    sys.exit(1)
EOF
}

# 還缺正式結果的案例數。Number of pipeline/case pairs still missing a real result.
missing() {
    "$python" - "$DATASET" "$res_root" $pipelines <<'EOF'
import json, os, sys
ids = [c["id"] for c in json.load(open(sys.argv[1]))]
print(sum(not os.path.exists(os.path.join(sys.argv[2], p, f"{i}.json")) for p in sys.argv[3:] for i in ids))
EOF
}

# transient_api / llm_hang 的結果移到 archive，之後重跑。Move transient results aside for a rerun.
archive_transient() {
    "$python" - "$res_root" "$OUT_ROOT" "$STAGE" $pipelines <<'EOF'
import glob, json, os, shutil, sys, time
root, out_root, stage = sys.argv[1:4]
dest = os.path.join(out_root, "archive", f"{stage}_transient_" + time.strftime("%Y%m%d"))
for p in sys.argv[4:]:
    for f in glob.glob(os.path.join(root, p, "*.json")):
        r = json.load(open(f))[0]
        if r.get("final_status") == "error" and r.get("error_kind") in ("transient_api", "llm_hang"):
            os.makedirs(os.path.join(dest, p), exist_ok=True)
            for src in (f, f[:-5] + ".log"):
                if os.path.exists(src):
                    shutil.move(src, os.path.join(dest, p, os.path.basename(src)))
            print(f"archived {p}/{os.path.basename(f)} ({r.get('error_kind')}) -> {dest}")
EOF
}

check_model || exit 65
[ -n "${START_AT:-}" ] && sleep_until "$START_AT"

while :; do
    if [ "$(missing)" -eq 0 ]; then
        log "all results present ($pipelines)"; date '+%F %T' > "$res_root/${STAGE}_DONE"; exit 0
    fi

    ok=0
    for try in 1 2 3; do
        "$python" tools/canary_gemini.py "$MODEL"
        rc=$?
        if [ $rc -eq 0 ]; then ok=1; break; fi
        log "canary failed (exit $rc), try $try/3"
        [ $try -lt 3 ] && sleep 1800
    done
    if [ $ok -eq 0 ]; then sleep_until "$(next_reset)"; continue; fi

    quota_hit=0
    for p in $pipelines; do
        log "pipeline $p"
        tools/run_with_key_rotation.sh "$p" "$res_root/$p" "$work_dir" --single-model
        rc=$?
        archive_transient
        case $rc in
            0) ;;
            2) log "daily quota exhausted during $p"; quota_hit=1; break ;;
            *) log "pipeline $p stopped with exit $rc; aborting"; exit $rc ;;
        esac
    done
    if [ $quota_hit -eq 1 ]; then sleep_until "$(next_reset)"; fi
    # 沒碰到額度但有 transient 被移走：迴圈回到 canary 再試。
    # No quota stop but transients were archived: loop back to the canary and retry.
done
