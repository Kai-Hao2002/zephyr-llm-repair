# agents/baselines.py
"""
B1/B2/B3 對照組的 LLM 呼叫邏輯 (見碩論提案 Table 2)——執行迴圈在
core/baseline_pipelines.py，evaluate.py 的 --pipeline 參數負責選擇要跑哪一個。

刻意跟 Proposed pipeline 的 agents/analyzer.py + agents/knowledge_expert.py +
agents/patch_expert.py 分開放，不是共用同一組節點加開關切換：三個 baseline
彼此、以及跟 Proposed 之間的差異 (有沒有 RAG、有沒有閉環重試、有沒有專門化
角色分工) 正是 Table 2 要測量的實驗變因本身，混在一起寫容易在某個分支不小心
讓 baseline 用上 Proposed 的部分機制 (例如不小心繼承跨迭代記憶)，讓對照組
不再乾淨。

The LLM-calling logic for the B1/B2/B3 ablation baselines (see the thesis
proposal's Table 2) — the execution loops live in
core/baseline_pipelines.py; evaluate.py's --pipeline flag selects which one
runs.

Deliberately kept separate from the Proposed pipeline's agents/analyzer.py +
agents/knowledge_expert.py + agents/patch_expert.py, rather than sharing one
set of nodes behind a flag: the differences between the three baselines and
Proposed (RAG or not, closed-loop retry or not, specialized role split or
not) are exactly the experimental variables Table 2 measures — combining the
code risks some branch accidentally inheriting a Proposed-only mechanism
(e.g. cross-iteration memory) and quietly un-isolating a baseline.
"""
from typing import Any, Dict, Tuple

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from core.llm_usage import extract_usage
from core.llm_provider import get_chat_model, get_model_name

# 三個 baseline 的修補生成都用 "pro" 角色 (見 core/llm_provider.py)——跟
# Proposed pipeline 的 Patch Expert 用同一個角色定位，維持公平比較 (三個
# baseline 跟 Proposed 的差異只該落在架構本身，不該連底層模型強弱都不同)。
# All three baselines' patch generation uses the "pro" role (see
# core/llm_provider.py) — the same role Proposed's Patch Expert uses, to
# keep the comparison fair (the difference between the baselines and
# Proposed should be architectural only, not also confounded by using a
# weaker/stronger underlying model).
_PATCH_ROLE = "pro"

_PATCH_FORMAT_SYSTEM_PROMPT = """You are an embedded software engineer specializing in fixing Zephyr RTOS code.
Based on the error log and the provided project source code, output the fix.

[STRICT FORMAT REQUIREMENTS]
You must modify files using the following SEARCH/REPLACE block format.
Never wrap the blocks in ``` code fences.
The code inside the SEARCH block must match the original file EXACTLY (including indentation).

<relative path of the file>
<<<<<<<< SEARCH
<original code to be replaced>
========
<new code after the fix>
>>>>>>>> REPLACE"""


class B1FullFilePatch(BaseModel):
    """B1 has no tools and no RAG: it guesses which file to change, and how, purely from
    the Zephyr knowledge it memorized during training. It has never seen the actual file
    contents in this workspace, so it cannot produce a SEARCH/REPLACE block that needs a
    character-exact match of the original text and can only output whole files."""
    filepath: str = Field(description="The file you think needs to be fixed, as a path relative to the Zephyr project root (e.g. 'samples/hello_world/src/main.c').")
    content: str = Field(description="The complete content of that file after the fix (the whole file, not a fragment).")


def b1_generate_full_file_patch(error_log: str) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """
    B1 Zero-Shot LLM (Table 2)："Raw log as input, no tools, no RAG"——
    這裡故意只傳 error_log 一個欄位進 prompt，不讀任何檔案、不做任何檢索、
    不給任何工具呼叫，逼模型完全依賴預訓練時記得的 Zephyr 原始碼知識。

    回傳 (patch 內容, 這次呼叫的 token 用量)。
    Returns (patch content, this call's token usage).
    """
    # timeout=300：見 agents/patch_expert.py 的同款說明——這三個 baseline
    # 呼叫都是修補生成 (context 可能不小)，沒有 timeout 的話一次卡住的
    # API 呼叫會讓整個 --pipeline b1/b2/b3 跑分無限期卡住 (2026-09-01 實測
    # 曾讓一次 B3 pilot 卡了 1 小時 44 分鐘)。
    # timeout=300: same rationale as agents/patch_expert.py — these three
    # baseline calls are all patch generation (context can be sizable);
    # without a timeout, one hung API call blocks an entire --pipeline
    # b1/b2/b3 run indefinitely (confirmed 2026-09-01: a B3 pilot hung for
    # 1h44m).
    llm = get_chat_model(role=_PATCH_ROLE, temperature=0, timeout=300)
    # include_raw=True：見 agents/analyzer.py 的同款說明，需要底層 AIMessage
    # 才拿得到 usage_metadata。
    # include_raw=True: see agents/analyzer.py — needs the underlying
    # AIMessage to access usage_metadata.
    structured_llm = llm.with_structured_output(B1FullFilePatch, include_raw=True)

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a Zephyr RTOS expert. You will only see a compile-time or runtime error log. "
                    "You cannot see the actual content of any file in this project, you get no retrieval data, "
                    "and you have no tools available. "
                    "Relying purely on your existing knowledge of the Zephyr source code, work out the file that is most likely at fault, "
                    "and directly output the complete content of that file as you believe it should be after the fix."),
        ("human", "Error log:\n{error_log}"),
    ])
    chain = prompt | structured_llm
    raw_output = chain.invoke({"error_log": error_log})
    result: B1FullFilePatch = raw_output["parsed"]
    usage_entry = extract_usage(raw_output["raw"], node="b1_zero_shot", model=get_model_name(_PATCH_ROLE))
    return {"filepath": result.filepath.strip(), "content": result.content}, usage_entry


def b2_generate_patch(error_log: str, project_files_content: str) -> Tuple[str, Dict[str, Any]]:
    """
    B2 Single Agent + Text RAG (Table 2)："Retrieval-augmented repair, no
    iterative build or runtime feedback"——單一 LLM 呼叫，只做修補，不做
    Proposed 那種獨立的診斷/檢索/修補角色分工。project_files_content 由
    core/baseline_pipelines.py 的 run_b2 組好傳入，已經包含 BM25-only
    (見 graph_rag/hybrid_retriever.py 的 bm25_only 參數) 額外檢索到的候選
    檔案內容——B2 本身不知道、也不需要知道哪些內容是「基礎上下文」、哪些是
    「RAG 額外找到的」，這正是 B2 跟 Proposed 的差異只該落在檢索模式
    (單模式 BM25 vs. Hybrid) 這一個變因上的設計意圖。

    回傳 (patch 內容, 這次呼叫的 token 用量)。
    Returns (patch content, this call's token usage).
    """
    # timeout=300：見 agents/patch_expert.py 的同款說明——這三個 baseline
    # 呼叫都是修補生成 (context 可能不小)，沒有 timeout 的話一次卡住的
    # API 呼叫會讓整個 --pipeline b1/b2/b3 跑分無限期卡住 (2026-09-01 實測
    # 曾讓一次 B3 pilot 卡了 1 小時 44 分鐘)。
    # timeout=300: same rationale as agents/patch_expert.py — these three
    # baseline calls are all patch generation (context can be sizable);
    # without a timeout, one hung API call blocks an entire --pipeline
    # b1/b2/b3 run indefinitely (confirmed 2026-09-01: a B3 pilot hung for
    # 1h44m).
    llm = get_chat_model(role=_PATCH_ROLE, temperature=0, timeout=300)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _PATCH_FORMAT_SYSTEM_PROMPT),
        ("human", "[Current project source and configuration file contents (including candidate files additionally found by keyword retrieval)]\n{project_files}\n\n"
                  "[Current error log]\n{error_log}\n\nPlease start generating the patch blocks:"),
    ])
    chain = prompt | llm
    response = chain.invoke({"error_log": error_log, "project_files": project_files_content})
    usage_entry = extract_usage(response, node="b2_single_agent_rag", model=get_model_name(_PATCH_ROLE))
    return response.content, usage_entry


def b3_generate_patch(error_log: str, project_files_content: str) -> Tuple[str, Dict[str, Any]]:
    """
    B3 Closed-Loop Single Agent (Table 2)："Build feedback, but no
    specialized multi-agent split"——單一 persona 身兼診斷與修補，closed
    loop 由 core/baseline_pipelines.py 的 run_b3 實作 (重複呼叫這個函式，
    每次把最新一輪的 error_log 帶進來)。故意不接受、不傳入跨迭代記憶
    (attempt_history)：提案原文把 Context Compression 明確歸屬 Supervisor
    Node，屬於多代理人分工的一部分，B3 定義是「no specialized multi-agent
    split」，給它記憶會讓「有沒有多代理人分工」這個變因跟「有沒有跨迭代
    記憶」混在一起，稀釋掉 Proposed 相對 B3 的優勢歸因。也不給任何檢索——
    B3 要對照的變因是「有沒有閉環重試」，不是「有沒有 RAG」。

    回傳 (patch 內容, 這次呼叫的 token 用量)。
    Returns (patch content, this call's token usage).
    """
    # timeout=300：見 agents/patch_expert.py 的同款說明——這三個 baseline
    # 呼叫都是修補生成 (context 可能不小)，沒有 timeout 的話一次卡住的
    # API 呼叫會讓整個 --pipeline b1/b2/b3 跑分無限期卡住 (2026-09-01 實測
    # 曾讓一次 B3 pilot 卡了 1 小時 44 分鐘)。
    # timeout=300: same rationale as agents/patch_expert.py — these three
    # baseline calls are all patch generation (context can be sizable);
    # without a timeout, one hung API call blocks an entire --pipeline
    # b1/b2/b3 run indefinitely (confirmed 2026-09-01: a B3 pilot hung for
    # 1h44m).
    llm = get_chat_model(role=_PATCH_ROLE, temperature=0, timeout=300)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _PATCH_FORMAT_SYSTEM_PROMPT),
        ("human", "[Current project source and configuration file contents]\n{project_files}\n\n"
                  "[Current error log]\n{error_log}\n\nPlease start generating the patch blocks:"),
    ])
    chain = prompt | llm
    response = chain.invoke({"error_log": error_log, "project_files": project_files_content})
    usage_entry = extract_usage(response, node="b3_closed_loop", model=get_model_name(_PATCH_ROLE))
    return response.content, usage_entry
