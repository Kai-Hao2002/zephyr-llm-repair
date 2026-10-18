# graph_rag/build_case_graph.py
"""
每筆案例的 Kconfig/DTS 知識圖譜 (2026-10-18，依論文提案恢復 Graph RAG)。在 zephyr-sandbox 容器裡、
對「注入後的 workspace」執行 (掛在 /zephyrproject/zephyr)，所以圖反映的是 agent 實際面對的原始碼，
不會含有注入前的正確內容 (舊的 zephyr_graph_cache.full.pkl 是從原始樹建的，會洩漏答案，不再使用)。

1. 對目標 app 跑 `west build --cmake-only`，從 build.ninja 的 menuconfig 規則取得 Zephyr 給 Kconfig 的
   環境變數；目標 app 在 DTS 階段就失敗 (還沒到 Kconfig) 時，改用同一個 board 的 samples/hello_world
   (這些變數是 board 層級的；Kconfig.dts 的 DT_HAS_* 由 binding 產生，不依 DT 內容)。
2. 用 Zephyr 樹裡附的 kconfiglib 解析整棵 Kconfig 樹：每個符號記錄型別、說明、定義位置 (檔案:行號)、
   完整的 depends on 運算式 (含 '!')、select/imply 的對象。
3. 用 dtc 把該 build 產生的 zephyr.dts 轉成節點 (沿用 graph_rag/parsers/dts_parser.py)。
輸出 JSON {"nodes": {...}, "edges": [...], "meta": {...}}，由 host 端轉成 NetworkX 圖。

Per-case Kconfig/DTS knowledge graph (2026-10-18, Graph RAG restored as in the thesis proposal). Runs in the
zephyr-sandbox container against the injected workspace (mounted at /zephyrproject/zephyr), so the graph
reflects the source the agent actually faces and never the pre-injection content (the old
zephyr_graph_cache.full.pkl was built from the clean tree and would leak answers; it is no longer used).

Usage (inside the container): python3 build_case_graph.py --app <target_app> --board <board> --out /out/graph.json
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys

ZEPHYR = "/zephyrproject/zephyr"
_HELP_CHARS = 600


def cmake_only(app_dir: str, board: str, build_dir: str) -> bool:
    r = subprocess.run(["west", "build", "-b", board, "-d", build_dir, "-p", "always", "--cmake-only", app_dir],
                       capture_output=True, text=True, timeout=900)
    return os.path.exists(os.path.join(build_dir, "build.ninja"))


def kconfig_env(build_dir: str):
    """build.ninja 裡 menuconfig 規則的 `cmake -E env K=V ... python menuconfig.py <root Kconfig>`。"""
    text = open(os.path.join(build_dir, "build.ninja"), encoding="utf-8", errors="ignore").read()
    for line in text.splitlines():
        if "menuconfig.py" in line and "-E env" in line:
            parts = shlex.split(line.split("-E env", 1)[1])
            env = {}
            for p in parts:
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", p):
                    k, v = p.split("=", 1)
                    env[k] = v
                elif p.endswith("menuconfig.py"):
                    continue
            root = next((p for p in reversed(parts) if p.endswith("Kconfig") or "Kconfig" in os.path.basename(p)), None)
            return env, root
    return None, None


def kconfig_nodes(env, root):
    os.environ.update(env)
    sys.path.insert(0, os.path.join(ZEPHYR, "scripts", "kconfig"))
    import kconfiglib  # Zephyr's bundled copy
    kconf = kconfiglib.Kconfig(root, warn_to_stderr=False)
    nodes, edges = {}, []
    for sym in kconf.unique_defined_syms:
        if not sym.name:
            continue
        nid = f"CONFIG_{sym.name}"
        locs = [f"{os.path.relpath(n.filename, ZEPHYR) if os.path.isabs(n.filename) else n.filename}:{n.linenr}"
                for n in sym.nodes]
        help_text = next((n.help for n in sym.nodes if n.help), "") or ""
        deps = sorted({s.name for s in kconfiglib.expr_items(sym.direct_dep)
                       if isinstance(s, kconfiglib.Symbol) and s.name and not s.is_constant})
        selects = [t[0].name for t in sym.selects if isinstance(t[0], kconfiglib.Symbol)]
        implies = [t[0].name for t in sym.implies if isinstance(t[0], kconfiglib.Symbol)]
        nodes[nid] = {
            "type": kconfiglib.TYPE_TO_STR[sym.type],
            "defined_at": locs[:3],
            "depends_on_expr": kconfiglib.expr_str(sym.direct_dep),
            "selects": [f"CONFIG_{s}" for s in selects],
            "implies": [f"CONFIG_{s}" for s in implies],
            "help": help_text.strip()[:_HELP_CHARS],
        }
        edges += [(nid, f"CONFIG_{d}", "depends_on") for d in deps]
        edges += [(nid, f"CONFIG_{s}", "selects") for s in selects]
        edges += [(nid, f"CONFIG_{s}", "implies") for s in implies]
    return nodes, edges


def dts_nodes(dts_path, repo_dir):
    sys.path.insert(0, repo_dir)
    from graph_rag.parsers.dts_parser import DTSParser
    data = DTSParser().parse(dts_path)
    return data["nodes"], [tuple(e) for e in data["edges"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--board", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default="/repo", help="this repository, mounted read-only (for dts_parser)")
    args = ap.parse_args()
    meta = {"board": args.board, "source": None}
    build_dir = "/tmp/case_graph_build"
    env = root = None
    for app, label in ((args.app, "target_app"), ("samples/hello_world", "hello_world")):
        if cmake_only(os.path.join(ZEPHYR, app), args.board, build_dir):
            env, root = kconfig_env(build_dir)
            if env:
                meta["source"] = label
                break
    if not env:
        json.dump({"nodes": {}, "edges": [], "meta": dict(meta, error="no Kconfig environment")}, open(args.out, "w"))
        return
    nodes, edges = kconfig_nodes(env, root)
    meta.update(kconfig_root=root, kconfig_symbols=len(nodes))
    dts = os.path.join(build_dir, "zephyr", "zephyr.dts")
    if os.path.exists(dts):
        d_nodes, d_edges = dts_nodes(dts, args.repo)
        for k, v in d_nodes.items():
            nodes[k] = dict(v, domain="DTS")
        edges += d_edges
        meta["dts_nodes"] = len(d_nodes)
    for v in nodes.values():
        v.setdefault("domain", "Kconfig")
    json.dump({"nodes": nodes, "edges": edges, "meta": meta}, open(args.out, "w"))


if __name__ == "__main__":
    main()
