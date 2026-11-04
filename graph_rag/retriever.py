# graph_rag/retriever.py
import re
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

    # 真實 Zephyr 圖譜有數萬個節點，不設上限的子字串比對 + 多層展開會回傳數 MB 的 YAML，
    # 遠超過 LLM 可用的上下文，所以每個環節都要有上限。
    # On a real Zephyr graph (tens of thousands of nodes), unbounded substring matching
    # plus multi-hop expansion returns megabytes of YAML — every stage needs a cap.
    MAX_NODES_PER_KEYWORD = 3
    MAX_MATCHED_NODES = 10
    MAX_NEIGHBORS_PER_NODE = 10
    MAX_HELP_CHARS = 240
    MAX_LIST_ITEMS = 8
    MAX_CONTEXT_CHARS = 8000

    @staticmethod
    def _tokens(name: str) -> List[str]:
        name = re.sub(r"^(CONFIG_|DTS_)", "", str(name), flags=re.IGNORECASE)
        return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]

    def _score(self, node: str, kw: str) -> int:
        """3=完整名稱相符, 2=關鍵字是節點名稱的完整 token 序列, 1=子字串, 0=不相符。"""
        kw_tokens = self._tokens(kw)
        if not kw_tokens:
            return 0
        node_tokens = self._tokens(node)
        if node_tokens == kw_tokens:
            return 3
        n = len(kw_tokens)
        if any(node_tokens[i:i + n] == kw_tokens for i in range(len(node_tokens) - n + 1)):
            return 2
        if kw.lower() in str(node).lower():
            return 1
        return 0

    def _find_matching_nodes(self, keywords: List[str]) -> List[str]:
        """
        依比對品質排序後，每個關鍵字最多取 MAX_NODES_PER_KEYWORD 個節點、總共最多
        MAX_MATCHED_NODES 個。同分時名稱越短越優先 (通常是子系統本身的符號，例如 FCB
        優先於 FCB_FLASH_XXX)。
        """
        picked: List[str] = []
        seen = set()
        per_kw = []
        for kw in keywords:
            scored = []
            for node in self.graph.nodes():
                sc = self._score(node, kw)
                if sc:
                    scored.append((-sc, len(str(node)), str(node)))
            scored.sort()
            per_kw.append([n for _, _, n in scored[: self.MAX_NODES_PER_KEYWORD]])
        # 輪流從各關鍵字取，避免前面的關鍵字佔滿名額
        for rank in range(self.MAX_NODES_PER_KEYWORD):
            for names in per_kw:
                if rank < len(names) and names[rank] not in seen and len(picked) < self.MAX_MATCHED_NODES:
                    seen.add(names[rank])
                    picked.append(names[rank])
        return picked

    def _neighbors(self, node: str) -> List[str]:
        # 有向圖需要同時看「該節點依賴誰」與「誰依賴該節點」
        nbrs = set(self.graph.successors(node)) | set(self.graph.predecessors(node))
        nbrs.discard(node)
        return sorted(nbrs, key=lambda n: (len(str(n)), str(n)))[: self.MAX_NEIGHBORS_PER_NODE]

    def _compact(self, subgraph: nx.DiGraph) -> nx.DiGraph:
        out = nx.DiGraph()
        for node, attrs in subgraph.nodes(data=True):
            a = dict(attrs)
            help_text = a.get("help")
            if isinstance(help_text, str) and len(help_text) > self.MAX_HELP_CHARS:
                a["help"] = help_text[: self.MAX_HELP_CHARS] + "..."
            for key, val in list(a.items()):
                if isinstance(val, list) and len(val) > self.MAX_LIST_ITEMS:
                    a[key] = val[: self.MAX_LIST_ITEMS] + [f"...(+{len(val) - self.MAX_LIST_ITEMS} more)"]
            out.add_node(node, **a)
        for u, v, attrs in subgraph.edges(data=True):
            out.add_edge(u, v, **attrs)
        return out

    def retrieve_context(self, keywords: List[str], radius: int = 1) -> str:
        """
        提取命中節點及其直接鄰居 (1 hop) 的子圖，輸出為有長度上限的 YAML 上下文。
        radius 只保留為相容參數；為了控制輸出大小，實際只展開 1 層。

        :param keywords: Analyzer 提取出來的錯誤特徵 (如: ["CONFIG_NETWORKING", "eth0"])
        """
        matched = self._find_matching_nodes(keywords)
        if not matched:
            return "No relevant Kconfig or DTS graph context found for the given keywords."

        keep = set(matched)
        for node in matched:
            keep.update(self._neighbors(node))
        subgraph = self._compact(self.graph.subgraph(keep))
        yaml_context = subgraph_to_yaml_context(subgraph)
        if len(yaml_context) > self.MAX_CONTEXT_CHARS:
            yaml_context = yaml_context[: self.MAX_CONTEXT_CHARS] + "\n# ...(truncated)\n"
        return yaml_context
