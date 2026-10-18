# graph_rag/case_graph.py
"""
每筆案例的知識圖譜 (host 端，2026-10-18)：在 zephyr-sandbox 容器裡對注入後的 workspace 執行
graph_rag/build_case_graph.py，把輸出的 JSON 轉成 NetworkX 圖，交給 GraphRetriever。圖存在 workspace
外面 (<workspace>.graph/)，Hybrid RAG 不會把它當成原始碼索引進去。Kconfig/DTS/overlay/.conf 這類檔案
被修改後下一次檢索會重建，圖始終反映目前的 workspace。

開關 ZEPHYR_GRAPH_RAG (預設開啟；Graph RAG 是論文提案方法的一部分，關閉用於 ablation)，記在 run_meta。

Per-case knowledge graph (host side, 2026-10-18): runs graph_rag/build_case_graph.py in the zephyr-sandbox
container against the injected workspace and turns its JSON into a NetworkX graph for GraphRetriever. The
graph lives outside the workspace (<workspace>.graph/), so Hybrid RAG never indexes it; it is rebuilt when
Kconfig/DTS/overlay/.conf files change, so it always reflects the current workspace. Switch ZEPHYR_GRAPH_RAG
(on by default, Graph RAG being part of the proposed method; off for ablations), recorded in run_meta.
"""
import json
import logging
import os
import subprocess
import time
import uuid
from typing import Dict, Optional, Tuple

import networkx as nx

GRAPH_RAG = os.environ.get("ZEPHYR_GRAPH_RAG", "1").strip().lower() not in ("0", "false", "no")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_SUFFIXES = (".dts", ".dtsi", ".overlay", ".conf", ".defconfig")
_SKIP_DIRS = {".cache", ".git", "__pycache__"}
logger = logging.getLogger(__name__)

# workspace -> (fingerprint, graph, meta)
_cache: Dict[str, Tuple[int, nx.DiGraph, Dict]] = {}


def _config_fingerprint(workspace: str) -> int:
    """Kconfig*/DTS/overlay/.conf 檔案的 (路徑, mtime) 雜湊。Hash of (path, mtime) of config-like files."""
    h = 0
    for dirpath, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if f.startswith("Kconfig") or f.endswith(_CONFIG_SUFFIXES):
                p = os.path.join(dirpath, f)
                try:
                    h ^= hash((p, os.stat(p).st_mtime_ns))
                except OSError:
                    pass
    return h


def _build(workspace: str, target_app: str, board: str) -> Tuple[nx.DiGraph, Dict]:
    out_dir = workspace.rstrip(os.sep) + ".graph"
    os.makedirs(out_dir, exist_ok=True)
    out_json = os.path.join(out_dir, "graph.json")
    if os.path.exists(out_json):
        os.remove(out_json)
    name = f"casegraph_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    cmd = ["docker", "run", "--rm", "--name", name,
           "-v", f"{os.path.abspath(workspace)}:/zephyrproject/zephyr",
           "-v", f"{_REPO}:/repo:ro", "-v", f"{out_dir}:/out", "zephyr-sandbox",
           "python3", "/repo/graph_rag/build_case_graph.py", "--app", target_app, "--board", board,
           "--out", "/out/graph.json"]
    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        return nx.DiGraph(), {"error": "graph build timed out"}
    if not os.path.exists(out_json):
        return nx.DiGraph(), {"error": f"graph build failed (exit {proc.returncode}): {proc.stderr[-800:]}"}
    data = json.load(open(out_json, encoding="utf-8"))
    g = nx.DiGraph()
    for nid, attrs in data["nodes"].items():
        g.add_node(nid, **attrs)
    for src, dst, rel in data["edges"]:
        g.add_edge(src, dst, relation=rel)
    meta = dict(data.get("meta", {}), seconds=round(time.time() - start, 1),
                nodes=g.number_of_nodes(), edges=g.number_of_edges())
    return g, meta


def get_case_graph(workspace: str, target_app: str, board: str) -> Tuple[Optional[nx.DiGraph], Dict]:
    """目前 workspace 的圖 (設定檔有改過就重建)；GRAPH_RAG 關閉時回傳 (None, {...})。
    The graph for the current workspace (rebuilt after config-file changes); (None, {...}) when disabled."""
    if not GRAPH_RAG:
        return None, {"disabled": True}
    key = os.path.abspath(workspace)
    fp = _config_fingerprint(key)
    cached = _cache.get(key)
    if cached and cached[0] == fp:
        return cached[1], dict(cached[2], rebuilt=False)
    g, meta = _build(key, target_app, board)
    if meta.get("error"):
        logger.warning("Case graph unavailable: %s", meta["error"])
    else:
        logger.info("Built case knowledge graph: %s", {k: meta.get(k) for k in ("source", "nodes", "edges", "seconds")})
    _cache[key] = (fp, g, meta)
    return g, dict(meta, rebuilt=True)


def drop_case_graph(workspace: str) -> None:
    _cache.pop(os.path.abspath(workspace), None)
