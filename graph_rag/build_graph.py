# graph_rag/build_graph.py
import os
import re
import networkx as nx
import logging
from typing import List

logger = logging.getLogger(__name__)

class ZephyrGraphRAG:
    """
    負責掃描 Zephyr 專案的硬體 (.overlay) 與軟體 (prj.conf, Kconfig) 設定檔，
    將其建構為 NetworkX 記憶體圖譜，並提供子圖檢索功能。
    """
    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.graph = nx.DiGraph()
        
    def build_graph(self):
        """掃描專案目錄並建構圖譜"""
        self.graph.clear()
        
        # 1. 掃描 prj.conf (軟體配置)
        prj_conf_path = os.path.join(self.workspace_path, "prj.conf")
        if os.path.exists(prj_conf_path):
            with open(prj_conf_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("CONFIG_") and "=" in line:
                        key, val = line.split("=", 1)
                        self.graph.add_node(key, type="kconfig", value=val)
                        
        # 2. 掃描 app.overlay 或其他 .overlay 檔案 (硬體配置)
        # 使用輕量級 Regex 提取硬體節點與 compatible 屬性
        node_pattern = re.compile(r"([a-zA-Z0-9_]+)@[0-9a-fA-F]+\s*\{")
        comp_pattern = re.compile(r'compatible\s*=\s*"([^"]+)"')
        
        for root, _, files in os.walk(self.workspace_path):
            for file in files:
                if file.endswith(".overlay"):
                    filepath = os.path.join(root, file)
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                        
                        # 尋找所有硬體節點
                        current_nodes = node_pattern.findall(content)
                        compatibles = comp_pattern.findall(content)
                        
                        for idx, node_name in enumerate(current_nodes):
                            node_id = f"DTS_{node_name}"
                            comp_val = compatibles[idx] if idx < len(compatibles) else "unknown"
                            self.graph.add_node(node_id, type="dts_node", compatible=comp_val)
                            
                            # 建立軟硬體假想依賴 (例如 i2c 節點依賴 CONFIG_I2C)
                            if "i2c" in node_name.lower():
                                self.graph.add_edge(node_id, "CONFIG_I2C", relation="requires_config")
                            if "spi" in node_name.lower():
                                self.graph.add_edge(node_id, "CONFIG_SPI", relation="requires_config")

        logger.info(f"🕸️ 圖譜建構完成: {self.graph.number_of_nodes()} 個節點, {self.graph.number_of_edges()} 條邊界。")

    def retrieve_context(self, keywords: List[str], depth: int = 1) -> str:
        """
        給定關鍵字，透過 NetworkX ego_graph 擷取周圍的關聯節點。
        """
        if self.graph.number_of_nodes() == 0:
            return "圖譜為空或專案中沒有設定檔。"

        context_lines = []
        retrieved_nodes = set()

        for kw in keywords:
            # 模糊比對圖譜中的節點
            matched_nodes = [n for n in self.graph.nodes() if kw.lower() in n.lower()]
            
            for target_node in matched_nodes:
                if target_node in retrieved_nodes:
                    continue
                
                # 擷取子圖 (半徑為 depth)
                subgraph = nx.ego_graph(self.graph, target_node, radius=depth, undirected=True)
                retrieved_nodes.update(subgraph.nodes())

                context_lines.append(f"\n=== 節點焦點: {target_node} ===")
                attrs = self.graph.nodes[target_node]
                for k, v in attrs.items():
                    context_lines.append(f"  [{k}]: {v}")

                out_edges = self.graph.out_edges(target_node, data=True)
                if out_edges:
                    context_lines.append("  [依賴 ->]:")
                    for _, dst, data in out_edges:
                        context_lines.append(f"    --({data.get('relation')})--> {dst}")

        if not context_lines:
            return f"⚠️ 找不到與 '{keywords}' 相關的圖譜拓樸。"

        return "\n".join(context_lines)