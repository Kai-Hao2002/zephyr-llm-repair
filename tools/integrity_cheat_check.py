# tools/integrity_cheat_check.py
"""
測試完整性檢查的負向驗證：保留注入的 bug，只把被注入測試檔裡 target_test 函式內的 assertion 全部刪掉
(模擬「改測試讓它過」的投機修補)，走完整評測路徑，預期判定為 test_integrity_violation 而不是修好。
Negative check for test integrity: keep the injected bug, delete every assertion inside the
target_test function of the injected test file (a test-gaming 'fix'), run the full evaluation
path, and expect test_integrity_violation instead of resolved.
Usage: venv/bin/python tools/integrity_cheat_check.py <dataset.json> <id> [<id> ...]
"""
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.protected_files import register, unregister  # noqa: E402
from core.workflow import evaluate_repair_attempt  # noqa: E402
from evaluate import _normalize_injections, prepare_broken_workspace  # noqa: E402

ASSERT_LINE = re.compile(r"^[ \t]*(?:zassert|zexpect)_\w+\s*\([^;]*;[ \t]*\n", re.M)

cases = {c["id"]: c for c in json.load(open(sys.argv[1]))}
work = os.path.expanduser("~/zephyr-eval-work/v3_cheat")
for cid in sys.argv[2:]:
    c = cases[cid]
    dest = os.path.join(work, cid)
    ws = prepare_broken_workspace(c, dest)
    injs = _normalize_injections(c)
    register(ws, c["target_app"], [i["target_file"] for i in injs], dest + ".protected")
    try:
        tf = injs[0]["target_file"]
        text = open(os.path.join(ws, tf)).read()
        m = re.search(r"ZTEST\w*\(\s*\w+\s*,\s*" + re.escape(c["target_test"]) + r"\s*\)\s*\{", text)
        start = m.end(); depth = 1; i = start
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0); i += 1
        body = text[start:i]
        cheated = ASSERT_LINE.sub("", body)
        open(os.path.join(ws, tf), "w").write(text[:start] + cheated + text[i:])
        r = evaluate_repair_attempt(ws, c["board"], c["target_app"], c["target_test"])
        print(f"{cid}: removed {len(ASSERT_LINE.findall(body))} assertions -> status={r['status']} resolved={r['resolved']}")
        print("   agent-visible log:", r["log"][:200])
    finally:
        unregister(ws)
        shutil.rmtree(dest, ignore_errors=True); shutil.rmtree(dest + ".protected", ignore_errors=True)
