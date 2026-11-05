import glob, json, os, shutil, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v4usage
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
STOP = os.path.expanduser("~/zephyr-eval-work/v4_main_STOP")
LIMIT, MIN_GB = float("inf"), 6  # 使用者 11/3 取消費用自動暫停；磁碟與 spending cap 仍會停
CAP_TEXT = "spending cap"
START = time.time() - 300

def cap_hit():
    # 只看這次開跑後寫入的結果，舊的錯誤已封存在 _transient
    for f in glob.glob("eval_runs/v4_main/**/*", recursive=True):
        if "/pilot/" in f or "/_transient/" in f or not f.endswith((".json", ".log")):
            continue
        try:
            if os.path.getmtime(f) >= START and CAP_TEXT in open(f, errors="ignore").read():
                return f
        except OSError:
            continue
    return None

while True:
    cost = 0.0
    files = [f for f in glob.glob("eval_runs/v4_main/**/*.json", recursive=True) if "/pilot/" not in f and "/_transient/" not in f]
    for f in files:
        try:
            r = json.load(open(f))[0]
        except Exception:
            continue
        for i in r.get("iteration_log") or []:
            for u in i.get("token_usage") or []:
                cost += u.get("input_tokens", 0) / 1e6 * 2 + u.get("output_tokens", 0) / 1e6 * 10
    try:
        v4usage.write()
    except Exception as e:
        print("usage report failed:", e)
    free = shutil.disk_usage(os.path.expanduser("~")).free / 2**30
    with open(os.path.expanduser("~/zephyr-eval-work/v4_main_watch.log"), "a") as w:
        w.write(f"{time.strftime('%F %T')} results={len(files)} cost=${cost:.2f} free={free:.1f}GB\n")
    cap = cap_hit()
    if cost >= LIMIT or free < MIN_GB or cap:
        open(STOP, "w").write(f"cost={cost:.2f} free={free:.1f} cap={cap}\n")
        subprocess.run("pkill -f xargs.*v4job; pkill -f v4job.sh; pkill -f run_with_key_rotation; pkill -f run_batch.sh; pkill -f evaluate.py; pkill -f openhands_runner", shell=True)
        time.sleep(3)
        subprocess.run("docker ps -q | xargs -r docker rm -f", shell=True)
        break
    if not subprocess.run(["pgrep", "-f", "v4job.sh"], capture_output=True).stdout:
        break
    time.sleep(60)
