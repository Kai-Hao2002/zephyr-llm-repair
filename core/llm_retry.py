"""
LLM/embedding API 暫時性錯誤 (503/429/逾時/斷線) 的退避重試。

供應商 SDK 自己也會重試 (google-genai 預設約 1/2/4/8/16 秒的短間隔)，但
503 過載常常持續好幾分鐘，短重試用完就讓整個案例變成 error (上次 80 次
執行有 4 次，例如 "The read operation timed out")。這裡在 SDK 之外再包一層
長間隔退避 (30s 起跳，最多 5 次重試、約 15 分鐘)，只重試暫時性錯誤；4xx
參數錯誤、結構化輸出解析失敗這類確定性錯誤照樣直接拋出。

每次重試、以及「悄悄退回」的降級 (embedding 失敗退回純 BM25、Supervisor
壓縮失敗退回截斷) 都記成事件，evaluate.py 逐案例寫進 results.json 的
api_events，讓暫時性錯誤對結果的影響看得到，而不是默默混進數字裡。

Backoff retry for transient LLM/embedding API errors (503/429/timeouts/
disconnects).

Provider SDKs retry on their own (google-genai defaults to short ~1/2/4/8/16s
waits), but 503 overloads often last minutes; once those short retries run
out the whole case becomes an error (4 out of 80 runs last time, e.g. "The
read operation timed out"). This adds a long-interval backoff layer on top
(starting at 30s, up to 5 retries, ~15 minutes), retrying only transient
errors; deterministic errors (4xx bad requests, structured-output parse
failures) still raise immediately.

Each retry, and each silent degradation (embedding failure falling back to
plain BM25, Supervisor compression failure falling back to truncation), is
recorded as an event; evaluate.py writes them per case into results.json's
api_events, so transient errors' effect on results is visible instead of
silently mixed into the numbers.
"""
import logging
import random
import re
import time
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_BACKOFF_SECONDS = (30, 60, 120, 240, 480)
_TRANSIENT_HTTP_CODES = frozenset({408, 429, 500, 502, 503, 504, 529})
_TRANSIENT_MESSAGE_RE = re.compile(
    r"\b(408|429|500|502|503|504|529)\b|UNAVAILABLE|RESOURCE_EXHAUSTED|DEADLINE_EXCEEDED|overloaded|"
    r"timed out|timeout|Server disconnected|Connection reset|Connection aborted|Connection refused",
    re.IGNORECASE,
)

_events: List[Dict[str, Any]] = []


def _transient_exception_types() -> tuple:
    types = [TimeoutError, ConnectionError]
    try:
        import httpx
        types += [httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError]
    except ImportError:  # pragma: no cover
        pass
    for module_name in ("anthropic", "openai"):
        try:
            module = __import__(module_name)
            types.append(module.APIConnectionError)  # APITimeoutError is a subclass
        except (ImportError, AttributeError):  # pragma: no cover
            pass
    return tuple(types)


_TRANSIENT_TYPES = _transient_exception_types()


# 每日配額用完 (例如 embedding 免費層級的 EmbedContentRequestsPerDay...
# PerModel-FreeTier，2026-09-26 實測) 雖然也是 429，但要到隔天才恢復，
# 退避 15 分鐘沒有意義，直接視為非暫時性錯誤。
# An exhausted daily quota (e.g. the embedding free tier's
# EmbedContentRequestsPerDay...PerModel-FreeTier, observed 2026-09-26) is
# also a 429 but only resets the next day; a 15-minute backoff is pointless,
# so it counts as non-transient.
_DAILY_QUOTA_RE = re.compile(r"PerDay", re.IGNORECASE)


def is_transient(exc: BaseException) -> bool:
    """沿著 __cause__/__context__ 往下找：LangChain 常把 SDK 例外再包一層。
    Walks __cause__/__context__, since LangChain often re-wraps SDK exceptions."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if _DAILY_QUOTA_RE.search(str(exc)):
            return False
        if isinstance(exc, _TRANSIENT_TYPES):
            return True
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if isinstance(code, int) and code in _TRANSIENT_HTTP_CODES:
            return True
        if _TRANSIENT_MESSAGE_RE.search(str(exc)):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def call_with_retry(fn: Callable[[], Any], *, what: str) -> Any:
    """呼叫 fn()；遇到暫時性錯誤就退避重試，用完次數或非暫時性錯誤則原樣拋出。
    Calls fn(); backs off and retries on transient errors, re-raising as-is
    once retries run out or on a non-transient error."""
    for attempt in range(len(_BACKOFF_SECONDS) + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == len(_BACKOFF_SECONDS) or not is_transient(e):
                raise
            wait = _BACKOFF_SECONDS[attempt] * random.uniform(0.8, 1.2)
            logger.warning(f"[{what}] Transient API error ({_describe(e)}); retry {attempt + 1}/{len(_BACKOFF_SECONDS)} in {wait:.0f}s")
            _events.append({"kind": "retry", "what": what, "attempt": attempt + 1,
                            "error": _describe(e), "wait_seconds": round(wait)})
            time.sleep(wait)


def record_fallback(what: str, exc: BaseException) -> None:
    """記下一次「失敗後悄悄退回」的降級。Records a silent fall-back-on-failure degradation."""
    logger.warning(f"[{what}] Falling back after error: {_describe(exc)}")
    _events.append({"kind": "fallback", "what": what, "error": _describe(exc), "transient": is_transient(exc)})


def pop_events() -> List[Dict[str, Any]]:
    """取出並清空目前累積的事件 (evaluate.py 每個案例開始與結束時呼叫)。
    Returns and clears accumulated events (called by evaluate.py at each case's start and end)."""
    events = list(_events)
    _events.clear()
    return events
