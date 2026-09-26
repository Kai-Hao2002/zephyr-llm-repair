"""
每次迭代的軌跡紀錄 (iteration_log 條目裡的 "trajectory" 欄位)：這一輪的
patch 內容、StaticCheck 輸出、過濾後的 log、Analyzer 診斷等。之前
iteration_log 只記 compiled/resolved，失敗原因只能事後猜；有了這份紀錄，
失敗質性分類跟錯誤訊號品質分析才有實際依據。Proposed 跟 B1/B2/B3 共用
同一個 build_trajectory()，讓 results.json 裡的軌跡結構不分 pipeline 都一樣。

Per-iteration trajectory records (the "trajectory" field of an
iteration_log entry): this iteration's patch, StaticCheck output, filtered
log, Analyzer diagnosis, etc. iteration_log used to record only
compiled/resolved, leaving failure causes to guesswork; this gives the
failure taxonomy and error-signal-quality analyses real evidence. Proposed
and B1/B2/B3 share build_trajectory() so the trajectory shape in
results.json is the same regardless of pipeline.
"""
from typing import Any, Dict, Optional

# 每個文字欄位的上限，避免 B1 的整份檔案 patch 或超長 log 讓 results.json 爆掉。
# Per-text-field cap, so B1's whole-file patches or very long logs can't bloat results.json.
TRAJECTORY_TEXT_LIMIT = 20000


def clip_text(text: Optional[str], limit: int = TRAJECTORY_TEXT_LIMIT) -> Optional[str]:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated {len(text) - limit} chars]"


def build_trajectory(stage: str, status: str, *, patch: Any = None, applied_files=None,
                     apply_error: Optional[str] = None, static_check_passed: Optional[bool] = None,
                     static_check_log: Optional[str] = None, filtered_log: Optional[str] = None,
                     conflict_tag: Optional[str] = None, analyzer_diagnosis: Optional[Dict[str, Any]] = None,
                     retrieved_files=None) -> Dict[str, Any]:
    """
    stage：這一輪停在哪一步 ("apply" / "static_check" / "build" / "run")。
    patch：LLM 原始輸出的 SEARCH/REPLACE 文字；B1 則是 {"filepath", "content"}。
    其餘欄位不適用的 pipeline/階段留 None。

    stage: where this iteration stopped ("apply" / "static_check" / "build" / "run").
    patch: the LLM's raw SEARCH/REPLACE output; for B1, {"filepath", "content"}.
    Fields that don't apply to a pipeline/stage are left None.
    """
    if isinstance(patch, dict):
        patch = {k: clip_text(v) if isinstance(v, str) else v for k, v in patch.items()}
    else:
        patch = clip_text(patch)
    return {
        "stage": stage,
        "status": status,
        "patch": patch,
        "applied_files": applied_files,
        "apply_error": clip_text(apply_error),
        "static_check_passed": static_check_passed,
        "static_check_log": clip_text(static_check_log),
        "filtered_log": clip_text(filtered_log),
        "conflict_tag": conflict_tag,
        "analyzer_diagnosis": analyzer_diagnosis,
        "retrieved_files": retrieved_files,
    }
