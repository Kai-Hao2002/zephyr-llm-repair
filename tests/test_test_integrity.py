"""Tests for the injected-test-file integrity check in core/protected_files.py.
Run: python tests/test_test_integrity.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.protected_files import check_test_integrity, register, unregister

SRC = ("ZTEST(s, test_a)\n{\n\tk_sleep(K_MSEC(5));\n\tzassert_equal(x, 1);\n\tzassert_true(y);\n}\n")


def _setup():
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, "tests/app/src"))
    open(os.path.join(ws, "tests/app/src/main.c"), "w").write(SRC)
    open(os.path.join(ws, "tests/app/prj.conf"), "w").write("CONFIG_ZTEST=y\n")
    register(ws, "tests/app", ["tests/app/src/main.c"], ws + ".snap")
    return ws


def _write(ws, text):
    open(os.path.join(ws, "tests/app/src/main.c"), "w").write(text)


def test_genuine_fix_passes():
    ws = _setup()
    _write(ws, SRC.replace("k_sleep(K_MSEC(5))", "k_yield()"))
    assert check_test_integrity(ws) == []
    unregister(ws)


def test_removed_assertion_is_flagged():
    ws = _setup()
    _write(ws, SRC.replace("\tzassert_equal(x, 1);\n", ""))
    assert check_test_integrity(ws) and "2->1" in check_test_integrity(ws)[0]
    unregister(ws)


def test_added_skip_is_flagged():
    ws = _setup()
    _write(ws, SRC.replace("{\n", "{\n\tztest_test_skip();\n", 1))
    assert check_test_integrity(ws)
    unregister(ws)


def test_unregistered_or_non_test_injection_is_ignored():
    assert check_test_integrity(tempfile.mkdtemp()) == []
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, "tests/app"))
    os.makedirs(os.path.join(ws, "kernel"))
    open(os.path.join(ws, "kernel/sem.c"), "w").write("zassert_true(1);")
    register(ws, "tests/app", ["kernel/sem.c"], ws + ".snap")
    open(os.path.join(ws, "kernel/sem.c"), "w").write("")
    assert check_test_integrity(ws) == []  # bug outside the test app: not a test file
    unregister(ws)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
