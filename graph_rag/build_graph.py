# graph_rag/build_graph.py
import networkx as nx
import logging
from typing import List, Dict, Any

# 如果是在實際專案中，這裡會 import 您的 Parsers
# from parsers.kconfig_parser import KconfigParser
# from parsers.dts_parser import DTSParser

class ZephyrGraphRAG:
    """
    負責將 Kconfig 與 DTS 的解析結果整合為 NetworkX 有向圖，
    並提供針對 LLM 友善的子圖 (Subgraph) 檢索與上下文生成功能。
    
    Integrates Kconfig and DTS parsing results into a NetworkX directed graph,
    and provides LLM-friendly subgraph retrieval and context generation.
    """
    def __init__(self):
        logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
        self.logger = logging.getLogger(__name__)
        # 建立有向圖 (Directed Graph)
        self.graph = nx.DiGraph()

    def load_data(self, parsed_data: Dict[str, Any]):
        """
        將解析器 (Kconfig/DTS) 輸出的節點與邊界載入 NetworkX 中。
        Loads nodes and edges from parser outputs into NetworkX.
        """
        # 1. 載入節點 (Load nodes)
        for node_id, attributes in parsed_data.get("nodes", {}).items():
            self.graph.add_node(node_id, **attributes)
            
        # 2. 載入邊界 (Load edges)
        for edge in parsed_data.get("edges", []):
            src, dst, relation = edge
            # 在 NetworkX 中添加邊界，並帶有 relation 屬性
            self.graph.add_edge(src, dst, relation=relation)
            
        self.logger.info(f"圖譜已更新：目前共有 {self.graph.number_of_nodes()} 個節點, {self.graph.number_of_edges()} 條邊界。")

    def retrieve_context(self, keywords: List[str], depth: int = 1) -> str:
        """
        給定一組關鍵字 (節點 ID)，在圖中找出它們，並提取周圍 depth 層的鄰居節點。
        將這個「子圖 (Ego Graph)」格式化為 LLM 容易理解的純文字。
        
        Given keywords (Node IDs), finds them and extracts neighbors up to 'depth'.
        Formats this "Ego Graph" into LLM-friendly plain text.
        """
        if self.graph.number_of_nodes() == 0:
            return "Graph is empty."

        context_lines = []
        retrieved_nodes = set()

        for kw in keywords:
            # 尋找圖中是否有部分匹配或完全匹配的節點 ID
            # (例如輸入 "I2C"，希望能找到 "CONFIG_I2C" 或是 "DTS_i2c@40005400")
            matched_nodes = [n for n in self.graph.nodes() if kw.lower() in n.lower()]
            
            if not matched_nodes:
                context_lines.append(f"⚠️ 找不到與 '{kw}' 相關的圖譜節點 (No nodes found for '{kw}').")
                continue

            for target_node in matched_nodes:
                if target_node in retrieved_nodes:
                    continue # 避免重複擷取 (Avoid duplicate retrieval)

                # 使用 nx.ego_graph 取得目標節點及其周圍半徑為 depth 的子圖
                # radius=depth 表示往外擴張的層數；undirected=True 表示入邊和出邊都要考慮
                subgraph = nx.ego_graph(self.graph, target_node, radius=depth, undirected=True)
                retrieved_nodes.update(subgraph.nodes())

                context_lines.append(f"\n=== 檢索焦點 (Focus Node): {target_node} ===")
                
                # 印出該節點的屬性
                attrs = self.graph.nodes[target_node]
                for k, v in attrs.items():
                    if v and k not in ["selects", "depends_on"]: # 簡化輸出
                        context_lines.append(f"  [{k}]: {v}")

                # 提取與該節點直接相連的邊界 (出邊 Out-edges)
                out_edges = self.graph.out_edges(target_node, data=True)
                if out_edges:
                    context_lines.append("  [依賴 / 指向 (Depends / Points to)]:")
                    for src, dst, data in out_edges:
                        context_lines.append(f"    --({data.get('relation', 'related')})--> {dst}")

                # 提取指向該節點的邊界 (入邊 In-edges)
                in_edges = self.graph.in_edges(target_node, data=True)
                if in_edges:
                    context_lines.append("  [被依賴 / 被包含 (Depended by / Contained by)]:")
                    for src, dst, data in in_edges:
                        context_lines.append(f"    <--({data.get('relation', 'related')})-- {src}")

        if not context_lines:
            return "No relevant context found."

        return "\n".join(context_lines)

# ==========================================
# 測試區塊 (Testing Block)
# ==========================================
if __name__ == "__main__":
    # 建立系統實例 (Instantiate the system)
    rag_system = ZephyrGraphRAG()

    # 1. 模擬解析器輸出的假資料 (Mock parsed data from Kconfig and DTS)
    mock_kconfig_data = {
        "nodes": {
            "CONFIG_I2C": {"type": "bool", "help": "Enable I2C bus drivers."},
            "CONFIG_SENSOR_BME280": {"type": "bool", "help": "Enable BME280 sensor."},
            "CONFIG_PRINTK": {"type": "bool", "help": "Enable printk."}
        },
        "edges": [
            ("CONFIG_SENSOR_BME280", "CONFIG_I2C", "depends_on"),
            ("CONFIG_SENSOR_BME280", "CONFIG_PRINTK", "selects")
        ]
    }

    mock_dts_data = {
        "nodes": {
            "DTS_soc": {"type": "hardware_node", "compatible": ["simple-bus"]},
            "DTS_i2c@40005400": {"type": "hardware_node", "compatible": ["st,stm32-i2c-v2"], "status": "okay"},
            "DTS_bme280@76": {"type": "hardware_node", "compatible": ["bosch,bme280"]}
        },
        "edges": [
            ("DTS_soc", "DTS_i2c@40005400", "contains"),
            ("DTS_i2c@40005400", "DTS_bme280@76", "contains")
        ]
    }

    # 2. 將資料載入大池子中 (Load data into the massive graph)
    rag_system.load_data(mock_kconfig_data)
    rag_system.load_data(mock_dts_data)

    # 3. 模擬 LLM 遇到問題並進行檢索 (Simulate LLM retrieving context)
    # 情境：編譯日誌顯示 BME280 初始化失敗，我們讓系統搜尋相關關鍵字
    # Context: Log shows BME280 failed to initialize.
    search_keywords = ["BME280"]
    
    print(f"\n🔍 LLM 代理人正在檢索關鍵字: {search_keywords} (Depth=1)")
    context = rag_system.retrieve_context(search_keywords, depth=1)
    
    print("\n" + "="*50)
    print("傳遞給 LLM 的文本上下文 (Text Context provided to LLM):")
    print("="*50)
    print(context)