"""Tests for the Agentless baseline (core/agentless_pipeline.py, agents/agentless.py) without LLM or Docker:
repository structure, candidate files, skeletons, code context, reply parsing, and candidate validation
(stop at the first passing candidate, revert failed candidates, skip duplicates).
Run: python tests/test_agentless_pipeline.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agents.agentless as al
import core.agentless_pipeline as ap


def _ws():
    ws = tempfile.mkdtemp()
    files = {
        "kernel/sem.c": "#include <x.h>\n\nint k_sem_take(struct k_sem *s, int t)\n{\n\treturn s->count > 0;\n}\n",
        "kernel/Kconfig": "menu \"Kernel\"\nconfig MULTITHREADING\n\tbool \"mt\"\n\tdefault y\nendmenu\n",
        "tests/app/src/main.c": "ZTEST(s, test_a)\n{\n\tzassert_true(k_sem_take(&s, 0) == 1);\n}\n",
        "tests/app/prj.conf": "CONFIG_ZTEST=y\n",
        "tests/app/boards/native_sim.overlay": "/ {\n\tnode: node {\n\t\tstatus = \"okay\";\n\t};\n};\n",
        "doc/index.rst": "docs\n",
        ".git/config": "x\n",
    }
    for p, c in files.items():
        os.makedirs(os.path.dirname(os.path.join(ws, p)), exist_ok=True)
        open(os.path.join(ws, p), "w").write(c)
    return ws


def test_repo_tree_and_candidate_files():
    ws = _ws()
    tree, dirs = ap.repo_tree(ws)
    assert "kernel/" in tree and "tests/app/" in tree and "doc/" not in tree and ".git/" not in tree
    assert "tests/app/src" not in dirs  # only the top two levels
    files = ap.candidate_files(ws, ["kernel"], "tests/app", "error at /zephyrproject/zephyr/kernel/sem.c:5")
    assert files[0] == "kernel/sem.c" and "tests/app/prj.conf" in files and "kernel/Kconfig" in files
    assert "doc/index.rst" not in files


def test_skeletons():
    ws = _ws()
    assert "3: int k_sem_take(struct k_sem *s, int t)" in ap.skeleton(ws, "kernel/sem.c")
    assert "2: config MULTITHREADING" in ap.skeleton(ws, "kernel/Kconfig")
    assert "1: ZTEST(s, test_a)" in ap.skeleton(ws, "tests/app/src/main.c")
    assert "2: \tnode: node {" in ap.skeleton(ws, "tests/app/boards/native_sim.overlay")
    assert "1: CONFIG_ZTEST=y" in ap.skeleton(ws, "tests/app/prj.conf")


def test_code_context_windows():
    ws = _ws()
    ctx = ap.code_context(ws, ["kernel/sem.c", "tests/app/prj.conf"], {"kernel/sem.c": [(5, 5)]})
    assert "### kernel/sem.c (from line 1)" in ctx and "return s->count > 0;" in ctx
    assert "### tests/app/prj.conf (from line 1)" in ctx  # small file without a location: shown whole


def test_reply_parsing():
    assert al._paths_in("1. `kernel/`\n- tests/app\nnonsense\nkernel", ["kernel", "tests/app"], 5) == ["kernel", "tests/app"]
    orig = al._ask
    al._ask = lambda *a, **k: ("kernel/sem.c: 3-6\n`tests/app/src/main.c`: 3\nother.c: 1-2", {})
    try:
        locs, _ = al.localize_elements("log", "skel", ["kernel/sem.c", "tests/app/src/main.c"])
    finally:
        al._ask = orig
    assert locs == {"kernel/sem.c": [(3, 6)], "tests/app/src/main.c": [(3, 3)]}


def test_validation_reverts_failed_candidates_and_stops_at_first_pass():
    ws = _ws()
    bad = "kernel/sem.c\n<<<<<<<< SEARCH\n\treturn s->count > 0;\n========\n\treturn 0;\n>>>>>>>> REPLACE"
    good = "kernel/sem.c\n<<<<<<<< SEARCH\n\treturn s->count > 0;\n========\n\treturn 1;\n>>>>>>>> REPLACE"
    seen_states = []

    def fake_eval(ws_, board, app, test):
        text = open(os.path.join(ws, "kernel/sem.c")).read()
        seen_states.append(text)
        ok = "return 1;" in text
        return {"status": "success" if ok else "crash", "resolved": ok, "compiled": True, "log": "x"}

    orig = (ap.localize_directories, ap.localize_files, ap.localize_elements, ap.generate_candidate,
            ap.evaluate_repair_attempt)
    ap.localize_directories = lambda log, tree, dirs: (["kernel"], {"node": "d"})
    ap.localize_files = lambda log, files: (["kernel/sem.c"], {"node": "f"})
    ap.localize_elements = lambda log, skel, files: ({"kernel/sem.c": [(5, 5)]}, {"node": "e"})
    cands = [bad, bad + " ", good, bad]
    asked = []
    ap.generate_candidate = lambda log, ctx, i: (asked.append(i) or cands[i - 1], {"node": f"r{i}"})
    ap.evaluate_repair_attempt = fake_eval
    try:
        out = ap.run_agentless({"workspace_path": ws, "target_app": "tests/app", "board": "native_sim",
                                "current_error_log": "log"})
    finally:
        (ap.localize_directories, ap.localize_files, ap.localize_elements, ap.generate_candidate,
         ap.evaluate_repair_attempt) = orig
    assert out["final_status"] == "resolved" and out["iterations"] == 2
    assert [e["trajectory"]["status"] for e in out["iteration_log"]] == ["crash", "duplicate_skipped", "success"]
    assert "return 0;" in seen_states[0] and "return 0;" not in seen_states[1]  # failed candidate was reverted
    assert "return 1;" in open(os.path.join(ws, "kernel/sem.c")).read()      # passing candidate is kept
    assert out["agentless"]["duplicates_skipped"] == 1
    assert asked == [1, 2, 3]  # no 4th candidate is generated after the third passes


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
