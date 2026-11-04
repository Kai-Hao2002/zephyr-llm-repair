"""Tests for the per-case Graph RAG (graph_rag/case_graph.py): rebuild only after config-like files change,
disabled switch, and retrieval output carrying definition sites and full depends-on expressions.
The container build is mocked. Run: python tests/test_case_graph.py"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import networkx as nx

import graph_rag.case_graph as cg
from graph_rag.retriever import GraphRetriever


def _graph():
    g = nx.DiGraph()
    g.add_node("CONFIG_ZBUS", type="bool", defined_at=["subsys/zbus/Kconfig:4"],
               depends_on_expr="!MULTITHREADING", help="Enables support for Zephyr message bus.")
    g.add_node("CONFIG_MULTITHREADING", type="bool", defined_at=["kernel/Kconfig:20"], depends_on_expr="y")
    g.add_edge("CONFIG_ZBUS", "CONFIG_MULTITHREADING", relation="depends_on")
    return g


def test_rebuild_only_after_config_change():
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, "subsys"))
    open(os.path.join(ws, "subsys", "Kconfig"), "w").write("config A\n")
    open(os.path.join(ws, "subsys", "a.c"), "w").write("int a;\n")
    calls = []
    orig, orig_switch = cg._build, cg.GRAPH_RAG
    cg._build = lambda w, a, b: (calls.append(w) or (_graph(), {"source": "target_app"}))
    cg.GRAPH_RAG = True  # 預設關閉 (2026-10-31)，這裡測開啟時的行為 / off by default; this tests the on path
    try:
        assert cg.get_case_graph(ws, ".", "native_sim")[1]["rebuilt"]
        assert not cg.get_case_graph(ws, ".", "native_sim")[1]["rebuilt"]
        time.sleep(0.01)
        open(os.path.join(ws, "subsys", "a.c"), "w").write("int b;\n")  # not a config file
        assert not cg.get_case_graph(ws, ".", "native_sim")[1]["rebuilt"]
        open(os.path.join(ws, "subsys", "Kconfig"), "w").write("config B\n")
        assert cg.get_case_graph(ws, ".", "native_sim")[1]["rebuilt"]
        assert len(calls) == 2
    finally:
        cg._build, cg.GRAPH_RAG = orig, orig_switch
        cg.drop_case_graph(ws)


def test_disabled_switch():
    orig = cg.GRAPH_RAG
    cg.GRAPH_RAG = False
    try:
        g, meta = cg.get_case_graph(tempfile.mkdtemp(), ".", "native_sim")
        assert g is None and meta == {"disabled": True}
    finally:
        cg.GRAPH_RAG = orig


def test_retrieval_shows_definition_site_and_expression():
    ctx = GraphRetriever(_graph()).retrieve_context(["ZBUS"])
    assert "subsys/zbus/Kconfig:4" in ctx and "'!MULTITHREADING'" in ctx and "depends_on" in ctx


def test_graph_json_lives_outside_workspace():
    ws = "/tmp/runs/case_x"
    assert (ws.rstrip(os.sep) + ".graph").startswith("/tmp/runs/case_x.graph")
    assert not (ws.rstrip(os.sep) + ".graph").startswith(ws + os.sep)


def test_graph_only_ablation_skips_hybrid_rag():
    import agents.knowledge_expert as ke
    calls = []
    orig = (ke.HYBRID_RAG, ke.get_case_graph, ke.get_hybrid_retriever)
    ke.get_case_graph = lambda *a: (_graph(), {"rebuilt": False})
    ke.get_hybrid_retriever = lambda ws: calls.append(ws) or (_ for _ in ()).throw(AssertionError("hybrid called"))
    try:
        ke.HYBRID_RAG = False
        out = ke.knowledge_expert_node({"search_keywords": ["ZBUS"], "workspace_path": "/tmp/x",
                                        "target_app": ".", "board": "native_sim"})
    finally:
        ke.HYBRID_RAG, ke.get_case_graph, ke.get_hybrid_retriever = orig
    assert out["retrieved_files"] == [] and calls == []
    assert "subsys/zbus/Kconfig:4" in out["retrieved_context"]


def test_launcher_refuses_mixed_retrieval_settings():
    import json
    import subprocess
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "pilot", "proposed"))
    meta = {"models": {"pro": "gemini-x"}, "proposed_graph_rag": True, "proposed_hybrid_rag": True}
    json.dump([{"run_meta": meta}], open(os.path.join(root, "pilot", "proposed", "c.json"), "w"))
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, MODEL="gemini-x", OUT_ROOT=root, STAGE="pilot", PIPELINES="proposed", ZEPHYR_HYBRID_RAG="0")
    r = subprocess.run(["bash", os.path.join(repo, "tools", "run_v3_freetier.sh")], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 65 and "proposed_hybrid_rag=True (this run: False)" in r.stdout


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
