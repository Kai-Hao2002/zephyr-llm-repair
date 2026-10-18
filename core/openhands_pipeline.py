# core/openhands_pipeline.py
"""
OpenHands baseline (2026-10-18，教授建議)：通用的 coding agent，可以自己執行 shell 指令、編輯檔案、
跑 west build 與 QEMU。和其他 pipeline 一樣：同一份準備好的 workspace (沒有 .git/.orig)、同一份
初始錯誤 log、同一段規格規則說明，評分也用同一個 evaluate_repair_attempt (受保護檔案還原、嚴格
規格檢查、target_test 必須 PASS)。不提供任何注入資訊，也沒有瀏覽器工具。

OpenHands SDK 需要 Python >= 3.12，所以 agent 由 tools/openhands_runner.py 在獨立的 venv
(ZEPHYR_OH_PYTHON，預設 venv_oh/bin/python) 執行；這裡負責組任務、呼叫、記錄改過的檔案與評分。
模型跟著 core/llm_provider (ZEPHYR_GEMINI_MODEL 等) 走，轉成 LiteLLM 的 "provider/model" 字串，
所以換模型不用改這裡。

OpenHands baseline (2026-10-18, advisor's suggestion): a general coding agent that runs shell commands,
edits files and can run west build / QEMU itself. Same prepared workspace (no .git/.orig), same initial
log, same spec-rule note and the same evaluate_repair_attempt grading as every other pipeline; no
injection information and no browser. The SDK needs Python >= 3.12, so tools/openhands_runner.py runs in
its own venv; this module builds the task, invokes it, records changed files and grades. The model
follows core/llm_provider (ZEPHYR_GEMINI_MODEL etc.) as a LiteLLM "provider/model" string.
"""
import json
import os
import subprocess
import tempfile
from typing import Any, Dict, List, Tuple

from agents.spec_rule import SPEC_RULE
from core.llm_provider import get_model_name, get_provider
from core.protected_files import pop_restored
from core.trajectory import build_trajectory
from core.workflow import evaluate_repair_attempt

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OH_PYTHON = os.environ.get("ZEPHYR_OH_PYTHON", os.path.join(_REPO, "venv_oh", "bin", "python"))
# zephyr-oh-agent:1.55.1 = OpenHands agent-server (SDK v1.55.1) 疊在 zephyr-sandbox 上；-z1 再加一層：清掉
# agent-server 設的 LD_LIBRARY_PATH (會讓 SDK 的 dtc crash)，並提供 zephyr-run (build + run，看到
# "PROJECT EXECUTION ..." 就停掉 QEMU)。建置方式見 tools/openhands_image/README.md。
# zephyr-oh-agent:1.55.1 = the OpenHands agent-server (SDK v1.55.1) on top of zephyr-sandbox; -z1 adds a layer
# that clears the agent-server's LD_LIBRARY_PATH (it crashes the SDK's dtc) and provides zephyr-run (build + run,
# stopping QEMU at "PROJECT EXECUTION ..."). See tools/openhands_image/README.md for how they are built.
OH_IMAGE = os.environ.get("ZEPHYR_OH_IMAGE", "zephyr-oh-agent:1.55.1-z1")
# 每筆案例的預算 (2026-10-18 建議值)：50 步、40 分鐘。其他 pipeline 最多 5 次 build；OpenHands 可以
# 自己 build 任意次，這兩個上限讓它的成本可控。
# Per-case budget (proposed 2026-10-18): 50 steps, 40 minutes. Other pipelines get at most 5 builds;
# OpenHands may build any number of times, so these caps bound its cost.
OH_MAX_STEPS = int(os.environ.get("ZEPHYR_OH_MAX_STEPS", "50"))
OH_TIMEOUT_MIN = float(os.environ.get("ZEPHYR_OH_TIMEOUT_MIN", "40"))

_KEY_ENV = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
_SKIP_DIRS = {".cache", "__pycache__", ".git"}
_MAX_RECORDED_FILES = 50
_MAX_RECORDED_BYTES = 300_000


def litellm_model() -> str:
    return f"{get_provider()}/{get_model_name('pro')}"


def build_task(state: Dict[str, Any]) -> str:
    app, board = state["target_app"], state["board"]
    return (
        "You are fixing a defect in a Zephyr RTOS project.\n\n"
        f"The Zephyr source tree is at /zephyrproject/zephyr. The test application is {app} and the board is "
        f"{board}. Build and run the test suite with:\n\n"
        f"    cd /zephyrproject/zephyr/{app} && timeout 600 west build -b {board} -d /tmp/build -p always -t run .\n\n"
        f"or, equivalently, with the helper `zephyr-run {board}` run from the application directory, which stops the "
        "emulator as soon as the suite finishes. The suite's result is printed as 'PROJECT EXECUTION SUCCESSFUL' or "
        "'PROJECT EXECUTION FAILED'. On QEMU boards the emulator keeps running after the suite ends, so if you run "
        "west directly, always use a timeout.\n\n"
        "The build/run currently fails with this (filtered) log:\n\n"
        f"{state.get('current_error_log', '')}\n\n"
        "Find and fix the defect so that the build succeeds and the test suite passes. Edit the source files "
        "in place; the evaluator rebuilds from scratch afterwards, so leave the tree in its fixed state. There is "
        "no network access and no version control history; do not try to use them.\n\n"
        + SPEC_RULE + "\n\nWhen you are done, stop."
    )


def _snapshot(root: str) -> Dict[str, Tuple[float, int]]:
    snap = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            snap[os.path.relpath(p, root)] = (st.st_mtime, st.st_size)
    return snap


def _changed_files(root: str, before: Dict[str, Tuple[float, int]]) -> Tuple[List[str], Dict[str, str]]:
    after = _snapshot(root)
    changed = sorted(p for p, v in after.items() if before.get(p) != v)
    deleted = sorted(p for p in before if p not in after)
    contents, total = {}, 0
    for p in changed[:_MAX_RECORDED_FILES]:
        try:
            text = open(os.path.join(root, p), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        if total + len(text) > _MAX_RECORDED_BYTES:
            break
        contents[p] = text
        total += len(text)
    return changed + [f"(deleted) {p}" for p in deleted], contents


def run_openhands(state: Dict[str, Any]) -> Dict[str, Any]:
    workspace = state["workspace_path"]
    key_env = _KEY_ENV.get(get_provider())
    if not key_env or not os.environ.get(key_env):
        raise RuntimeError(f"OpenHands baseline needs {key_env} for provider {get_provider()}")
    before = _snapshot(workspace)
    with tempfile.TemporaryDirectory() as tmp:
        task_file, out_file = os.path.join(tmp, "task.txt"), os.path.join(tmp, "out.json")
        with open(task_file, "w", encoding="utf-8") as f:
            f.write(build_task(state))
        cmd = [OH_PYTHON, os.path.join(_REPO, "tools", "openhands_runner.py"),
               "--workspace", workspace, "--target-app", state["target_app"], "--task-file", task_file,
               "--model", litellm_model(), "--image", OH_IMAGE, "--max-steps", str(OH_MAX_STEPS),
               "--timeout-min", str(OH_TIMEOUT_MIN), "--out", out_file]
        env = dict(os.environ, LLM_API_KEY=os.environ[key_env])
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=OH_TIMEOUT_MIN * 60 + 900)
        if not os.path.exists(out_file):
            raise RuntimeError(f"openhands_runner failed (exit {proc.returncode}): {proc.stderr[-3000:]}")
        oh = json.load(open(out_file, encoding="utf-8"))
    # LLM 錯誤 (額度用完、503…) 讓 evaluate.py 照一般規則分類 (daily_quota / transient_api)，
    # 不當成結果；到達步數或時間上限則照常評分。
    # LLM errors (quota, 503...) are raised so evaluate.py classifies them as usual (daily_quota /
    # transient_api) instead of grading; hitting the step or time limit is graded normally.
    if oh.get("error") and oh.get("status") not in ("max_steps", "timeout", "finished", "stuck"):
        raise RuntimeError(f"OpenHands run error: {oh['error']}")
    changed, contents = _changed_files(workspace, before)

    eval_result = evaluate_repair_attempt(workspace, state["board"], state["target_app"],
                                          state.get("required_pass_test"))
    usage = {"node": "openhands", "model": get_model_name("pro"), "input_tokens": oh.get("input_tokens", 0),
             "output_tokens": oh.get("output_tokens", 0), "llm_calls": oh.get("llm_calls", 0)}
    trajectory = build_trajectory("run" if eval_result["compiled"] else "build", eval_result["status"],
                                  applied_files=changed, filtered_log=eval_result["log"],
                                  restored_files=pop_restored(workspace))
    return {
        "final_status": "resolved" if eval_result["resolved"] else "failed_max_retries",
        "iterations": oh.get("steps", 0),
        "iteration_log": [{"iteration": 1, "compiled": eval_result["compiled"], "resolved": eval_result["resolved"],
                           "tool_invocation_error": False, "token_usage": [usage], "trajectory": trajectory}],
        "first_retrieval_files": None,
        "openhands": {k: oh.get(k) for k in ("status", "error", "steps", "llm_calls", "input_tokens",
                                              "output_tokens", "cost", "wall_seconds", "model", "image",
                                              "max_steps", "timeout_min")}
                     | {"changed_files": changed, "final_contents": contents, "events": oh.get("events", [])},
    }
