# agents/knowledge_expert.py
import logging
from typing import Dict, Any, List
from graph_rag.build_graph import ZephyrGraphBuilder
from graph_rag.retriever import GraphRetriever
from graph_rag.hybrid_retriever import HybridRetriever

logger = logging.getLogger(__name__)

# 建議在全域或系統啟動時初始化並載入圖譜，避免在每次 LangGraph 迭代中重複建置
# It is recommended to initialize and load the graph globally or at system startup to avoid rebuilding it during every LangGraph iteration.
GRAPH_CACHE_PATH = "zephyr_graph_cache.pkl"
_global_retriever = None

# HybridRetriever 的 BM25 索引是針對單一 workspace_path 的原始碼樹建的，
# 不能像圖譜檢索那樣用單一全域單例——不同案例的 workspace_path 不同，且
# evaluate.py 在同一個行程裡可能依序處理好幾個不同案例 (--limit N)。用
# workspace_path 當 key，讓同一次修復迴圈內的多次迭代重複使用同一份索引
# (不用每次迭代都重新掃過整棵樹)，但不同案例之間不會互相汙染。
# HybridRetriever's BM25 index is built over a single workspace_path's
# source tree, so it can't be a single global singleton like the graph
# retriever — different cases have different workspace_path values, and
# evaluate.py may process several cases sequentially in one process
# (--limit N). Keyed by workspace_path so multiple iterations of the same
# repair loop reuse the same index (no re-scanning the whole tree every
# iteration), without different cases polluting each other.
_hybrid_retrievers: Dict[str, HybridRetriever] = {}


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


def get_hybrid_retriever(workspace_path: str) -> HybridRetriever:
    if workspace_path not in _hybrid_retrievers:
        _hybrid_retrievers[workspace_path] = HybridRetriever(workspace_path)
    return _hybrid_retrievers[workspace_path]


def knowledge_expert_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph 節點：根據 Analyzer 提取的關鍵字，檢索 Kconfig/DTS 依賴圖譜
    (既有的圖遍歷檢索)，並額外用 Hybrid RAG (BM25 + 語意 embedding) 對
    整個 workspace 的原始碼樹做以文字內容為基礎的檢索——後者是為了補上
    圖遍歷檢索的結構性盲區：圖譜只認得 Kconfig 符號/DTS 節點「名稱」，
    對「哪個 C 檔案實作了這個子系統的邏輯」沒有任何檢索能力 (見
    graph_rag/hybrid_retriever.py 開頭的說明)。

    LangGraph Node: retrieves the Kconfig/DTS dependency graph (existing
    graph-traversal retrieval) based on keywords the Analyzer extracted,
    plus additional Hybrid RAG (BM25 + semantic embedding) text-content
    retrieval over the whole workspace's source tree — the latter closes a
    structural blind spot in graph retrieval: the graph only recognizes
    Kconfig-symbol/DTS-node *names*, with no way to answer "which C file
    implements this subsystem's logic" (see graph_rag/hybrid_retriever.py's
    module docstring).
    """
    logger.info("--- 啟動 Knowledge Expert (Starting Knowledge Expert) ---")

    keywords = state.get("search_keywords", [])
    if not keywords:
        logger.info("沒有收到關鍵字，跳過圖譜檢索 (No keywords received, skipping graph retrieval).")
        return {"retrieved_context": "No keywords provided for graph retrieval.", "retrieved_files": []}

    logger.info(f"正在檢索以下關鍵字的圖譜上下文 (Retrieving graph context for keywords): {keywords}")

    retriever = get_retriever()

    # 執行子圖提取與 YAML 序列化 (Execute subgraph extraction and YAML serialization)
    yaml_context = retriever.retrieve_context(keywords, radius=2)

    # 也可以透過 LLM 整理檢索結果，但為了節省 Token，直接傳遞 YAML 是最有效率的做法
    # We could also use an LLM to summarize the retrieved results, but to save tokens, passing YAML directly is the most efficient approach.

    retrieved_files: List[str] = []
    workspace_path = state.get("workspace_path", "")
    if workspace_path:
        try:
            hybrid = get_hybrid_retriever(workspace_path)
            # top_k=8 而非更保守的 5——實測 (inject_runtime_fcb_nullcheck)
            # 真正該找到的檔案 (fcb_getnext.c) BM25 排名不錯但語意分數
            # 沒有特別突出，RRF 融合後排到第 8 名，top_k=5 會漏掉它。
            # top_k=8, not a more conservative 5 — empirically
            # (inject_runtime_fcb_nullcheck), the file that should actually
            # be found (fcb_getnext.c) ranks decently on BM25 but not
            # standout on semantic score; after RRF fusion it lands at
            # rank 8, which a top_k=5 would miss.
            retrieved_files = hybrid.retrieve(" ".join(keywords), top_k=8)
            if retrieved_files:
                logger.info(f"Hybrid RAG (BM25+語意) 額外檢索到候選檔案 (Hybrid RAG additionally retrieved candidate files): {retrieved_files}")
        except Exception as e:
            # Hybrid RAG 是既有圖譜檢索之外「額外」的輔助訊號，失敗時不該
            # 讓整個 Knowledge 節點掛掉——退回只用圖譜檢索的結果。
            # Hybrid RAG is an additional signal on top of existing graph
            # retrieval — a failure here shouldn't take down the whole
            # Knowledge node; fall back to graph-retrieval-only results.
            logger.warning(f"Hybrid RAG 檢索失敗，略過 (不影響既有的圖譜檢索) (Hybrid RAG retrieval failed, skipping — doesn't affect existing graph retrieval): {e}")

    return {"retrieved_context": yaml_context, "retrieved_files": retrieved_files}