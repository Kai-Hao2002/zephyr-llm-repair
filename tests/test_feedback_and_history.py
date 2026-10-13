"""Tests for the 2026-10-13 changes: the shared spec rule in every patch prompt, the most recent
build log kept after an apply/StaticCheck failure, B3's raw history, and the apply-error hint.
LLM and evaluator are mocked. Run: python tests/test_feedback_and_history.py"""
import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agents.baselines as baselines
import agents.patch_expert as patch_expert
import core.baseline_pipelines as bp
from agents.spec_rule import SPEC_RULE
from core.workflow import with_last_build_log
from tools.patch_applier import PatchApplier


def test_spec_rule_in_every_patch_prompt():
    assert SPEC_RULE in baselines._PATCH_FORMAT_SYSTEM_PROMPT  # B2, B3
    assert "SPEC_RULE" in inspect.getsource(baselines.b1_generate_full_file_patch)
    assert "SPEC_RULE" in inspect.getsource(patch_expert)


def test_with_last_build_log():
    out = with_last_build_log("Patch Application Failed:\nX", "error: foo", "note")
    assert out.startswith("Patch Application Failed") and "error: foo" in out and "(note)" in out
    assert with_last_build_log("msg", "", "note") == "msg"


def test_apply_error_hint_and_original_contents():
    ws = tempfile.mkdtemp()
    open(os.path.join(ws, "a.c"), "w").write("x;\n")
    r = PatchApplier(ws).apply_patches("--- a.c\n<<<<<<<< SEARCH\nx;\n========\ny;\n>>>>>>>> REPLACE")
    assert not r["success"] and "its own line containing only the file's relative path" in r["error"]
    r = PatchApplier(ws).apply_patches("a.c\n<<<<<<<< SEARCH\nx;\n========\ny;\n>>>>>>>> REPLACE")
    assert r["success"] and r["original_contents"] == {"a.c": "x;\n"}
    r = PatchApplier(ws).apply_patches("b.c\n<<<<<<<< SEARCH\nx;\n========\ny;\n>>>>>>>> REPLACE")
    assert "relative path" not in r["error"]


def test_b3_history_and_last_build_log():
    ws = tempfile.mkdtemp()
    open(os.path.join(ws, "a.c"), "w").write("bad;\n")
    patches = [
        "a.c\n<<<<<<<< SEARCH\nnot there;\n========\nx;\n>>>>>>>> REPLACE",  # apply fails
        "a.c\n<<<<<<<< SEARCH\nbad;\n========\nstill_bad;\n>>>>>>>> REPLACE",  # builds, fails
        "a.c\n<<<<<<<< SEARCH\nstill_bad;\n========\ngood;\n>>>>>>>> REPLACE",  # resolves
    ]
    seen = []

    def fake_gen(error_log, files, history=""):
        seen.append((error_log, history))
        return patches[len(seen) - 1], {"node": "b3_closed_loop", "input_tokens": 1, "output_tokens": 1}

    def fake_eval(ws_, board, app, test):
        good = "good;" in open(os.path.join(ws, "a.c")).read()
        return {"status": "success" if good else "crash", "resolved": good, "compiled": True,
                "log": "ok" if good else "Assertion failed at a.c: second"}

    orig = (bp.b3_generate_patch, bp.evaluate_repair_attempt, bp._read_context_files)
    bp.b3_generate_patch, bp.evaluate_repair_attempt = fake_gen, fake_eval
    bp._read_context_files = lambda *a, **k: ""
    try:
        out = bp.run_b3({"workspace_path": ws, "target_app": ".", "board": "native_sim",
                         "current_error_log": "Assertion failed at a.c: initial"}, 5)
    finally:
        bp.b3_generate_patch, bp.evaluate_repair_attempt, bp._read_context_files = orig
    assert out["final_status"] == "resolved" and out["iterations"] == 3
    # iteration 1: initial log, no history
    assert seen[0] == ("Assertion failed at a.c: initial", "")
    # iteration 2: apply failure + the initial build log, history has attempt 1
    assert seen[1][0].startswith("Patch Application Failed") and "initial" in seen[1][0]
    assert "--- Attempt 1 ---" in seen[1][1] and "not there;" in seen[1][1]
    # iteration 3: latest build log, history has attempts 1 and 2
    assert seen[2][0] == "Assertion failed at a.c: second"
    assert "--- Attempt 2 ---" in seen[2][1] and "status crash" in seen[2][1]


def test_b3_history_truncation():
    text = bp._render_b3_history([{"iteration": 1, "patch": "p" * 20000, "outcome": "x", "log": "l" * 20000}])
    assert len(text) < bp._B3_HISTORY_PATCH_CHARS + bp._B3_HISTORY_LOG_CHARS + 500 and "chars omitted" in text


def test_block_without_path_is_an_error():
    ws = tempfile.mkdtemp()
    open(os.path.join(ws, "a.c"), "w").write("x;\ny;\n")
    two = ("a.c\n<<<<<<<< SEARCH\nx;\n========\nX;\n>>>>>>>> REPLACE\n"
           "<<<<<<<< SEARCH\ny;\n========\nY;\n>>>>>>>> REPLACE")
    r = PatchApplier(ws).apply_patches(two)
    assert not r["success"] and "1 SEARCH/REPLACE block(s) had no file path line" in r["error"]
    assert r["applied_files"] == ["a.c"] and open(os.path.join(ws, "a.c")).read() == "X;\ny;\n"
    ok = two.replace(">>>>>>>> REPLACE\n<<<<<<<< SEARCH\ny;", ">>>>>>>> REPLACE\na.c\n<<<<<<<< SEARCH\ny;")
    open(os.path.join(ws, "a.c"), "w").write("x;\ny;\n")
    r = PatchApplier(ws).apply_patches(ok)
    assert r["success"] and open(os.path.join(ws, "a.c")).read() == "X;\nY;\n"


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
