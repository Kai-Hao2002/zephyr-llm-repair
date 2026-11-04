"""Tests for StaticCheck's cppcheck filtering (tools/static_checker.py): ignored parse-failure ids,
only findings new relative to the pre-patch file, and no baseline when the pre-patch file did not
parse. cppcheck itself is mocked. Run: python tests/test_static_checker.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.static_checker import StaticChecker


def F(file, fid, msg, line="1"):
    return {"file": file, "line": line, "severity": "error", "id": fid, "message": msg}


def _checker(results_by_content):
    """Mock: findings depend on the current content of a.c in the workspace."""
    chk = StaticChecker()

    def fake(ws, files):
        return list(results_by_content.get(open(os.path.join(ws, "a.c")).read(), []))
    chk._cppcheck_findings = fake
    return chk


def _ws(content):
    ws = tempfile.mkdtemp()
    open(os.path.join(ws, "a.c"), "w").write(content)
    return ws


def test_parse_failures_are_ignored():
    chk = _checker({"post": [F("a.c", "syntaxError", "syntax error"), F("a.c", "unknownMacro", "macro X")]})
    assert chk._run_cppcheck(_ws("post"), ["a.c"], {})["passed"]


def test_preexisting_finding_is_not_blamed_on_patch():
    oob = F("a.c", "arrayIndexOutOfBounds", "Array 'x[1]' accessed at index 1", line="10")
    chk = _checker({"pre": [oob], "post": [dict(oob, line="12")]})
    ws = _ws("post")
    assert chk._run_cppcheck(ws, ["a.c"], {"a.c": "pre"})["passed"]
    assert open(os.path.join(ws, "a.c")).read() == "post"  # patched content restored


def test_new_finding_fails_and_is_reported():
    old = F("a.c", "nullPointer", "Possible null pointer dereference: p")
    new = F("a.c", "arrayIndexOutOfBounds", "Array 'a[2]' accessed at index 5", line="7")
    chk = _checker({"pre": [old], "post": [old, new]})
    r = chk._run_cppcheck(_ws("post"), ["a.c"], {"a.c": "pre"})
    assert not r["passed"] and "a.c:7" in r["log"] and "nullPointer" not in r["log"]


def test_duplicate_finding_counts_as_new():
    old = F("a.c", "nullPointer", "Possible null pointer dereference: p")
    chk = _checker({"pre": [old], "post": [old, dict(old, line="9")]})
    assert not chk._run_cppcheck(_ws("post"), ["a.c"], {"a.c": "pre"})["passed"]


def test_no_baseline_when_pre_patch_did_not_parse():
    chk = _checker({"pre": [F("a.c", "syntaxError", "syntax error")],
                    "post": [F("a.c", "nullPointerArithmetic", "Pointer addition with NULL pointer.")]})
    assert chk._run_cppcheck(_ws("post"), ["a.c"], {"a.c": "pre"})["passed"]


def test_without_pre_contents_real_findings_still_fail():
    chk = _checker({"post": [F("a.c", "nullPointer", "Possible null pointer dereference: p")]})
    assert not chk._run_cppcheck(_ws("post"), ["a.c"], {})["passed"]


def test_timeout_passes():
    chk = StaticChecker()
    chk._cppcheck_findings = lambda ws, files: None
    assert chk._run_cppcheck(_ws("post"), ["a.c"], {"a.c": "pre"})["passed"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
