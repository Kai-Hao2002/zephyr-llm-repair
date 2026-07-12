# graph_rag/build_graph.py
import networkx as nx
import yaml
import pickle
import os
from typing import Dict, Any

from .parsers.kconfig_parser import KconfigParser
from .parsers.dts_parser import DTSParser

class ZephyrGraphBuilder:
    def __init__(self):
        self.graph = nx.DiGraph()

    def build_graph(self, kconfig_path: str, dts_path: str, zephyr_base: str = "") -> nx.DiGraph:
        """
        利用 Parser 萃取資料並建構完整的知識圖譜。
        Extract data using Parsers and build the complete knowledge graph.
        """
        # 1. 解析與載入 Kconfig (Parse and ingest Kconfig)
        k_parser = KconfigParser(zephyr_base=zephyr_base)
        k_data = k_parser.parse(kconfig_path)
        
        for node_id, attrs in k_data["nodes"].items():
            self.graph.add_node(node_id, domain="Kconfig", **attrs)
            
        for source, target, relation in k_data["edges"]:
            self.graph.add_edge(source, target, relation=relation)

        # 2. 解析與載入 DTS (Parse and ingest DTS)
        d_parser = DTSParser()
        d_data = d_parser.parse(dts_path)
        
        for node_id, attrs in d_data["nodes"].items():
            self.graph.add_node(node_id, domain="DTS", **attrs)
            
        for source, target, relation in d_data["edges"]:
            self.graph.add_edge(source, target, relation=relation)

        return self.graph

    def save(self, filepath: str):
        with open(filepath, 'wb') as f:
            pickle.dump(self.graph, f)

    def load(self, filepath: str):
        with open(filepath, 'rb') as f:
            self.graph = pickle.load(f)

def subgraph_to_yaml_context(subgraph: nx.DiGraph) -> str:
    """
    將 NetworkX 子圖轉化為 LLM 友善的 YAML 格式。
    Convert a NetworkX subgraph into an LLM-friendly YAML format.
    """
    context_dict = {"nodes": {}, "relationships": []}

    for node, attrs in subgraph.nodes(data=True):
        clean_attrs = {k: v for k, v in attrs.items() if v}
        context_dict["nodes"][node] = clean_attrs

    for source, target, attrs in subgraph.edges(data=True):
        relation = attrs.get('relation', 'related_to')
        context_dict["relationships"].append(f"{source} --[{relation}]--> {target}")

    return yaml.dump(context_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)

# ===== CLI: 建置並快取知識圖譜 (Build and cache the knowledge graph) =====
# 執行方式 (Usage): python -m graph_rag.build_graph --kconfig $ZEPHYR_BASE/Kconfig --dts <board>.dts --zephyr-base $ZEPHYR_BASE
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parse Zephyr Kconfig/DTS and cache the resulting NetworkX graph.")
    parser.add_argument("--kconfig", required=True, help="Path to the root Kconfig file (e.g. $ZEPHYR_BASE/Kconfig)")
    parser.add_argument("--dts", required=True, help="Path to the board's .dts file")
    parser.add_argument("--zephyr-base", default=os.environ.get("ZEPHYR_BASE", ""), help="Zephyr source root (defaults to $ZEPHYR_BASE)")
    parser.add_argument("--output", default="zephyr_graph_cache.pkl", help="Output path for the cached graph")
    args = parser.parse_args()

    builder = ZephyrGraphBuilder()
    builder.build_graph(args.kconfig, args.dts, zephyr_base=args.zephyr_base)
    builder.save(args.output)
    print(f"✅ 圖譜已快取至 {args.output}: {builder.graph.number_of_nodes()} 個節點, {builder.graph.number_of_edges()} 條邊界。")