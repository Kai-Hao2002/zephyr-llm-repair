# agents/knowledge_expert.py
import logging
from typing import Dict, Any
from graph_rag.build_graph import ZephyrGraphBuilder
from graph_rag.retriever import GraphRetriever

logger = logging.getLogger(__name__)

# 建議在全域或系統啟動時初始化並載入圖譜，避免在每次 LangGraph 迭代中重複建置
# It is recommended to initialize and load the graph globally or at system startup to avoid rebuilding it during every LangGraph iteration.
GRAPH_CACHE_PATH = "zephyr_graph_cache.pkl"
_global_retriever = None

def get_retriever() -> GraphRetriever:
    global _global_retriever
    if _global_retriever is None:
        builder = ZephyrGraphBuilder()
        try:
            builder.load(GRAPH_CACHE_PATH)
            logger.info("成功載入圖譜快取 (Successfully loaded graph cache).")
        except FileNotFoundError:
            logger.warning("找不到圖譜快取，請先執行 build_graph.py 進行建構 (Graph cache not found, please run build_graph.py to construct it first).")
            # 這裡可以加入動態建置邏輯，或直接拋出例外
            # dynamic build logic can be added here, or raise an exception
        _global_retriever = GraphRetriever(builder.graph)
    return _global_retriever


def knowledge_expert_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph 節點：根據 Analyzer 提取的關鍵字，檢索 Kconfig/DTS 依賴圖譜。
    LangGraph Node: Retrieves Kconfig/DTS dependency graph based on keywords extracted by the Analyzer.
    """
    logger.info("--- 啟動 Knowledge Expert (Starting Knowledge Expert) ---")
    
    keywords = state.get("search_keywords", [])
    if not keywords:
        logger.info("沒有收到關鍵字，跳過圖譜檢索 (No keywords received, skipping graph retrieval).")
        return {"retrieved_context": "No keywords provided for graph retrieval."}

    logger.info(f"正在檢索以下關鍵字的圖譜上下文 (Retrieving graph context for keywords): {keywords}")

    retriever = get_retriever()

    # 執行子圖提取與 YAML 序列化 (Execute subgraph extraction and YAML serialization)
    yaml_context = retriever.retrieve_context(keywords, radius=2)

    # 也可以透過 LLM 整理檢索結果，但為了節省 Token，直接傳遞 YAML 是最有效率的做法
    # We could also use an LLM to summarize the retrieved results, but to save tokens, passing YAML directly is the most efficient approach.

    return {"retrieved_context": yaml_context}