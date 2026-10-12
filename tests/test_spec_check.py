"""Tests for the strict spec check (core/protected_files.spec_changes and its use in
check_test_integrity): an injected file under target_app may only change on the injected lines.
Run: python tests/test_spec_check.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.protected_files import check_test_integrity, register, spec_changes, unregister

ORIG = ("ZTEST(s, test_a)\n{\n\tk_thread_create(&t, s, 512, f, NULL, NULL, NULL,\n"
        "\t\tK_PRIO_PREEMPT(3), 0, K_NO_WAIT);\n\tk_sleep(K_MSEC(5));\n"
        "\tzassert_equal(x, 1, \"x wrong\");\n\tzassert_true(y);\n}\n")
INJ = ORIG.replace("k_sleep(K_MSEC(5))", "k_yield()")


def test_restore_or_untouched_is_fine():
    assert spec_changes(ORIG, INJ, ORIG, "a.c") == []
    assert spec_changes(ORIG, INJ, INJ, "a.c") == []


def test_different_fix_on_injected_line_is_fine():
    assert spec_changes(ORIG, INJ, ORIG.replace("K_MSEC(5)", "K_TICKS(50)"), "a.c") == []


def test_comment_and_whitespace_changes_are_fine():
    final = ORIG.replace("\tzassert_true(y);", "\t/* check y */\n\n\tzassert_true(y);  // y must hold")
    assert spec_changes(ORIG, INJ, final, "a.c") == []


def test_changed_expected_value_is_flagged():
    final = INJ.replace("zassert_equal(x, 1,", "zassert_equal(x, 2,")
    assert spec_changes(ORIG, INJ, final, "a.c")


def test_keeping_defect_and_changing_scenario_is_flagged():
    final = INJ.replace("K_PRIO_PREEMPT(3)", "K_PRIO_PREEMPT(9)")
    assert spec_changes(ORIG, INJ, final, "a.c")


def test_message_only_change_outside_injection_is_flagged():
    # strict by design: any non-comment edit outside the injected lines counts
    final = ORIG.replace("\"x wrong\"", "\"x is wrong\"")
    assert spec_changes(ORIG, INJ, final, "a.c")


def test_reinserted_closing_brace_is_fine():
    orig = "void f(void)\n{\n\tif (a) {\n\t\tb();\n\t}\n\tc();\n}\n"
    inj = orig.replace("\t\tb();\n\t}\n", "\t\tb();\n")
    assert spec_changes(orig, inj, orig, "a.c") == []


def test_swapped_priorities_both_lines_restored_is_fine():
    orig = "A(PRIO(1));\nx();\nB(PRIO(3));\nzassert_true(ok);\n"
    inj = "A(PRIO(3));\nx();\nB(PRIO(1));\nzassert_true(ok);\n"
    assert spec_changes(orig, inj, orig, "a.c") == []
    assert spec_changes(orig, inj, inj.replace("zassert_true(ok)", "zassert_false(ok)"), "a.c")


def test_conf_and_overlay():
    assert spec_changes("A=y\nB=y\n", "A=n\nB=y\n", "A=y\nB=y\n# note\n", "tests/app/prj.conf") == []
    assert spec_changes("A=y\nB=y\n", "A=n\nB=y\n", "A=y\nB=n\n", "tests/app/prj.conf")
    ov = "n: n {\n\tcompatible = \"x\";\n\tstatus = \"okay\";\n};\n"
    ov_inj = ov.replace("\tcompatible = \"x\";\n", "")
    assert spec_changes(ov, ov_inj, ov, "b.overlay") == []
    assert spec_changes(ov, ov_inj, ov.replace("okay", "disabled"), "b.overlay")


def test_check_test_integrity_uses_originals():
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, "tests/app/src"))
    path = os.path.join(ws, "tests/app/src/main.c")
    open(path, "w").write(INJ)
    register(ws, "tests/app", ["tests/app/src/main.c"], ws + ".snap", {"tests/app/src/main.c": ORIG})
    open(path, "w").write(ORIG)
    assert check_test_integrity(ws) == []
    open(path, "w").write(INJ.replace("K_PRIO_PREEMPT(3)", "K_PRIO_PREEMPT(9)"))
    problems = check_test_integrity(ws)
    assert problems and "spec lines changed" in problems[0]
    unregister(ws)


def test_injection_outside_target_app_is_not_spec_checked():
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, "tests/app"))
    os.makedirs(os.path.join(ws, "kernel"))
    open(os.path.join(ws, "kernel/k.c"), "w").write(INJ)
    register(ws, "tests/app", ["kernel/k.c"], ws + ".snap", {"kernel/k.c": ORIG})
    open(os.path.join(ws, "kernel/k.c"), "w").write(INJ.replace("PREEMPT(3)", "PREEMPT(9)"))
    assert check_test_integrity(ws) == []
    unregister(ws)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
