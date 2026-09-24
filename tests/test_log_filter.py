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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
