"""Regression tests for QemuOracle's expected-fault handling, replaying trimmed real
run output through `cat` (no Docker). The stack sample is from tests/kernel/stack/stack
on qemu_riscv32 (2026-10-03 v3 pilot), which the old oracle misjudged as a crash.
Run: python -m pytest tests/test_qemu_oracle.py  (or python tests/test_qemu_oracle.py)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.qemu_oracle import QemuOracle

HEAD = ("-- west build: generating a build system\n"
        "*** Booting Zephyr OS build v4.4.0-3085-gbcf31c20f01f ***\n"
        "Running TESTSUITE stack_fail\n")
EXPECTED_FAULT = (
    "START - test_stack_user_init_invalid_value\n"
    "E: syscall z_vrfy_k_stack_alloc_init failed check: num_entries > 0\n"
    "E:  mcause: 8, Environment call from U-mode\n"
    "E: >>> ZEPHYR FATAL ERROR 3: Kernel oops on CPU 0\n"
    "Caught system error -- reason 3 1\n"
    "Fatal error expected as part of test case.\n"
    " PASS - test_stack_user_init_invalid_value in 0.007 seconds\n"
)
SUCCESS_TAIL = "TESTSUITE stack_fail succeeded\nPROJECT EXECUTION SUCCESSFUL\n"


def _run(text, required=None, timeout=20):
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(text)
    oracle = QemuOracle(timeout=timeout)
    oracle.fault_resolution_timeout = 2
    try:
        return oracle.evaluate(f"cat {f.name}", wait_for_completion=True, required_pass_test=required)
    finally:
        os.remove(f.name)


def _run_then_hang(text):
    """Prints text, then stays silent (like QEMU after k_fatal_halt)."""
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(text)
    oracle = QemuOracle(timeout=60)
    oracle.fault_resolution_timeout = 2
    try:
        return oracle.evaluate(f"bash -c 'cat {f.name}; sleep 30'", wait_for_completion=True)
    finally:
        os.remove(f.name)


def test_expected_fault_is_not_a_crash():
    r = _run(HEAD + EXPECTED_FAULT + SUCCESS_TAIL, required="test_stack_user_init_invalid_value")
    assert r["status"] == "success", r["status"]


def test_unexpected_fault_is_a_crash():
    log = HEAD + EXPECTED_FAULT.replace("reason 3 1", "reason 3 0").replace(
        "Fatal error expected as part of test case.", "Fatal error was unexpected, aborting...")
    assert _run(log + SUCCESS_TAIL)["status"] == "crash"


def test_fault_without_error_hook_then_silence_is_a_crash():
    log = HEAD + "START - test_x\nE: >>> ZEPHYR FATAL ERROR 3: Kernel oops on CPU 0\nE: Halting system\n"
    r = _run_then_hang(log)
    assert r["status"] == "crash" and r["error_signature"] == "ZEPHYR FATAL ERROR"


def test_fault_then_eof_is_a_crash():
    assert _run(HEAD + "START - test_x\nE: >>> ZEPHYR FATAL ERROR 0: CPU exception\n")["status"] == "crash"


def test_assertion_failure_still_immediate_crash():
    log = HEAD + "START - test_x\nAssertion failed at main.c:1\nFAIL - test_x\nPROJECT EXECUTION FAILED\n"
    assert _run(log)["status"] == "crash"


def test_unresolved_fault_before_success_summary_stays_crash():
    log = HEAD + "START - test_x\nE: >>> ZEPHYR FATAL ERROR 3: Kernel oops\n" + SUCCESS_TAIL
    assert _run(log)["status"] == "crash"


def test_ztest_assertion_stops_immediately():
    log = HEAD + "START - test_x\nAssertion failed at main.c:1: x\n" 
    r = _run_then_hang(log)
    assert r["status"] == "crash" and r["error_signature"] == "ASSERTION FAIL"


def test_kernel_assert_with_expected_hook_continues():
    log = (HEAD + "START - test_y\nASSERTION FAIL [x] @ kernel/sem.c:10\nCaught assert failed\n"
           "Assert error expected as part of test case.\nPASS - test_y\n" + SUCCESS_TAIL)
    assert _run(log)["status"] == "success"


def test_aborted_in_log_text_is_not_a_crash():
    log = (HEAD + "START - test_capture_timeout\n<inf> biometrics_emul: Enrollment aborted\n"
           "PASS - test_capture_timeout\n" + SUCCESS_TAIL)
    assert _run(log)["status"] == "success"


def test_host_abort_is_a_crash():
    log = HEAD + "START - test_x\nbash: line 1:  42 Aborted                 (core dumped) ./zephyr.exe\n"
    assert _run(log)["status"] == "crash"
    assert _run(HEAD + "START - test_x\nAborted (core dumped)\n")["status"] == "crash"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
