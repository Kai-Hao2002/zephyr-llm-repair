# graph_rag/hybrid_retriever.py
"""
Hybrid RAG：在 graph_rag 既有的 Kconfig/DTS 圖遍歷檢索之外，補上「以文字
內容為基礎」的檢索——BM25 做關鍵字比對的第一階段篩選，語意 embedding
(Gemini) 對 BM25 篩出的候選做第二階段重排。

目標是解決 Phase 1 pilot (inject_runtime_fcb_nullcheck/
inject_compound_adc_emul_kconfig_dts) 實測發現的定位缺口：注入的 bug
常常在 target_app 之外、錯誤日誌裡也完全沒有路徑線索，既有的圖遍歷檢索
只認得 Kconfig 符號/DTS 節點「名稱」，對「哪個 C 檔案實作了這個子系統的
邏輯」沒有任何檢索能力。

Hybrid RAG: alongside graph_rag's existing Kconfig/DTS graph-traversal
retrieval, adds text-content-based retrieval — BM25 for a first-stage
keyword filter, semantic embeddings (Gemini) to re-rank the BM25
candidates in a second stage.

Aims to close the localization gap Phase 1 pilots surfaced empirically
(inject_runtime_fcb_nullcheck/inject_compound_adc_emul_kconfig_dts): the
injected bug is often outside target_app with zero path clues in the error
log, and graph-traversal retrieval only ever recognizes Kconfig-symbol/
DTS-node *names* — it has no way to answer "which C file implements this
subsystem's logic".
"""
import os
import re
from typing import List, Optional

from rank_bm25 import BM25Okapi
from langchain_google_genai import GoogleGenerativeAIEmbeddings

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INDEXABLE_EXTENSIONS = (".c", ".h", ".conf", ".dts", ".dtsi", ".overlay")

# text-embedding-004 回傳 404 (2026-08-31 實測，這個 API 版本已經不支援)，
# gemini-embedding-001 才是目前真的能用的 embedding 模型——先用
# embed_query() 實際呼叫一次確認過，不是憑文件猜的。
# text-embedding-004 returns a 404 (confirmed empirically 2026-08-31, not
# supported by this API version); gemini-embedding-001 is the embedding
# model that actually works — verified with a real embed_query() call
# first, not guessed from docs.
_EMBEDDING_MODEL = "models/gemini-embedding-001"


def _is_indexable_file(filename: str) -> bool:
    return (filename.endswith(_INDEXABLE_EXTENSIONS)
            or filename == "Kconfig" or filename.startswith("Kconfig."))


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class HybridRetriever:
    """
    對單一 workspace 的原始碼樹做一次 BM25 索引 (lazy build，之後同一個
    HybridRetriever 實例重複呼叫都直接沿用，不會每次迭代都重新掃過整棵
    3.7 萬檔案的樹)，retrieve() 時先用 BM25 抓出前 _BM25_CANDIDATES 名
    候選，再用語意 embedding 對這批候選重新排序，回傳前 top_k 個檔案的
    相對路徑。

    只對 BM25 篩出的候選 (預設最多 30 個) 做語意 embedding，不是對整棵樹
    每個檔案都 embedding——對 3.7 萬個檔案逐一呼叫 embedding API 在時間
    與成本上都不可行，也沒有必要：BM25 已經先把明顯不相關的檔案濾掉，
    語意層只需要在「已經是關鍵字相關」的候選裡挑出語意上最貼切的少數幾個
    ——這也是為什麼這裡刻意不用 faiss (雖然 requirements.txt 有裝)：
    faiss 是為了大規模向量集合的近似搜尋而存在，對只有 30 個候選向量的
    重排步驟來說，直接算 cosine similarity 更簡單、結果完全一樣精確，
    上 faiss 反而是不必要的複雜度。

    Builds a BM25 index over a single workspace's source tree once (lazy;
    reused on subsequent calls on the same HybridRetriever instance,
    so a repair loop's later iterations don't re-scan the whole ~38k-file
    tree each time); retrieve() first pulls the top _BM25_CANDIDATES via
    BM25, then re-ranks just that shortlist by semantic embedding
    similarity, returning the top_k resulting file paths.

    Only the BM25 shortlist (30 by default) gets semantically embedded,
    not every file in the tree — embedding all ~38k files one by one is
    neither time- nor cost-feasible, and isn't necessary: BM25 already
    filters out the obviously irrelevant files, so the semantic layer only
    needs to pick the most fitting few out of an already keyword-relevant
    shortlist. This is also why faiss is deliberately not used here
    (despite being in requirements.txt): faiss exists for approximate
    search over large vector collections — for re-ranking just 30
    candidate vectors, plain cosine similarity is simpler, exactly as
    accurate, and pulling in faiss would be unnecessary complexity.
    """

    _BM25_CANDIDATES = 30

    # 每個候選檔案送去 embedding 的內容上限——embedding API 對輸入長度
    # 有限制，而且對「這個檔案是否跟查詢語意相關」這個問題，開頭幾千字元
    # 通常已經足夠 (檔案標頭註解、include、函式簽名多半集中在前段)。
    # Cap on how much of a candidate file's content gets sent for
    # embedding — embedding APIs have input-length limits, and for "is
    # this file semantically relevant to the query" the first few thousand
    # characters are usually enough (header comments, includes, function
    # signatures tend to cluster near the top of a file).
    _EMBED_CONTENT_CHARS = 3000

    def __init__(self, workspace_path: str):
        self.workspace_path = os.path.abspath(workspace_path)
        self._file_paths: List[str] = []
        self._bm25: Optional[BM25Okapi] = None
        self._embeddings: Optional[GoogleGenerativeAIEmbeddings] = None

    def _ensure_index(self) -> None:
        if self._bm25 is not None:
            return
        corpus_tokens = []
        for root, dirs, files in os.walk(self.workspace_path):
            for file in files:
                if not _is_indexable_file(file):
                    continue
                full_path = os.path.join(root, file)
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                except Exception:
                    continue
                self._file_paths.append(os.path.relpath(full_path, self.workspace_path))
                corpus_tokens.append(_tokenize(content))
        self._bm25 = BM25Okapi(corpus_tokens) if corpus_tokens else None

    def _ensure_embeddings(self) -> GoogleGenerativeAIEmbeddings:
        if self._embeddings is None:
            self._embeddings = GoogleGenerativeAIEmbeddings(model=_EMBEDDING_MODEL)
        return self._embeddings

    def retrieve(self, query: str, top_k: int = 5) -> List[str]:
        """
        回傳跟 query (通常是 Analyzer 的 search_keywords 接成的字串) 最
        相關的前 top_k 個檔案相對路徑；索引是空的、query 是空字串，或
        BM25 完全沒有命中的候選時回傳空列表。
        """
        self._ensure_index()
        if self._bm25 is None or not query.strip():
            return []

        scores = self._bm25.get_scores(_tokenize(query))
        ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        candidate_indices = [i for i in ranked_indices if scores[i] > 0][: self._BM25_CANDIDATES]
        if not candidate_indices:
            return []

        # candidate_paths 本身已經依 BM25 分數排序 (index 0 = BM25 最高分)。
        # candidate_paths is already ordered by BM25 score (index 0 = top
        # BM25 score).
        candidate_paths = [self._file_paths[i] for i in candidate_indices]

        try:
            embeddings = self._ensure_embeddings()
            candidate_contents = []
            for rel_path in candidate_paths:
                try:
                    with open(os.path.join(self.workspace_path, rel_path), "r", encoding="utf-8", errors="ignore") as f:
                        candidate_contents.append(f.read()[: self._EMBED_CONTENT_CHARS])
                except Exception:
                    candidate_contents.append("")
            doc_vectors = embeddings.embed_documents(candidate_contents)
            query_vector = embeddings.embed_query(query)
            semantic_order = sorted(
                range(len(candidate_paths)),
                key=lambda i: _cosine_similarity(query_vector, doc_vectors[i]),
                reverse=True,
            )

            # 用 Reciprocal Rank Fusion 融合 BM25 名次與語意名次，而不是
            # 直接用語意分數整個蓋掉 BM25 排序——實測 (2026-08-31,
            # inject_runtime_fcb_nullcheck 案例) 純語意重排會把 BM25 排名
            # 第 7 (三十個候選裡數一數二高) 的 fcb_getnext.c 擠出
            # top_k=5，只因為它的語意 cosine 分數剛好比其他候選低一點；
            # RRF 讓「兩邊都排中等」的檔案比「其中一邊吊車尾、另一邊第一」
            # 更有機會出線，比較符合「BM25 已經先證明關鍵字相關」這個
            # 前提下語意層只是輔助排序、不是唯一依據的設計初衷。
            # Reciprocal Rank Fusion combines the BM25 and semantic
            # rankings instead of letting the semantic score alone
            # override BM25 entirely — empirically (2026-08-31,
            # inject_runtime_fcb_nullcheck) pure semantic re-ranking
            # pushed fcb_getnext.c (BM25 rank 7 out of thirty candidates,
            # a solidly high BM25 rank) out of a top_k=5 result, just
            # because its semantic cosine score happened to be a bit lower
            # than a few others. RRF gives a file that ranks decently on
            # *both* signals a better shot than one that's last on one
            # signal and first on the other — closer to the intended
            # design where BM25 already establishes keyword relevance and
            # the semantic layer is a secondary re-ranking signal, not the
            # sole arbiter.
            rrf_k = 60
            combined_scores = {}
            for bm25_rank, path in enumerate(candidate_paths):
                combined_scores[path] = combined_scores.get(path, 0.0) + 1.0 / (rrf_k + bm25_rank)
            for semantic_rank, candidate_idx in enumerate(semantic_order):
                path = candidate_paths[candidate_idx]
                combined_scores[path] = combined_scores.get(path, 0.0) + 1.0 / (rrf_k + semantic_rank)

            # candidate_paths 本身沒有重複 (每個 BM25 候選 index 對應唯一
            # 檔案)，直接排序，不需要先去重。
            # candidate_paths has no duplicates (each BM25 candidate index
            # maps to a distinct file), so sort it directly — no need to
            # dedupe first.
            final_order = sorted(candidate_paths, key=lambda p: combined_scores[p], reverse=True)
            return final_order[:top_k]
        except Exception:
            # 語意重排失敗 (例如 embedding API 暫時出錯) 時退回純 BM25
            # 排序，而不是讓整個檢索直接沒有任何結果——BM25 的排序本身
            # 已經是有用的訊號，只是少了語意層的再排序。
            # If semantic re-ranking fails (e.g. a transient embedding API
            # error), fall back to plain BM25 ranking rather than
            # returning nothing at all — BM25's own ranking is already a
            # useful signal, it just loses the semantic re-ranking layer.
            return candidate_paths[:top_k]
