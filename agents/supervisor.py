# agents/supervisor.py
"""
Supervisor：中央調度，負責在 ApplyPatch/StaticCheck/Build 之後決定要不要
繼續重試、要退回哪一個節點，還是已經超過最大重試次數 (或已經修復成功)
該結束了。

Supervisor: central dispatch, deciding after ApplyPatch/StaticCheck/Build
whether to keep retrying, which node to bounce back to, or whether the max
retry count has been exceeded (or the repair already succeeded) and it's
time to stop.
"""
from core.state import ZephyrAgentState


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
