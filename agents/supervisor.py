# agents/supervisor.py
"""
Supervisor：中央調度，負責在 ApplyPatch/StaticCheck/Build 之後決定要不要
繼續重試、要退回哪一個節點，還是已經超過最大重試次數 (或已經修復成功)
該結束了；也負責 Context Compression——把每次迭代嘗試過的修補與結果記進
attempt_history，讓 Patch Expert 有跨迭代的記憶，避免在同一個已經證明
無效的方向上重複打轉。

Supervisor: central dispatch, deciding after ApplyPatch/StaticCheck/Build
whether to keep retrying, which node to bounce back to, or whether the max
retry count has been exceeded (or the repair already succeeded) and it's
time to stop; also owns Context Compression — recording each iteration's
attempted patch and outcome into attempt_history, giving the Patch Expert
cross-iteration memory so it doesn't circle on a direction already shown
not to work.
"""
from typing import Any, Dict

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from core.state import ZephyrAgentState

# 從第幾次迭代開始，把每次嘗試的結果壓成一句話再放進 attempt_history，
# 而不是繼續帶著較完整的錯誤日誌片段——迭代次數愈往後，愈需要控制
# attempt_history 餵進 Patch prompt 的成長速度，但仍然要讓 Patch 知道
# 「這個方向已經試過、沒用」。這是 write-time 的決定：只影響「從這次
# 迭代起新寫入的紀錄」，不會回頭壓縮更早、已經寫入的舊紀錄。
# From which iteration onward each attempt's outcome gets compressed to one
# sentence in attempt_history, instead of carrying a fuller log excerpt
# forward — later iterations need tighter control on how much
# attempt_history grows the Patch prompt, while still letting Patch know
# "this direction was already tried, didn't work". This is a write-time
# decision: it only affects entries newly written from this iteration on,
# never retroactively compresses older entries already written.
CONTEXT_COMPRESSION_THRESHOLD_ITERATION = 3

# 未壓縮時，每筆紀錄裡原始日誌片段最多保留的字元數——早期迭代仍然想要
# 比一句話更多的細節，但也不該無上限地把整段日誌塞進 attempt_history。
# When not compressed, the max characters of raw log excerpt kept per
# entry — early iterations still want more detail than one sentence, but
# shouldn't get an unbounded log dump into attempt_history either.
_RAW_LOG_EXCERPT_CHARS = 400


def _compress_outcome_to_one_sentence(outcome_description: str) -> str:
    """
    用便宜的模型 (gemini-2.5-flash) 把一次嘗試的內容與結果壓縮成剛好一句
    話，供 attempt_history 使用。壓縮本身失敗時 (例如 API 暫時出錯) 退回
    原始文字的前 200 字，而不是讓呼叫端節點因為這個非必要的加分步驟而
    中止——記錄一則稍微沒那麼精簡的歷史，好過完全記錄失敗讓整次迭代掛掉。

    Compresses one attempt's content and outcome to exactly one sentence
    via a cheap model (gemini-2.5-flash), for attempt_history. Falls back
    to the first 200 chars of the raw text if the compression call itself
    fails (e.g. a transient API error) rather than letting the calling
    node abort over this non-essential step — a slightly less compact
    history entry beats failing the whole iteration over logging.
    """
    try:
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
        prompt = ChatPromptTemplate.from_messages([
            ("system", "把以下這次修補嘗試的內容與結果，壓縮成剛好一句話的繁體中文摘要，"
                       "保留關鍵資訊 (改了哪個檔案、為什麼還是失敗)。只輸出這一句話，"
                       "不要加任何前綴、編號或其他說明文字。"),
            ("human", "{outcome}")
        ])
        chain = prompt | llm
        response = chain.invoke({"outcome": outcome_description})
        first_line = response.content.strip().splitlines()[0] if response.content.strip() else ""
        return first_line or outcome_description[:200]
    except Exception:
        return outcome_description[:200]


def record_attempt_outcome(current_iter: int, outcome_description: str) -> Dict[str, Any]:
    """
    建立這次迭代要附加進 attempt_history 的一筆紀錄，回傳一個只含
    attempt_history 這個 key 的 dict——設計成可以直接跟呼叫端節點
    (apply_patch_node/static_check_node/build_node) 自己的 return dict
    用 `**` 合併，不需要各節點自己重複判斷該不該壓縮。

    current_iter 由呼叫端直接傳入這次迭代的編號，而不是從 state 讀取：
    apply_patch_node 自己就是遞增 iterations 的地方，呼叫當下傳進來的
    state 引數還是遞增「之前」的舊值，用 state.get("iterations") 讀會
    差一次；直接接受呼叫端已經算好的 current_iter 就不會有這個
    off-by-one 風險，也讓三個呼叫點的行為完全一致。

    Builds the entry to append to attempt_history for this iteration and
    returns a dict containing only the attempt_history key — designed to
    be merged into the calling node's (apply_patch_node/static_check_node/
    build_node) own return dict with `**`, so no node has to re-implement
    the compress-or-not decision itself.

    current_iter is passed in directly by the caller rather than read from
    state: apply_patch_node is where iterations itself gets incremented,
    and the state argument available at that call site still holds the
    pre-increment value, so reading state.get("iterations") there would be
    off by one. Accepting an already-computed current_iter from the caller
    avoids that risk entirely and keeps all three call sites consistent.
    """
    if current_iter >= CONTEXT_COMPRESSION_THRESHOLD_ITERATION:
        body = _compress_outcome_to_one_sentence(outcome_description)
    else:
        body = outcome_description[:_RAW_LOG_EXCERPT_CHARS]
    return {"attempt_history": [f"[第 {current_iter} 次迭代] {body}"]}


def route_after_apply_patch(state: ZephyrAgentState) -> str:
    """
    ApplyPatch 套用失敗 (patch_format_error) 時：還有預算就退回 Analyzer
    重新診斷 (patch 格式本身有問題，不是建置/執行期錯誤，維持原本
    "重新走一次完整診斷" 的行為)；預算用完就結束。ApplyPatch 成功一律
    進 StaticCheck。

    When ApplyPatch fails (patch_format_error): if budget remains, go back
    to Analyzer for a fresh diagnosis (the patch's own formatting is
    broken, not a build/runtime error — keeps the original "start a full
    diagnosis pass over" behavior); finish if budget is exhausted.
    ApplyPatch succeeding always proceeds to StaticCheck.
    """
    if state.get("error_type") == "patch_format_error":
        return "finish" if state.get("final_status") == "failed_max_retries" else "retry_analyzer"
    return "goto_static_check"


def route_after_static_check(state: ZephyrAgentState) -> str:
    """
    StaticCheck 沒過 (static_check_failed) 時：還有預算就直接退回 Patch
    重新生成——proposal 說的 early-exit-back-to-GeneratePatch，跳過
    Analyzer 是因為錯誤已經很具體 (cppcheck/cmake 的輸出)，不需要 LLM
    再診斷一次；預算用完就結束。StaticCheck 通過一律進 Build。

    When StaticCheck fails (static_check_failed): if budget remains, bounce
    straight back to Patch to regenerate — the proposal's
    early-exit-back-to-GeneratePatch. Skipping Analyzer here is deliberate:
    the error is already concrete (cppcheck/cmake output), no LLM
    re-diagnosis needed; finish if budget is exhausted. StaticCheck passing
    always proceeds to Build.
    """
    if state.get("error_type") == "static_check_failed":
        return "finish" if state.get("final_status") == "failed_max_retries" else "retry_patch"
    return "goto_build"


def route_after_build(state: ZephyrAgentState) -> str:
    if state.get("error_type") == "success" or state.get("iterations", 0) >= state.get("max_iterations", 5):
        return "finish"
    return "retry"
