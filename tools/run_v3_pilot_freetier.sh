#!/usr/bin/env bash
# v3 評測試跑 (dataset/v3/eval/eval_pilot_cases.json，2 筆 × B1/B2/B3/Proposed)，給免費層金鑰用：
#   1. 等到 START_AT (epoch 秒，預設立刻)；
#   2. 用 tools/canary_gemini.py 的「結束碼」確認模型可用 (不是 grep 的結束碼)，失敗就隔 30 分鐘再試，
#      每天最多 3 次 (每次最多打兩把金鑰各 1 次，503 也算額度)，仍失敗就等下一次額度重置 (太平洋時間午夜過 5 分鐘)；
#   3. FREE_TIER=1 依序跑四條 pipeline (tools/run_with_key_rotation.sh，--single-model)；
#      碰到每日額度 (exit 2) 就等下一次重置再接著跑；
#   4. 每輪結束把 transient_api / llm_hang 的結果移到 archive (不當成結果)，下一輪單筆重跑；
#   5. 8 筆都有正式結果時寫出 eval_runs/v3/pilot/PILOT_DONE 並結束。
# v3 eval pilot for free-tier keys: canary-gated (by canary_gemini.py's exit code), FREE_TIER=1,
# resumes after each daily quota reset, archives transient results for single-case reruns.
#
# Usage: nohup tools/run_v3_pilot_freetier.sh > ~/zephyr-eval-work/v3_eval_pilot.log 2>&1 &
# Env: START_AT (epoch seconds) to delay the first attempt.
set -u
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_dir"
python="$repo_dir/venv/bin/python3"
pilot_dir="$repo_dir/eval_runs/v3/pilot"
work_dir="$HOME/zephyr-eval-work/v3_eval_work"
export DATASET="$repo_dir/dataset/v3/eval/eval_pilot_cases.json"
export FREE_TIER=1
pipelines="b1 b2 b3 proposed"

log() { echo "=== $(date '+%F %T') $*"; }

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

# 還缺正式結果的案例數。Number of pipeline/case pairs still missing a real result.
missing() {
    "$python" - "$DATASET" "$pilot_dir" $pipelines <<'EOF'
import json, os, sys
ids = [c["id"] for c in json.load(open(sys.argv[1]))]
print(sum(not os.path.exists(os.path.join(sys.argv[2], p, f"{i}.json")) for p in sys.argv[3:] for i in ids))
EOF
}

# transient_api / llm_hang 的結果移到 archive，之後重跑。Move transient results aside for a rerun.
archive_transient() {
    "$python" - "$pilot_dir" $pipelines <<'EOF'
import glob, json, os, shutil, sys, time
root = sys.argv[1]
dest = os.path.join(os.path.dirname(root), "archive", "pilot_transient_" + time.strftime("%Y%m%d"))
for p in sys.argv[2:]:
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

[ -n "${START_AT:-}" ] && sleep_until "$START_AT"

while :; do
    if [ "$(missing)" -eq 0 ]; then
        log "all pilot results present"; date '+%F %T' > "$pilot_dir/PILOT_DONE"; exit 0
    fi

    ok=0
    for try in 1 2 3; do
        "$python" tools/canary_gemini.py gemini-3.8-flash
        rc=$?
        if [ $rc -eq 0 ]; then ok=1; break; fi
        log "canary failed (exit $rc), try $try/3"
        [ $try -lt 3 ] && sleep 1800
    done
    if [ $ok -eq 0 ]; then sleep_until "$(next_reset)"; continue; fi

    quota_hit=0
    for p in $pipelines; do
        log "pipeline $p"
        tools/run_with_key_rotation.sh "$p" "$pilot_dir/$p" "$work_dir" --single-model
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
