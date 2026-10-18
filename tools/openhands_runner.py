# tools/openhands_runner.py
"""
OpenHands baseline 的 agent 執行端 (2026-10-18，教授建議)：用 OpenHands Software Agent SDK 的
CodeAct 式 agent (Terminal + FileEditor 工具，沒有瀏覽器) 在一個以 zephyr-sandbox 為底的
agent-server 容器裡修一個案例。agent 可以自己跑 west build / QEMU。評分不在這裡做：結束後由
evaluate.py (core/openhands_pipeline.py) 用和其他 pipeline 相同的 evaluate_repair_attempt 評分。

需要 Python >= 3.12 (SDK 的要求)，所以獨立於主程式的 venv 執行：
  venv_oh/bin/python tools/openhands_runner.py --workspace <dir> --target-app <app> --task-file <txt> \
      --model gemini/gemini-2.5-flash --image zephyr-oh-agent:1.55.1 --max-steps 50 --timeout-min 40 --out <json>
API 金鑰從環境變數 LLM_API_KEY 讀 (呼叫端帶入)，不寫進任何檔案。

Agent side of the OpenHands baseline (2026-10-18, advisor's suggestion): an OpenHands Software Agent SDK
agent (Terminal + FileEditor tools, no browser) fixes one case inside an agent-server container built on
zephyr-sandbox; it may run west build / QEMU itself. Grading happens afterwards in evaluate.py with the same
evaluate_repair_attempt as every other pipeline. Needs Python >= 3.12, hence its own venv. The API key comes
from LLM_API_KEY and is never written anywhere.
"""
import argparse
import json
import os
import threading
import time
from typing import Any, Dict, List

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from openhands.workspace import DockerWorkspace

_OBS_CLIP = 2000


def _clip(text: Any, n: int = _OBS_CLIP) -> str:
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[: n // 2] + f"\n…[{len(s) - n} chars omitted]…\n" + s[-n // 2:]


def _summarize(event: Any) -> Dict[str, Any]:
    if isinstance(event, ActionEvent):
        action = event.action.model_dump(mode="json") if event.action is not None else None
        return {"type": "action", "tool": event.tool_name, "action": _clip(action, 4000),
                "thought": _clip(" ".join(t.text for t in event.thought) if event.thought else "", 1500)}
    if isinstance(event, ObservationEvent):
        obs = event.observation.model_dump(mode="json") if event.observation is not None else None
        return {"type": "observation", "tool": getattr(event, "tool_name", None), "observation": _clip(obs)}
    return {"type": type(event).__name__}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, help="host path of the prepared Zephyr tree")
    ap.add_argument("--target-app", required=True)
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--model", required=True, help="LiteLLM model string, e.g. gemini/gemini-2.5-flash")
    ap.add_argument("--image", required=True)
    ap.add_argument("--max-steps", type=int, default=50)
    ap.add_argument("--timeout-min", type=float, default=40)
    ap.add_argument("--llm-timeout", type=int, default=300)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    task = open(args.task_file, encoding="utf-8").read()
    events: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {"model": args.model, "image": args.image, "max_steps": args.max_steps,
                           "timeout_min": args.timeout_min, "status": None, "error": None}
    start = time.time()

    llm = LLM(usage_id="agent", model=args.model, api_key=SecretStr(os.environ["LLM_API_KEY"]),
              temperature=0.0, timeout=args.llm_timeout, num_retries=3)
    agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)])

    # 掛在 /zephyrproject/zephyr：映像的 ZEPHYR_BASE 指向這裡 (見 core/workflow.build_devops_docker_cmd)。
    # agent-server 會在工作目錄執行 `git init` (空 repo，給它的 Changes 介面用)，所以工作目錄放在 workspace 外的
    # /workspace/project，不在 Zephyr 樹裡留下 .git；任務說明會叫 agent 先 cd 到 app 目錄。
    # Mounted at /zephyrproject/zephyr: the image's ZEPHYR_BASE points there (see build_devops_docker_cmd).
    # The agent-server runs `git init` (empty repo, for its Changes view) in the working dir, so the working dir
    # is /workspace/project outside the workspace; the task tells the agent to cd into the app first.
    with DockerWorkspace(server_image=args.image, host_port=None,
                         volumes=[f"{os.path.abspath(args.workspace)}:/zephyrproject/zephyr"],
                         working_dir="/workspace/project",
                         health_check_timeout=300.0) as workspace:
        conversation = Conversation(agent=agent, workspace=workspace, max_iteration_per_run=args.max_steps,
                                    visualizer=None, callbacks=[lambda e: events.append(_summarize(e))])
        conversation.send_message(task)
        errors: List[str] = []

        def _run() -> None:
            try:
                conversation.run(timeout=args.timeout_min * 60 + 60)
            except Exception as e:  # noqa: BLE001 - recorded, graded as unresolved by the caller
                errors.append(f"{type(e).__name__}: {e}")

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(args.timeout_min * 60)
        timed_out = worker.is_alive()
        if timed_out:
            try:
                conversation.pause()
            except Exception as e:  # noqa: BLE001
                errors.append(f"pause failed: {type(e).__name__}: {e}")
            worker.join(120)
        try:
            status = str(conversation.state.execution_status.value)
        except Exception:  # noqa: BLE001
            status = "unknown"
        # 到達步數上限時 SDK 會丟 MaxIterationsReached；這是預算用完，照常評分，不是執行錯誤。
        # The SDK raises MaxIterationsReached at the step limit; that is a spent budget, graded normally.
        if any("MaxIterationsReached" in e for e in errors):
            errors = [e for e in errors if "MaxIterationsReached" not in e]
            status = "max_steps"
        stats = conversation.conversation_stats.get_combined_metrics()
        usage = stats.accumulated_token_usage
        out.update({
            "status": "timeout" if timed_out else status,
            "error": "; ".join(errors) or None,
            "steps": sum(1 for e in events if e["type"] == "action"),
            "llm_calls": len(stats.token_usages),
            "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
            "cost": stats.accumulated_cost,
            "wall_seconds": time.time() - start,
        })
        try:
            conversation.close()
        except Exception:  # noqa: BLE001
            pass
    out["events"] = events
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
