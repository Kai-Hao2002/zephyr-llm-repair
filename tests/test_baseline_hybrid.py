"""Tests for the 2026-11 baseline definition (core/baseline_pipelines.py): B2 and B3 retrieve with the same
Hybrid RAG call as the Knowledge Expert, B3 retrieves every iteration and feeds the result into the context,
first_retrieval_files is written once, and B3 never touches the Proposed-only roles. No Docker, no LLM.
Run: python -m pytest tests/test_baseline_hybrid.py  (or python tests/test_baseline_hybrid.py)"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.analyzer as analyzer
import agents.supervisor as supervisor
import core.baseline_pipelines as bp
import core.llm_retry as llm_retry
import core.workflow as wf
import tools.static_checker as static_checker


def _block(path, old, new):
    return f"{path}\n<<<<<<<< SEARCH\n{old}\n========\n{new}\n>>>>>>>> REPLACE\n"


def _workspace():
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, "drivers"))
    open(os.path.join(ws, "drivers/a.c"), "w").write("int a;\n")
    return ws


class _FakeRetriever:
    def __init__(self, results):
        self.calls, self.results = [], list(results)

    def retrieve(self, query, top_k=5, bm25_only=False):
        self.calls.append({"query": query, "top_k": top_k, "bm25_only": bm25_only})
        return self.results[min(len(self.calls), len(self.results)) - 1]


def _patched(attrs, fn):
    """Temporarily replace module attributes: attrs = [(module, name, value), ...]."""
    saved = [(m, n, getattr(m, n)) for m, n, _ in attrs]
    for m, n, v in attrs:
        setattr(m, n, v)
    try:
        return fn()
    finally:
        for m, n, v in saved:
            setattr(m, n, v)


def _fake_eval(ws, logs):
    """Resolved once drivers/a.c contains 'fixed'; otherwise returns the next log from logs."""
    def ev(ws_, board, app, test):
        good = "fixed" in open(os.path.join(ws, "drivers/a.c")).read()
        return {"status": "success" if good else "crash", "resolved": good, "compiled": True,
                "log": "" if good else logs.pop(0)}
    return ev


def _run_b3(ws, patches, retriever, logs, gate=False, max_iters=5):
    contexts = []

    def fake_gen(error_log, files, history=""):
        return patches[min(len(contexts), len(patches)) - 1], {"node": "b3_closed_loop"}

    def fake_ctx(ws_, app, error_log, retrieved):
        contexts.append(list(retrieved))
        return ""

    out = _patched([(bp, "get_hybrid_retriever", lambda ws_: retriever), (bp, "b3_generate_patch", fake_gen),
                    (bp, "evaluate_repair_attempt", _fake_eval(ws, logs)), (bp, "_read_context_files", fake_ctx),
                    (bp, "B3_GATE", gate)],
                   lambda: bp.run_b3({"workspace_path": ws, "target_app": ".", "board": "native_sim",
                                      "current_error_log": "log0"}, max_iters))
    return out, contexts


# (a) B2 uses the Knowledge Expert's Hybrid call: same retriever factory, top_k=8, no bm25_only.
def test_b2_uses_hybrid_retrieval_top8():
    ws = _workspace()
    retriever = _FakeRetriever([["drivers/a.c", "drivers/b.c"]])
    contexts = []

    def fake_ctx(ws_, app, error_log, retrieved):
        contexts.append(list(retrieved))
        return ""

    out = _patched([(bp, "get_hybrid_retriever", lambda ws_: retriever),
                    (bp, "b2_generate_patch", lambda log, files: (_block("drivers/a.c", "int a;", "int fixed;"), {})),
                    (bp, "evaluate_repair_attempt", _fake_eval(ws, [])), (bp, "_read_context_files", fake_ctx)],
                   lambda: bp.run_b2({"workspace_path": ws, "target_app": ".", "board": "native_sim",
                                      "current_error_log": "filtered log", "last_build_log": "filtered log"}))
    assert retriever.calls == [{"query": "filtered log", "top_k": 8, "bm25_only": False}]
    assert contexts == [["drivers/a.c", "drivers/b.c"]]
    assert out["first_retrieval_files"] == ["drivers/a.c", "drivers/b.c"]
    assert out["iteration_log"][0]["trajectory"]["retrieved_files"] == ["drivers/a.c", "drivers/b.c"]


def test_b2_uses_the_knowledge_expert_retriever_factory():
    import agents.knowledge_expert as ke
    assert bp.get_hybrid_retriever is ke.get_hybrid_retriever


def test_retrieval_failure_raises_in_strict_mode_and_is_skipped_otherwise():
    class Broken:
        def retrieve(self, *a, **k):
            raise RuntimeError("embedding down")

    def call():
        return bp._hybrid_retrieve("/ws", "q")

    orig = llm_retry.strict_fallbacks()
    try:
        llm_retry.set_strict_fallbacks(True)
        try:
            _patched([(bp, "get_hybrid_retriever", lambda ws_: Broken())], call)
            raise AssertionError("expected RetrievalDegradedError")
        except llm_retry.RetrievalDegradedError:
            pass
        llm_retry.set_strict_fallbacks(False)
        assert _patched([(bp, "get_hybrid_retriever", lambda ws_: Broken())], call) == []
    finally:
        llm_retry.set_strict_fallbacks(orig)
        llm_retry.pop_events()


# (b) B3 retrieves every iteration with last_build_log and the result reaches _read_context_files.
def test_b3_retrieves_every_iteration_into_the_context():
    ws = _workspace()
    retriever = _FakeRetriever([["r1.c"], ["r2.c"], ["r3.c"]])
    bad = _block("drivers/a.c", "int a;", "int still_bad;")
    patches = [bad, _block("drivers/a.c", "int still_bad;", "int also_bad;"),
               _block("drivers/a.c", "int also_bad;", "int fixed;")]
    out, contexts = _run_b3(ws, patches, retriever, ["log1", "log2"])
    assert out["final_status"] == "resolved" and out["iterations"] == 3
    assert [c["query"] for c in retriever.calls] == ["log0", "log1", "log2"]
    assert all(c["top_k"] == 8 and c["bm25_only"] is False for c in retriever.calls)
    assert contexts == [["r1.c"], ["r2.c"], ["r3.c"]]
    assert [it["trajectory"]["retrieved_files"] for it in out["iteration_log"]] == [["r1.c"], ["r2.c"], ["r3.c"]]


def test_b3_query_excludes_apply_failure_text_and_gate_retries_retrieve_too():
    ws = _workspace()
    retriever = _FakeRetriever([["r.c"]])
    bad = _block("drivers/a.c", "not there", "x")
    out, contexts = _run_b3(ws, [bad, _block("drivers/a.c", "int a;", "int fixed;")], retriever, [], gate=True)
    assert out["final_status"] == "resolved" and out["iterations"] == 1
    assert len(retriever.calls) == 2  # first try + one gate retry
    assert [c["query"] for c in retriever.calls] == ["log0", "log0"]  # not "Patch Application Failed..."
    assert out["iteration_log"][0]["trajectory"]["retrieved_files"] == ["r.c"]


# (c) first_retrieval_files is written once, an empty first result included.
def test_b3_first_retrieval_files_written_once():
    ws = _workspace()
    patches = [_block("drivers/a.c", "int a;", "int b1;"), _block("drivers/a.c", "int b1;", "int fixed;")]
    out, _ = _run_b3(ws, patches, _FakeRetriever([["first.c"], ["second.c"]]), ["log1"])
    assert out["first_retrieval_files"] == ["first.c"]
    ws = _workspace()
    out, _ = _run_b3(ws, patches, _FakeRetriever([[], ["second.c"]]), ["log1"])
    assert out["first_retrieval_files"] == []


def test_b3_failure_result_carries_first_retrieval_files():
    ws = _workspace()
    out, _ = _run_b3(ws, [_block("drivers/a.c", "int a;", "int b1;")], _FakeRetriever([["x.c"]]), ["l1", "l2"],
                     max_iters=2)
    assert out["final_status"] == "failed_max_retries" and out["first_retrieval_files"] == ["x.c"]


# (d) B3 never calls the Analyzer, the Supervisor or StaticCheck.
def test_b3_does_not_use_proposed_roles():
    def boom(*a, **k):
        raise AssertionError("Proposed-only role called by B3")

    blocked = [(analyzer, "analyzer_node", boom), (supervisor, "record_attempt_outcome", boom),
               (supervisor, "record_iteration_success", boom), (supervisor, "_compress_outcome_to_one_sentence", boom),
               (wf, "static_check_node", boom), (static_checker, "StaticChecker", boom)]
    ws = _workspace()
    patches = [_block("drivers/a.c", "int a;", "int b1;"), _block("drivers/a.c", "int b1;", "int fixed;")]
    out = _patched(blocked, lambda: _run_b3(ws, patches, _FakeRetriever([["r.c"]]), ["log1"])[0])
    assert out["final_status"] == "resolved"
    names = set(vars(bp))
    assert not names & {"analyzer_node", "record_attempt_outcome", "static_check_node", "StaticChecker",
                        "knowledge_expert_node"}


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
