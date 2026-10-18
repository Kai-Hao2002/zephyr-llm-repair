# agents/agentless.py
"""
Agentless baseline 的 LLM 呼叫 (2026-10-18，依 Xia et al. 2024 "Agentless" 的三階段，針對 C/Kconfig/DTS 調整)：
目錄層定位 → 檔案層定位 → 元素層定位 (都是 temperature 0) → 產生 N 個候選 patch (第 1 個 temperature 0，
其餘 0.8)。流程與驗證在 core/agentless_pipeline.py。沒有 Zephyr 專用的檢索或知識、沒有迭代回饋、沒有角色分工。

LLM calls of the Agentless baseline (2026-10-18; the three phases of Xia et al. 2024 "Agentless", adapted to
C/Kconfig/DTS): directory-level -> file-level -> element-level localization (temperature 0), then N candidate
patches (the first at temperature 0, the rest at 0.8). Orchestration and validation: core/agentless_pipeline.py.
No Zephyr-specific retrieval or knowledge, no iterative feedback, no role split.
"""
import re
from typing import Any, Dict, List, Tuple

from langchain_core.prompts import ChatPromptTemplate

from agents.baselines import _PATCH_FORMAT_SYSTEM_PROMPT
from core.llm_provider import get_chat_model, get_model_name
from core.llm_retry import call_with_retry
from core.llm_usage import extract_usage

_ROLE = "pro"
SAMPLE_TEMPERATURE = 0.8


def _ask(system: str, human: str, values: Dict[str, str], node: str, temperature: float = 0.0) -> Tuple[str, Dict]:
    llm = get_chat_model(role=_ROLE, temperature=temperature, timeout=300)
    chain = ChatPromptTemplate.from_messages([("system", system), ("human", human)]) | llm
    response = call_with_retry(lambda: chain.invoke(values), what=node)
    return str(response.text), extract_usage(response, node=node, model=get_model_name(_ROLE))


def _paths_in(text: str, allowed: List[str], limit: int) -> List[str]:
    """回覆中依序出現、且在候選清單裡的路徑。Paths from the reply that are in the candidate list, in order."""
    allowed_set, out = set(allowed), []
    for line in text.splitlines():
        cand = re.sub(r"^\s*(?:\d+[.)]|[-*•>])\s*", "", line).strip().strip("`*").strip()
        cand = cand.split()[0] if cand else ""
        cand = cand.rstrip("/:,")
        if cand in allowed_set and cand not in out:
            out.append(cand)
        if len(out) >= limit:
            break
    return out


def localize_directories(error_log: str, tree: str, dirs: List[str], limit: int = 5) -> Tuple[List[str], Dict]:
    text, usage = _ask(
        "You are an expert Zephyr RTOS developer localizing the cause of a build or test failure.",
        "Below is a failure log from building and running a Zephyr test application, followed by the directory "
        "structure of the Zephyr repository (top two levels).\n\n[Failure log]\n{log}\n\n[Repository directories]\n"
        "{tree}\n\nList up to {limit} directories that most likely contain the file(s) that must be edited to fix "
        "the failure, most likely first. Output one directory path per line exactly as listed, nothing else.",
        {"log": error_log, "tree": tree, "limit": str(limit)}, node="agentless_dirs")
    return _paths_in(text, dirs, limit), usage


def localize_files(error_log: str, file_list: List[str], limit: int = 5) -> Tuple[List[str], Dict]:
    text, usage = _ask(
        "You are an expert Zephyr RTOS developer localizing the cause of a build or test failure.",
        "Below is a failure log and a list of candidate files of the Zephyr repository.\n\n[Failure log]\n{log}\n\n"
        "[Candidate files]\n{files}\n\nList up to {limit} files that most likely need to be edited to fix the "
        "failure, most likely first. Output one file path per line exactly as listed, nothing else.",
        {"log": error_log, "files": "\n".join(file_list), "limit": str(limit)}, node="agentless_files")
    return _paths_in(text, file_list, limit), usage


_LOC_RE = re.compile(r"^\s*([\w./\-+]+)\s*:\s*(\d+)\s*(?:-\s*(\d+))?")


def localize_elements(error_log: str, skeletons: str, files: List[str]) -> Tuple[Dict[str, List[Tuple[int, int]]], Dict]:
    text, usage = _ask(
        "You are an expert Zephyr RTOS developer localizing the cause of a build or test failure.",
        "Below is a failure log and the skeletons (definitions with line numbers) of the files suspected to "
        "contain the defect.\n\n[Failure log]\n{log}\n\n[File skeletons]\n{skeletons}\n\nIdentify the locations "
        "that need to be inspected or edited to fix the failure. Output one location per line as "
        "'<file path>: <start line>-<end line>' (use the line numbers shown; a single line may be given as "
        "'<file path>: <line>'), at most 10 lines, nothing else.",
        {"log": error_log, "skeletons": skeletons}, node="agentless_elements")
    locs: Dict[str, List[Tuple[int, int]]] = {}
    for line in text.splitlines():
        m = _LOC_RE.match(re.sub(r"^\s*(?:\d+[.)]|[-*•>])\s*", "", line.replace("`", "")).strip())
        if m and m.group(1) in files:
            start = int(m.group(2))
            end = int(m.group(3) or start)
            locs.setdefault(m.group(1), []).append((min(start, end), max(start, end)))
    return locs, usage


def generate_candidate(error_log: str, code_context: str, index: int) -> Tuple[str, Dict]:
    """第 index 個候選 patch (從 1 開始)：第 1 個 greedy (temperature 0)，其餘 temperature 0.8 取樣。驗證端
    需要時才產生下一個：各次取樣彼此獨立，結果的分布和一次產生 N 個相同，但不會浪費呼叫。
    Candidate number `index` (1-based): the first greedy (temperature 0), the rest sampled at 0.8. The validator
    asks for the next one only when needed; samples are independent, so this matches generating N up front
    without wasted calls."""
    human = ("[Relevant code segments (file path and starting line number are given in each header; do not "
             "include line numbers in SEARCH blocks)]\n{code}\n\n[Failure log]\n{log}\n\n"
             "Please start generating the patch blocks:")
    return _ask(_PATCH_FORMAT_SYSTEM_PROMPT, human, {"code": code_context, "log": error_log},
                node=f"agentless_repair_{index}", temperature=0.0 if index == 1 else SAMPLE_TEMPERATURE)
