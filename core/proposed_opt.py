# core/proposed_opt.py
"""
Proposed 的架構優化開關與共用函式 (2026-10-31)：
- Patch 不合格不消耗輪次 (core/workflow.apply_patch_node)
- 上下文排序與配額 (agents/patch_expert.select_ordered_context)
- 記住無效的方向 (failure_signature；core/workflow.no_progress_fields)
開關 ZEPHYR_PROPOSED_OPT 預設關閉，記在 run_meta；關閉時 Proposed 的行為和之前完全相同。
這些規則只看 log、patch 和檔案位置，不含任何案例資訊。

Proposed architecture optimizations (2026-10-31): patches that fail the gate do not consume an iteration,
ordered context with quotas, and memory of ineffective directions. Switch ZEPHYR_PROPOSED_OPT is off by default
and recorded in run_meta; when off, Proposed behaves exactly as before. The rules look only at logs, patches
and file locations, never at individual cases.
"""
import hashlib
import os
import re

PROPOSED_OPT = os.environ.get("ZEPHYR_PROPOSED_OPT", "0").strip().lower() in ("1", "true", "yes")
# B3 只取套用閘門 (core/baseline_pipelines.run_b3)，讓「迭代」和優化版 Proposed 同義；排序內容和無進展記憶
# 屬於 Proposed 的架構，B3 沒有。B3 gets the apply gate only, so an iteration means the same thing as in
# optimized Proposed; ordered context and no-progress memory stay Proposed's own architecture.
B3_GATE = os.environ.get("ZEPHYR_B3_GATE", "0").strip().lower() in ("1", "true", "yes")
MAX_GATE_RETRIES_PER_ITERATION = 2
RETRIEVAL_CONTEXT_QUOTA_CHARS = 100_000

_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_NUMBER_RE = re.compile(r"\d+")


def failure_signature(log: str) -> str:
    """過濾後 log 的特徵：位址、行號、時間這類數字不算差異，行的順序也不算。
    Signature of a filtered log; addresses, line numbers, timings and line order do not count as differences."""
    lines = {_NUMBER_RE.sub("#", _HEX_RE.sub("HEX", line.strip())) for line in (log or "").splitlines()}
    return hashlib.sha256("\n".join(sorted(lines - {""})).encode()).hexdigest()[:16]
