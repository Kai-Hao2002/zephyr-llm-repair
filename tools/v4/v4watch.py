import glob, json, os, shutil, subprocess, time
os.chdir(os.path.expanduser("~/zephyr-llm-repair"))
STOP = os.path.expanduser("~/zephyr-eval-work/v4_main_STOP")
LIMIT, MIN_GB = 185.0, 6
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
    free = shutil.disk_usage(os.path.expanduser("~")).free / 2**30
    with open(os.path.expanduser("~/zephyr-eval-work/v4_main_watch.log"), "a") as w:
        w.write(f"{time.strftime('%F %T')} results={len(files)} cost=${cost:.2f} free={free:.1f}GB\n")
    if cost >= LIMIT or free < MIN_GB:
        open(STOP, "w").write(f"cost={cost:.2f} free={free:.1f}\n")
        subprocess.run("pkill -f xargs.*v4job; pkill -f v4job.sh; pkill -f run_with_key_rotation; pkill -f run_batch.sh; pkill -f evaluate.py; pkill -f openhands_runner", shell=True)
        time.sleep(3)
        subprocess.run("docker ps -q | xargs -r docker rm -f", shell=True)
        break
    if not subprocess.run(["pgrep", "-f", "v4job.sh"], capture_output=True).stdout:
        break
    time.sleep(600)
