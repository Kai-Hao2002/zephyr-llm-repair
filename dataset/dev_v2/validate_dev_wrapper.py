"""Dev-set validation wrapper (2026-10-30). Runs tools/validate_v3_cases.py unchanged, except that an
injected file delivered by extra_files counts as existing (it is not in the upstream tree)."""
import sys
sys.path.insert(0, "/Users/yangkaihao/zephyr-llm-repair")
import tools.validate_v3_cases as V
_orig = V.precheck
def precheck(case):
    r = _orig(case)
    for f in r["files_exist"]:
        if f in V._extra_files(case):
            r["files_exist"][f] = True
    return r
V.precheck = precheck
V.main()
