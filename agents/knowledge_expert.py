# agents/knowledge_expert.py
import logging
import os
from typing import Dict, Any, List
from graph_rag.case_graph import get_case_graph
from graph_rag.retriever import GraphRetriever
from graph_rag.hybrid_retriever import HybridRetriever
from core.llm_retry import RetrievalDegradedError, record_fallback, strict_fallbacks

logger = logging.getLogger(__name__)

# Hybrid RAG 的開關 (2026-10-18，ablation「只保留 Graph RAG」用)：預設開啟，關閉時 Knowledge Expert 只做圖譜
# 檢索，retrieved_files 一律為空；run_meta 會記錄。Graph RAG 的開關見 graph_rag/case_graph.GRAPH_RAG。
# Hybrid RAG switch (2026-10-18, for the "Graph RAG only" ablation): on by default; when off the Knowledge
# Expert only does graph retrieval and retrieved_files is always empty; recorded in run_meta. The Graph RAG
# switch is graph_rag/case_graph.GRAPH_RAG.
HYBRID_RAG = os.environ.get("ZEPHYR_HYBRID_RAG", "1").strip().lower() not in ("0", "false", "no")

# 圖譜 (2026-10-18 起)：每筆案例從注入後的 workspace 現場建 (graph_rag/case_graph.py)。舊的全域快取
# zephyr_graph_cache.pkl 從來不存在；從原始樹建的 zephyr_graph_cache.full.pkl 含有注入前的正確內容，會洩漏
# 答案，所以不再使用全域快取。
# The graph (from 2026-10-18) is built per case from the injected workspace (graph_rag/case_graph.py). The old
# global zephyr_graph_cache.pkl never existed, and zephyr_graph_cache.full.pkl was built from the clean tree
# (pre-injection content, i.e. answers), so no global cache is used any more.

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

    Analyzer 的關鍵字只決定要不要做圖譜檢索 (原始設計的決策規則講的就是「要不要檢索圖譜」)；Hybrid RAG 每輪都跑，
    沒有關鍵字時改用最近一次過濾後的 build/run log 當查詢，和 B2 相同 (2026-10-19 使用者決定)。之前 Hybrid RAG
    跟著圖譜的開關，Analyzer 對 runtime 錯誤判斷「與硬體無關、不用查圖譜」時，連實作檔的檢索也一起被跳過。
    The Analyzer's keywords only decide whether graph retrieval runs (the original design's decision rules are
    about "is graph retrieval needed"); Hybrid RAG runs every iteration, querying with the most recent filtered
    build/run log when there are no keywords, like B2 (user decision 2026-10-19). Hybrid RAG used to follow the
    graph switch, so when the Analyzer judged a runtime error "not hardware-related, no graph needed" the search
    for implementation files was skipped as well.
    """
    logger.info("--- Starting Knowledge Expert ---")

    keywords = state.get("search_keywords", [])
    if not keywords:
        logger.info("No keywords received, skipping graph retrieval.")
        yaml_context = "No keywords provided for graph retrieval."
    else:
        logger.info(f"Retrieving graph context for keywords: {keywords}")
        graph, graph_meta = get_case_graph(state.get("workspace_path", ""), state.get("target_app", "."),
                                           state.get("board", "native_sim"))
        if graph is None:
            yaml_context = "Knowledge graph retrieval is disabled."
        elif graph.number_of_nodes() == 0:
            yaml_context = f"Knowledge graph unavailable for this workspace ({graph_meta.get('error', 'empty graph')})."
        else:
            # 執行子圖提取與 YAML 序列化 (Execute subgraph extraction and YAML serialization)
            yaml_context = GraphRetriever(graph).retrieve_context(keywords, radius=1)

    # 也可以透過 LLM 整理檢索結果，但為了節省 Token，直接傳遞 YAML 是最有效率的做法
    # We could also use an LLM to summarize the retrieved results, but to save tokens, passing YAML directly is the most efficient approach.

    retrieved_files: List[str] = []
    workspace_path = state.get("workspace_path", "")
    if workspace_path and HYBRID_RAG:
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
            # 沒有關鍵字時用最近一次過濾後的 build/run log (last_build_log，不含規格檔還原提醒和 patch 套用失敗訊息)。
            # Without keywords, query with the most recent filtered build/run log (last_build_log, which excludes the
            # restore reminder and patch-application failure text).
            query = " ".join(keywords) if keywords else (state.get("last_build_log") or state.get("current_error_log", ""))
            retrieved_files = hybrid.retrieve(query, top_k=8)
            if retrieved_files:
                logger.info(f"Hybrid RAG (BM25 + semantic) additionally retrieved candidate files: {retrieved_files}")
        except Exception as e:
            # Hybrid RAG 是既有圖譜檢索之外「額外」的輔助訊號，失敗時不該
            # 讓整個 Knowledge 節點掛掉——退回只用圖譜檢索的結果。
            # Hybrid RAG is an additional signal on top of existing graph
            # retrieval — a failure here shouldn't take down the whole
            # Knowledge node; fall back to graph-retrieval-only results.
            # 評測模式下不准退回 (見 core/llm_retry.py 的 strict_fallbacks)。
            # Not allowed in evaluation mode (see core/llm_retry.py's strict_fallbacks).
            if isinstance(e, RetrievalDegradedError):
                raise
            record_fallback("hybrid_retrieval", e)
            if strict_fallbacks():
                raise RetrievalDegradedError(f"Hybrid RAG retrieval failed and strict mode forbids skipping it: {e}") from e
            logger.warning(f"Hybrid RAG retrieval failed, skipping (does not affect the existing graph retrieval): {e}")

    result: Dict[str, Any] = {"retrieved_context": yaml_context, "retrieved_files": retrieved_files}

    # first_retrieval_files：只在這是本次案例「第一次」真正執行檢索時寫入
    # (None 代表從未觸發過；一旦被設成任何值，包含空列表 [] 本身，就永遠
    # 不再覆蓋)。RQ2 的 MRR/Recall@k/Top-k Accuracy 依確認過的方法論，只
    # 看第一次診斷觸發的檢索結果，不算閉環重試帶來的效果——見
    # core/state.py 的欄位說明。
    # first_retrieval_files: written only if this is the "first" time this
    # case's run has actually performed a retrieval (None means never
    # fired; once set to anything, including an empty list [] itself, it's
    # never overwritten again). Per the confirmed methodology, RQ2's
    # MRR/Recall@k/Top-k Accuracy only look at the first diagnosis's
    # retrieval result — see core/state.py's field docstring.
    if state.get("first_retrieval_files") is None:
        result["first_retrieval_files"] = retrieved_files

    return result