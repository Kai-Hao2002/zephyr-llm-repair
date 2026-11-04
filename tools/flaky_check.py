# tools/flaky_check.py
"""
穩定性檢查：對指定案例重複跑注入版本 N 次、原始版本 M 次 (同 tools/validate_v3_cases.py 的環境)，
確認注入版本每次都在 target_test 失敗、原始版本每次 target_test 都 PASS。
時序/排程類的注入 (k_sleep->k_yield、優先權互換) 若只是偶爾觸發，什麼都沒修也可能「剛好通過」。

Stability check: rerun the injected version N times and the original M times (same environment as
tools/validate_v3_cases.py); every injected run must fail at target_test and every original run must
PASS target_test. Timing/scheduling injections that only sometimes trigger could be "fixed" by luck.

Usage: venv/bin/python tools/flaky_check.py <dataset.json> <out.json> [--ids ID ...] [--timing]
       [--mutated 3] [--original 2]   (resumable)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.validate_v3_cases import _failing_test, _passed_tests, run  # noqa: E402
from tools.qemu_oracle import QemuOracle  # noqa: E402

TIMING_OPS = ("c_api_substitute", "thread_priority_swap")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset"); ap.add_argument("out")
    ap.add_argument("--ids", nargs="*"); ap.add_argument("--timing", action="store_true")
    ap.add_argument("--mutated", type=int, default=3); ap.add_argument("--original", type=int, default=2)
    a = ap.parse_args()
    cases = json.load(open(a.dataset))
    if a.ids:
        cases = [c for c in cases if c["id"] in a.ids]
    if a.timing:
        cases = [c for c in cases if any(i["operator"].split(":")[0] in TIMING_OPS
                                         for i in (c.get("injections") or [c["injection"]]))]
    done = json.load(open(a.out)) if os.path.exists(a.out) else []
    oracle = QemuOracle(timeout=900)
    for c in cases:
        if c["id"] in {d["id"] for d in done}:
            continue
        tt = c["target_test"]
        mut = []
        for _ in range(a.mutated):
            r = run(c, True, oracle)
            mut.append({"status": r["status"], "failing_test": _failing_test(r.get("log", ""))})
        orig = []
        for _ in range(a.original):
            r = run(c, False, oracle, required=tt)
            orig.append({"status": r["status"], "tt_pass": tt in _passed_tests(r.get("log", ""))})
        stable = (all(m["status"] == "crash" and m["failing_test"] == tt for m in mut)
                  and all(o["status"] == "success" and o["tt_pass"] for o in orig))
        done.append({"id": c["id"], "stable": stable, "mutated": mut, "original": orig})
        json.dump(done, open(a.out, "w"), indent=1)
        print(f"{c['id']}: stable={stable} mut={[m['status'] + '@' + str(m['failing_test']) for m in mut]} "
              f"orig={[o['status'] for o in orig]}", flush=True)
    print(f"FLAKY_DONE {sum(d['stable'] for d in done)}/{len(done)} stable")


if __name__ == "__main__":
    main()
