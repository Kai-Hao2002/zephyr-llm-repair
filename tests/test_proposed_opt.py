"""Tests for the Proposed optimizations (core/proposed_opt.py): gate retries, ordered context, no-progress memory,
and that everything stays off with the switch off. Run: python tests/test_proposed_opt.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.patch_expert as pe
import core.baseline_pipelines as bp
import core.protected_files as pf
import core.workflow as wf
from agents.supervisor import route_after_apply_patch
from core.proposed_opt import failure_signature
from core.state import create_initial_state


def _block(path, old, new):
    return f"{path}\n<<<<<<<< SEARCH\n{old}\n========\n{new}\n>>>>>>>> REPLACE\n"


def _workspace():
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, "tests/app/src"))
    os.makedirs(os.path.join(ws, "drivers"))
    open(os.path.join(ws, "tests/app/src/main.c"), "w").write("int main;\n")
    open(os.path.join(ws, "drivers/a.c"), "w").write("int a;\n")
    open(os.path.join(ws, "drivers/b.c"), "w").write("int b;\n")
    return ws


def _with_opt(on, fn):
    orig = wf.PROPOSED_OPT
    wf.PROPOSED_OPT = on
    try:
        return fn()
    finally:
        wf.PROPOSED_OPT = orig


def _state(ws, patch, **extra):
    s = create_initial_state(ws, "error: original", target_app="tests/app")
    s.update(patch_content=patch, **extra)
    return s


def test_signature_ignores_numbers_but_not_text():
    assert failure_signature("main.c:12: error x at 0xdead in 0.1s") == failure_signature("main.c:99: error x at 0xbeef in 0.2s")
    assert failure_signature("error x") != failure_signature("error y")


def test_context_order_log_then_app_then_retrieved():
    ws = _workspace()
    log = "/zephyrproject/zephyr/drivers/b.c:1:1: error: x\n"
    chosen, stats = pe.select_ordered_context(ws, "tests/app", log, ["drivers/a.c", "drivers/b.c"])
    assert [p for p, _ in chosen] == ["drivers/b.c", "tests/app/src/main.c", "drivers/a.c"]
    assert stats["skipped"] == []


def test_context_skips_a_big_file_and_keeps_going():
    ws = _workspace()
    open(os.path.join(ws, "drivers/big.c"), "w").write("x" * 50)
    chosen, stats = pe.select_ordered_context(ws, "tests/app", "", ["drivers/big.c", "drivers/a.c"], limit=1000, retrieval_quota=20)
    assert [p for p, _ in chosen] == ["tests/app/src/main.c", "drivers/a.c"]
    assert stats["skipped"] == ["drivers/big.c"]


def test_failed_apply_retries_without_consuming_an_iteration_and_rolls_back():
    ws = _workspace()
    patch = _block("drivers/a.c", "int a;", "int z;") + _block("drivers/b.c", "NOT THERE", "x")
    out = _with_opt(True, lambda: wf.apply_patch_node(_state(ws, patch)))
    assert out["error_type"] == "patch_gate_retry" and "iterations" not in out and out["gate_retries"] == 1
    assert "SearchMatchFailed" in out["current_error_log"] and "error: original" in out["current_error_log"]
    assert open(os.path.join(ws, "drivers/a.c")).read() == "int a;\n"
    assert route_after_apply_patch(out) == "retry_patch"


def test_gate_retries_are_capped_then_the_iteration_is_consumed():
    ws = _workspace()
    out = _with_opt(True, lambda: wf.apply_patch_node(_state(ws, _block("drivers/a.c", "NOT THERE", "x"), gate_retries=2)))
    assert out["error_type"] == "patch_format_error" and out["iterations"] == 1 and out["gate_retries"] == 0


def test_patch_touching_only_protected_files_is_rejected_before_building():
    ws = _workspace()
    pf.register(ws, "tests/app", [], tempfile.mkdtemp())
    patch = _block("tests/app/src/main.c", "int main;", "int other;")
    out = _with_opt(True, lambda: wf.apply_patch_node(_state(ws, patch)))
    assert out["error_type"] == "patch_gate_retry" and "reverted" in out["current_error_log"]
    assert open(os.path.join(ws, "tests/app/src/main.c")).read() == "int main;\n"
    mixed = patch + _block("drivers/a.c", "int a;", "int z;")
    out = _with_opt(True, lambda: wf.apply_patch_node(_state(ws, mixed)))
    assert out["error_type"] == "patch_applied"
    pf.unregister(ws)


def test_switch_off_keeps_the_old_behaviour():
    ws = _workspace()
    out = _with_opt(False, lambda: wf.apply_patch_node(_state(ws, _block("drivers/a.c", "NOT THERE", "x"))))
    assert out["error_type"] == "patch_format_error" and out["iterations"] == 1
    outcome = {"attempt_history": ["[Iteration 1] x"]}
    state = _state(ws, "")
    assert _with_opt(False, lambda: wf.no_progress_fields(state, "error: original", ["a.c"], outcome)) is outcome


def test_unchanged_failure_is_marked_and_remembered():
    state = _state(_workspace(), "")
    outcome = {"attempt_history": ["[Iteration 1] x"]}
    same = _with_opt(True, lambda: wf.no_progress_fields(state, "error: original", ["drivers/a.c"], outcome))
    assert "[No progress]" in same["attempt_history"][0] and "drivers/a.c" in same["ineffective_attempts"][0]
    other = _with_opt(True, lambda: wf.no_progress_fields(state, "error: new", ["drivers/a.c"], outcome))
    assert "ineffective_attempts" not in other and other["attempt_history"] == outcome["attempt_history"]
    assert other["last_failure_signature"] == failure_signature("error: new")


def test_graph_routes_the_gate_back_to_patch():
    edges = {(e.source, e.target) for e in wf.build_zephyr_graph().get_graph().edges}
    assert ("ApplyPatch", "Patch") in edges


def _run_b3(ws, patches, gate):
    seen = []

    def fake_gen(error_log, files, history=""):
        seen.append(error_log)
        return patches[len(seen) - 1], {"node": "b3_closed_loop", "input_tokens": 1, "output_tokens": 1}

    def fake_eval(ws_, board, app, test):
        good = "fixed" in open(os.path.join(ws, "drivers/a.c")).read()
        return {"status": "success" if good else "crash", "resolved": good, "compiled": True, "log": "boom"}

    orig = (bp.b3_generate_patch, bp.evaluate_repair_attempt, bp._read_context_files, bp.B3_GATE)
    bp.b3_generate_patch, bp.evaluate_repair_attempt = fake_gen, fake_eval
    bp._read_context_files, bp.B3_GATE = (lambda *a, **k: ""), gate
    try:
        out = bp.run_b3({"workspace_path": ws, "target_app": "tests/app", "board": "native_sim",
                         "current_error_log": "boom"}, 5)
    finally:
        bp.b3_generate_patch, bp.evaluate_repair_attempt, bp._read_context_files, bp.B3_GATE = orig
    return out, seen


def test_b3_gate_retries_within_the_iteration_and_rolls_back():
    ws = _workspace()
    partial = _block("drivers/a.c", "int a;", "int partial;") + _block("drivers/b.c", "not there", "x")
    out, seen = _run_b3(ws, [partial, _block("drivers/a.c", "int a;", "int fixed;")], True)
    assert out["final_status"] == "resolved" and out["iterations"] == 1
    assert len(out["iteration_log"][0]["token_usage"]) == 2
    assert len(out["iteration_log"][0]["trajectory"]["gate_events"]) == 1
    assert "Patch Application Failed" in seen[1]
    assert open(os.path.join(ws, "drivers/a.c")).read() == "int fixed;\n"


def test_b3_gate_spends_an_iteration_after_two_retries():
    ws = _workspace()
    bad = _block("drivers/a.c", "not there", "x")
    out, seen = _run_b3(ws, [bad, bad, bad, _block("drivers/a.c", "int a;", "int fixed;")], True)
    assert out["iterations"] == 2 and len(seen) == 4
    assert len(out["iteration_log"][0]["token_usage"]) == 3


def test_b3_gate_off_keeps_the_old_loop():
    ws = _workspace()
    out, seen = _run_b3(ws, [_block("drivers/a.c", "not there", "x"), _block("drivers/a.c", "int a;", "int fixed;")], False)
    assert out["iterations"] == 2 and len(seen) == 2
    assert "gate_events" not in out["iteration_log"][0]["trajectory"]
    assert len(out["iteration_log"][0]["token_usage"]) == 1


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
