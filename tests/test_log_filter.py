"""Regression tests for tools/log_filter.py, using trimmed real Zephyr build output.

Before the fix, all three samples below were compressed to just the trailing
"ninja: build stopped" line, hiding the real error from the agents.
Run: python -m pytest tests/test_log_filter.py  (or python tests/test_log_filter.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.log_filter import LogFilter

NINJA = "ninja: build stopped: subcommand failed.\n"

KCONFIG_WARNING = (
    "-- Generated devicetree_generated.h: /tmp/build/zephyr/include/generated/zephyr/devicetree_generated.h\n"
    "warning: FAT_FILESYSTEM_ELM (defined at subsys/fs/Kconfig.fatfs:6) was assigned the value 'y' but\n"
    "got the value 'n'. Check these unsatisfied dependencies: (!FILE_SYSTEM_LIB_LINK) (=n). See\n"
    "helpful too.\n"
    "Parsing /zephyrproject/zephyr/Kconfig\n"
    "Loaded configuration '/zephyrproject/zephyr/boards/native/native_sim/native_sim_defconfig'\n"
)

MISSING_HEADER = (
    "In file included from /zephyrproject/zephyr/tests/subsys/fs/fat_fs_api/src/main.c:10:\n"
    + "/zephyrproject/zephyr/tests/subsys/fs/fat_fs_api/src/test_fat.h:12:10: fatal error: ff.h: No such file or directory\n"
    * 5
    + "compilation terminated.\n"
)

LINK_ERRORS = (
    "/zephyrproject/zephyr/tests/drivers/bbram/emul/src/main.c:108:(.text.before+0x9): undefined reference to `__device_dts_ord_4'\n"
    "/usr/bin/ld: main.c:109:(.text.before+0x17): undefined reference to `__device_dts_ord_4'\n"
    "/usr/bin/ld: main.c:110:(.text.before+0xe): undefined reference to `bbram_emul_set_invalid'\n"
    "collect2: error: ld returned 1 exit status\n"
)


def test_zephyr_kconfig_warning_is_kept_with_continuation_lines():
    out = LogFilter().compress_log(KCONFIG_WARNING + NINJA)
    assert "FAT_FILESYSTEM_ELM (defined at subsys/fs/Kconfig.fatfs:6)" in out
    assert "(!FILE_SYSTEM_LIB_LINK)" in out
    assert "Parsing /zephyrproject" not in out


def test_fatal_error_kept_and_deduplicated():
    out = LogFilter().compress_log(MISSING_HEADER + NINJA)
    assert "ff.h: No such file or directory" in out
    assert out.count("ff.h: No such file or directory") == 1


def test_link_errors_kept_and_deduplicated_by_symbol():
    out = LogFilter().compress_log(LINK_ERRORS + NINJA)
    assert "bbram_emul_set_invalid" in out
    assert out.count("__device_dts_ord_4") == 1
    assert "collect2: error" in out


def test_link_errors_are_capped():
    many = "".join(f"main.c:{i}:(.text+0x1): undefined reference to `sym_{i}'\n" for i in range(100))
    out = LogFilter().compress_log(many + NINJA)
    assert out.count("undefined reference") <= LogFilter().link_error_max_symbols
    assert "more distinct link errors omitted" in out


def test_plain_c_error_and_unrelated_error_line_behaviour_unchanged():
    log = "src/main.c:15:5: error: expected ';' before 'return'\nerror: RPC failed; curl 56\n" + NINJA
    out = LogFilter().compress_log(log)
    assert "expected ';' before 'return'" in out
    assert "RPC failed" not in out


BENIGN_KCONFIG_THEN_ASSERT = (
    "warning: MAX_THREAD_BYTES (defined at arch/Kconfig:410) was assigned the value '3' but got the value\n"
    "''. Check these unsatisfied dependencies: USERSPACE (=n). See\n"
    "Parsing /zephyrproject/zephyr/Kconfig\n"
    "[110/111] Running utility command for native_runner_executable\n"
    "*** Booting Zephyr OS build v4.3.0-9263-g5286027c8594 ***\n"
    "Running TESTSUITE semaphore\n"
    "START - test_k_sem_correct_count_limit\n"
    "Assertion failed at WEST_TOPDIR/zephyr/tests/kernel/semaphore/semaphore/src/main.c:776: "
    "semaphore_test_k_sem_correct_count_limit: (_act not equal to _exp)\n"
)


def test_runtime_tail_kept_when_benign_kconfig_warning_matches():
    # v2 bug: the warning matched, so the tail (with the assertion) was dropped.
    out = LogFilter().compress_log(BENIGN_KCONFIG_THEN_ASSERT)
    assert "MAX_THREAD_BYTES" in out
    assert "[Runtime Log Tail]" in out
    assert "START - test_k_sem_correct_count_limit" in out
    assert "main.c:776" in out
    assert "Running utility command" not in out


def test_no_runtime_tail_for_build_failures():
    out = LogFilter().compress_log(MISSING_HEADER + NINJA)
    assert "[Runtime Log Tail]" not in out


def test_runtime_tail_is_capped():
    log = (BENIGN_KCONFIG_THEN_ASSERT
           + "".join(f"E:      a{i}: 0000000{i}\n" for i in range(100))
           + "E: >>> ZEPHYR FATAL ERROR 3: Kernel oops on CPU 0\n")
    out = LogFilter().compress_log(log)
    tail = out.split("[Runtime Log Tail]\n", 1)[1].splitlines()
    assert len(tail) <= LogFilter.runtime_tail_max_lines + 1
    assert tail[0].startswith("START - ") and "ZEPHYR FATAL ERROR" in tail[-1]


def test_runtime_tail_starts_at_first_failing_test():
    log = (BENIGN_KCONFIG_THEN_ASSERT + "FAIL - test_k_sem_correct_count_limit\n"
           + "".join(f"START - test_{i}\nPASS - test_{i}\n" for i in range(5))
           + "PROJECT EXECUTION FAILED\n")
    tail = LogFilter().compress_log(log).split("[Runtime Log Tail]\n", 1)[1]
    assert tail.startswith("START - test_k_sem_correct_count_limit")
    assert "main.c:776" in tail


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
