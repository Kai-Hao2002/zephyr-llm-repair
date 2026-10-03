"""Tests for agents.patch_expert.collect_relevant_context_paths: log-cited files reach
the Patch context in absolute and relative (Kconfig 'defined at', WEST_TOPDIR, ZEPHYR_BASE)
forms. Run: python -m pytest tests/test_context_paths.py (or python tests/test_context_paths.py)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.patch_expert import collect_relevant_context_paths

FILES = ["subsys/fs/fcb/Kconfig", "kernel/sem.c", "lib/os/assert.c", "tests/app/src/main.c",
         "drivers/x/x.c", "arch/Kconfig"]


def _workspace():
    d = tempfile.mkdtemp()
    for f in FILES:
        os.makedirs(os.path.join(d, os.path.dirname(f)), exist_ok=True)
        open(os.path.join(d, f), "w").write("x")
    return d


def test_kconfig_defined_at_is_collected():
    log = ("warning: FCB (defined at subsys/fs/fcb/Kconfig:10) was assigned the value 'y' but got\n"
           "the value 'n'. Check these unsatisfied dependencies: FLASH_MAP (=n).\n")
    assert "subsys/fs/fcb/Kconfig" in collect_relevant_context_paths(_workspace(), "tests/app", log)


def test_macro_prefix_mapped_paths_are_collected():
    log = ("Assertion failed at WEST_TOPDIR/zephyr/kernel/sem.c:776: x\n"
           "ASSERTION FAIL [y] @ ZEPHYR_BASE/lib/os/assert.c:12\n")
    out = collect_relevant_context_paths(_workspace(), "tests/app", log)
    assert "kernel/sem.c" in out and "lib/os/assert.c" in out


def test_absolute_paths_and_target_app_unchanged():
    log = "/zephyrproject/zephyr/drivers/x/x.c:3:1: error: expected ';'\n"
    out = collect_relevant_context_paths(_workspace(), "tests/app", log)
    assert out == ["drivers/x/x.c", "tests/app/src/main.c"]


def test_nonexistent_relative_paths_are_dropped():
    log = "warning: FOO (defined at nowhere/Kconfig:1) was assigned\n"
    assert collect_relevant_context_paths(_workspace(), "tests/app", log) == ["tests/app/src/main.c"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
