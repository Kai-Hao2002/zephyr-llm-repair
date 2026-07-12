# graph_rag/retriever.py
import networkx as nx
from typing import List, Set
from .build_graph import subgraph_to_yaml_context # 引入先前寫好的序列化函數

class GraphRetriever:
    def __init__(self, graph: nx.DiGraph):
        """
        初始化檢索器，載入完整的 Zephyr 知識圖譜。
        Initialize the retriever and load the complete Zephyr knowledge graph.
        """
        self.graph = graph

    def _find_matching_nodes(self, keywords: List[str]) -> Set[str]:
        """
        根據關鍵字尋找圖譜中的目標節點 (支援部分匹配/子字串匹配)。
        Find target nodes in the graph based on keywords (supports partial/substring matching).
        """
        matched_nodes = set()
        
        # 為了提升比對效率，可以先將 keywords 轉為小寫
        lower_keywords = [kw.lower() for kw in keywords]
        
        for node in self.graph.nodes():
            node_str = str(node).lower()
            for kw in lower_keywords:
                # 只要關鍵字出現在節點名稱中即視為命中 (例如: 'eth0' 能匹配 '&eth0')
                if kw in node_str:
                    matched_nodes.add(node)
                    break # 找到一個關鍵字匹配就跳下一個節點
                    
        return matched_nodes

    def retrieve_context(self, keywords: List[str], radius: int = 2) -> str:
        """
        提取包含目標節點及其相鄰節點的子圖，並輸出為 YAML 上下文。
        Extract a subgraph containing target nodes and their neighbors, outputting it as YAML context.
        
        :param keywords: Analyzer 提取出來的錯誤特徵 (如: ["CONFIG_NETWORKING", "eth0"])
        :param radius: 檢索深度 (預設 2 層)
        :return: LLM 友善的 YAML 格式字串
        """
        matched_nodes = self._find_matching_nodes(keywords)
        
        if not matched_nodes:
            return "No relevant Kconfig or DTS graph context found for the given keywords."

        subgraphs = []
        for node in matched_nodes:
            # undirected=True 非常重要：
            # 在有向圖中，我們不僅需要知道「該節點依賴誰(out-edges)」，
            # 也需要知道「誰依賴該節點(in-edges)」，才能看清完整的相依性衝突。
            ego = nx.ego_graph(self.graph, node, radius=radius, undirected=True)
            subgraphs.append(ego)

        # 將所有命中的子圖合併為單一有向圖
        combined_subgraph = nx.compose_all(subgraphs)
        
        # 呼叫 YAML 轉換函數
        yaml_context = subgraph_to_yaml_context(combined_subgraph)
        
        return yaml_context