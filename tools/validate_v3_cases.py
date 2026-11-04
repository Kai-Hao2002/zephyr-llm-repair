# tools/validate_v3_cases.py
"""
v3 資料集自動驗證：對每筆候選案例 (schema 同 final_dataset.json，另可帶 v2_source)
依序檢查
  1. 檔案存在：broken_commit 上 target_app 目錄、每個注入檔、extra_files 的 host 來源檔
  2. target_test 有定義在 target_app 的原始碼裡 (沒給時第 5 步自動挑)
  3. 注入可套用 (mutate_inject.py 沒有 NO_MATCH)
  4. 能重現錯誤：注入後 build/run 的狀態符合類別預期；runtime 類另外檢查失敗發生在 target_test
  5. 原始版本 (不注入) build/run 成功，且 target_test 有 PASS
並記錄 initial_error_log (用評測時同一個 LogFilter 壓縮)。
環境與評測一致：不跑 west update，模組用 zephyr-sandbox image 內的快照；
建置指令與 core/workflow.build_devops_docker_cmd 相同 (west build -d /tmp/build -p always -t run)。

Automated v3 case validation. For each candidate case: files exist, target_test is
defined, the injection applies, the injected build/run reproduces the expected failure
(and, for runtime cases, at target_test), the unmodified build/run passes target_test.
Records initial_error_log with the evaluator's LogFilter. Same environment as evaluation:
no west update (image module snapshot), same west build command.

Usage: venv/bin/python tools/validate_v3_cases.py <candidates.json> <out.json> [--logs DIR] [--only ID ...]
Resumable: cases already present in <out.json> are skipped.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from evaluate import (MUTATE_SCRIPT_CONTAINER_PATH, MUTATE_SCRIPT_HOST_PATH,  # noqa: E402
                      _escape_operator, _normalize_injections, _resolve_extra_file_host_path)
from tools.fault_injector import EXPECTED_FAILURE_STATUSES  # noqa: E402
from tools.log_filter import LogFilter  # noqa: E402
from tools.qemu_oracle import QemuOracle  # noqa: E402

_ZTEST_RE = re.compile(r"\bZTEST(?:_USER|_F|_USER_F|_EXPECT_FAIL|_EXPECT_SKIP)?\s*\(\s*\w+\s*,\s*(\w+)")
WEST_UPDATE = False
_CRASH_MARKERS = re.compile(r"Assertion failed|FAIL - |FATAL ERROR|>>> ZEPHYR FATAL|Segmentation fault|Bus Fault|Hard Fault|panic", re.I)


def _extra_files(case):
    injs = _normalize_injections(case)
    return case.get("extra_files") or injs[0].get("extra_files") or {}


def _docker_script(case, mutate: bool) -> (str, str):
    """Returns (docker command string for pexpect, container name)."""
    name = f"v3val_{case['id'][:40]}_{'mut' if mutate else 'orig'}_{uuid.uuid4().hex[:6]}"
    mounts = f"-v {MUTATE_SCRIPT_HOST_PATH}:{MUTATE_SCRIPT_CONTAINER_PATH}:ro "
    steps = []
    # extra_files 是測試 app 的一部分 (例如新增的測試檔)，原始版本也要有；只有 mutation 是 bug
    # extra_files are part of the test app (e.g. an added test), so the original run needs them too
    for idx, (rel, host) in enumerate(_extra_files(case).items()):
        mounts += f"-v {_resolve_extra_file_host_path(host)}:/tmp/extra_files/{idx}:ro "
        steps.append(f"mkdir -p $(dirname /zephyrproject/zephyr/{rel}) && cp /tmp/extra_files/{idx} /zephyrproject/zephyr/{rel}")
    if mutate:
        for inj in _normalize_injections(case):
            steps.append(f"python3 {MUTATE_SCRIPT_CONTAINER_PATH} /zephyrproject/zephyr/{inj['target_file']} "
                         f"{_escape_operator(inj['operator'])}")
    # --west-update：先把模組對齊到該 commit 的 manifest (只用於驗證「模組快照不影響結果」，
    # 評測本身一律用 image 內的模組快照)。
    # --west-update: align modules with the commit's manifest first (only to check that the
    # image's module snapshot does not change outcomes; evaluation always uses the snapshot).
    west_update = "west update --narrow -o=--depth=1 > /tmp/west_update.log 2>&1 && " if WEST_UPDATE else ""
    script = (f"cd /zephyrproject/zephyr && git checkout -q {case['broken_commit']} && " + west_update
              + "".join(s + " && " for s in steps)
              + f"cd /zephyrproject/zephyr/{case['target_app']} && "
              f"west build -b {case['board']} -d /tmp/build -p always -t run .")
    cmd = f"docker run --rm -i --name {name} --cpus=2 --memory=2400m {mounts}zephyr-sandbox bash -c '{script}'"
    return cmd, name


def precheck(case) -> dict:
    """Cheap static checks in a throwaway container (no build)."""
    files = [i["target_file"] for i in _normalize_injections(case)]
    app = case["target_app"]
    script = (f"cd /zephyrproject/zephyr && git checkout -q {case['broken_commit']} && "
              f"echo APP:$(test -d {shlex.quote(app)} && echo ok || echo missing) && "
              + " && ".join(f"echo FILE:{f}:$(test -f {shlex.quote(f)} && echo ok || echo missing)" for f in files)
              + f" && echo ZTESTS_BEGIN && grep -rHoE 'ZTEST(_USER|_F|_USER_F|_EXPECT_FAIL|_EXPECT_SKIP)?\\s*\\(\\s*\\w+\\s*,\\s*\\w+' {shlex.quote(app)} || true")
    r = subprocess.run(["docker", "run", "--rm", "zephyr-sandbox", "bash", "-c", script],
                       capture_output=True, text=True, timeout=300)
    out = r.stdout
    res = {"app_exists": "APP:ok" in out,
           "files_exist": {f: f"FILE:{f}:ok" in out for f in files},
           "ztests": {}}
    for line in out.split("ZTESTS_BEGIN", 1)[-1].splitlines():
        if ":" not in line:
            continue
        path, decl = line.split(":", 1)
        m = _ZTEST_RE.search(decl)
        if m:
            res["ztests"].setdefault(path, []).append(m.group(1))
    missing_host = [h for h in _extra_files(case).values() if not os.path.exists(_resolve_extra_file_host_path(h))]
    res["extra_files_missing"] = missing_host
    if r.returncode != 0:
        res["error"] = r.stderr[-1500:]
    return res


def run(case, mutate, oracle, required=None):
    cmd, name = _docker_script(case, mutate)
    t0 = time.time()
    r = oracle.evaluate(cmd, container_name=name, wait_for_completion=True, required_pass_test=required)
    r["seconds"] = round(time.time() - t0)
    if "NO_MATCH:" in r.get("log", ""):
        r["status"] = "operator_no_match"
    return r


def _passed_tests(log):
    return re.findall(r"PASS - (\w+)", log)


def _failing_test(log):
    m = re.findall(r"FAIL - (\w+)", log)
    if m:
        return m[0]
    starts = re.findall(r"START - (\w+)", log)
    return starts[-1] if starts else None


def validate(case, oracle, logdir) -> dict:
    rec = {"id": case["id"], "checks": {}, "notes": []}
    chk = rec["checks"]
    pre = precheck(case)
    chk["app_exists"] = pre["app_exists"]
    chk["files_exist"] = all(pre["files_exist"].values()) and not pre["extra_files_missing"]
    if pre["extra_files_missing"]:
        rec["notes"].append(f"extra_files missing on host: {pre['extra_files_missing']}")
    all_tests = [t for ts in pre["ztests"].values() for t in ts]
    tt = case.get("target_test")
    chk["target_test_defined"] = (tt in all_tests) if tt else None
    if not (chk["app_exists"] and chk["files_exist"]):
        rec["accepted"] = False
        rec["precheck"] = pre
        return rec

    mut = run(case, True, oracle)
    open(os.path.join(logdir, f"{case['id']}.mutated.log"), "w").write(mut.get("log", ""))
    rec["mutated_status"], rec["mutated_seconds"] = mut["status"], mut["seconds"]
    chk["mutation_applied"] = mut["status"] != "operator_no_match"
    expected = EXPECTED_FAILURE_STATUSES.get(case["category"], {"eof_no_boot", "crash"})
    chk["reproduces_failure"] = mut["status"] in expected
    log = mut.get("log", "")
    if mut["status"] == "crash" and tt:
        rec["failing_test"] = _failing_test(log)
        chk["failure_at_target_test"] = rec["failing_test"] == tt
    rec["initial_error_log"] = LogFilter().compress_log(log)
    if case["category"] in ("runtime_crash",) or mut["status"] == "crash":
        chk["crash_signal_in_initial_log"] = bool(_CRASH_MARKERS.search(rec["initial_error_log"]))

    orig = run(case, False, oracle, required=tt)
    open(os.path.join(logdir, f"{case['id']}.original.log"), "w").write(orig.get("log", ""))
    rec["original_status"], rec["original_seconds"] = orig["status"], orig["seconds"]
    passed = _passed_tests(orig.get("log", ""))
    if not tt and orig["status"] == "success":
        # 自動挑 target_test：優先選定義在注入檔裡、而且原始版本有 PASS 的測試
        # Auto-pick target_test: prefer a test defined in an injected file that PASSes on the original.
        inj_files = {os.path.relpath(f) for f in (i["target_file"] for i in _normalize_injections(case))}
        in_inj = [t for p, ts in pre["ztests"].items() if os.path.relpath(p) in inj_files for t in ts if t in passed]
        tt = (in_inj or passed or [None])[0]
        rec["target_test_auto"] = tt
        rec["notes"].append(f"target_test auto-picked ({'from injected file' if in_inj else 'first PASS in suite'})")
        chk["target_test_defined"] = tt in all_tests if tt else False
    rec["target_test"] = tt
    if tt and not chk.get("target_test_defined") and tt in passed:
        # 用其他巨集定義的測試 (grep 抓不到) 只要原始版本有 PASS 就算有定義
        # Tests declared through other macros count as defined if they PASS on the original
        chk["target_test_defined"] = True
        rec["notes"].append("target_test found via PASS line (not matched by ZTEST grep)")
    chk["original_passes_target_test"] = orig["status"] == "success" and bool(tt) and tt in passed
    hard = ["app_exists", "files_exist", "target_test_defined", "mutation_applied", "reproduces_failure",
            "original_passes_target_test"]
    if "failure_at_target_test" in chk:
        hard.append("failure_at_target_test")
    rec["accepted"] = all(chk.get(k) for k in hard)
    if chk.get("failure_at_target_test") is False:
        rec["notes"].append(f"injected run fails at {rec.get('failing_test')}, not at target_test {tt}")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates")
    ap.add_argument("out")
    ap.add_argument("--logs", default=os.path.expanduser("~/zephyr-eval-work/v3_pilot/logs"))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--west-update", action="store_true",
                    help="align modules with the case commit's manifest before building (module-snapshot check)")
    args = ap.parse_args()
    global WEST_UPDATE
    WEST_UPDATE = args.west_update
    os.makedirs(args.logs, exist_ok=True)
    cases = json.load(open(args.candidates))
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    done = json.load(open(args.out)) if os.path.exists(args.out) else []
    done_ids = {r["id"] for r in done}
    oracle = QemuOracle(timeout=900)
    for case in cases:
        if case["id"] in done_ids:
            continue
        t0 = time.time()
        try:
            rec = validate(case, oracle, args.logs)
        except Exception as e:  # infra error: record, don't count as a dataset verdict
            rec = {"id": case["id"], "accepted": False, "infra_error": repr(e)}
        rec["wall_seconds"] = round(time.time() - t0)
        done.append(rec)
        json.dump(done, open(args.out, "w"), indent=1, ensure_ascii=False)
        print(f"{case['id']}: accepted={rec['accepted']} mut={rec.get('mutated_status')} "
              f"orig={rec.get('original_status')} tt={rec.get('target_test')} "
              f"checks={rec.get('checks')} notes={rec.get('notes')} ({rec['wall_seconds']}s)", flush=True)


if __name__ == "__main__":
    main()
