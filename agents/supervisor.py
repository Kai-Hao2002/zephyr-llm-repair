# agents/supervisor.py
"""
Supervisor：中央調度，負責判斷是否超過最大重試次數 (或已經修復成功)，決定
閉環要繼續重試還是結束。

Supervisor: central dispatch, decides whether the max retry count has been
exceeded (or the repair already succeeded), routing the closed loop to
either retry or finish.
"""
from core.state import ZephyrAgentState


def route_after_devops(state: ZephyrAgentState) -> str:
    if state.get("error_type") == "success" or state.get("iterations", 0) >= state.get("max_iterations", 5):
        return "finish"
    return "retry"
