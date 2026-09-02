# core/workflow.py
import os
import logging
from typing import Dict, Any

from langgraph.graph import StateGraph, END

# 引入自訂工具鏈與狀態
from core.state import ZephyrAgentState
from tools.patch_applier import PatchApplier
from tools.log_filter import LogFilter
from tools.qemu_oracle import QemuOracle
from tools.devops_analyzer import classify_build_failure, annotate_log_with_classification

# 需要呼叫 LLM 的代理人節點邏輯都搬到 agents/*.py 了 (跟 README 描述的架構
# 對齊)；ApplyPatch/StaticCheck/DevOps/QA 這幾個節點雖然對應提案
# Methodology 描述的具體階段/角色 (DevOps Expert、QA Expert)，但本身都是
# 純粹呼叫工具/子行程 (PatchApplier、cppcheck、west build、QemuOracle)，
# 不叫 LLM，所以維持跟 ApplyPatch/StaticCheck 一致的既有慣例，留在
# workflow.py，不搬進 agents/*.py。workflow.py 因此負責把所有節點接成圖，
# 加上這幾個不叫 LLM 的執行期節點本身。
# Agent node logic that calls an LLM has moved to agents/*.py (matching
# the architecture README.md describes); ApplyPatch/StaticCheck/DevOps/QA
# correspond to concrete stages/roles the proposal's Methodology names
# (DevOps Expert, QA Expert), but they're all pure tool/subprocess
# execution (PatchApplier, cppcheck, west build, QemuOracle) with no LLM
# call — so, consistent with ApplyPatch/StaticCheck's existing precedent,
# they stay here rather than moving into agents/*.py. workflow.py wires
# every node into the graph, plus owns these non-LLM execution nodes
# themselves.
from agents.analyzer import analyzer_node
from agents.knowledge_expert import knowledge_expert_node
from agents.patch_expert import patch_node
from agents.supervisor import (
    route_after_apply_patch, route_after_static_check, route_after_devops, route_after_qa,
    record_attempt_outcome, record_iteration_success,
)
from tools.static_checker import StaticChecker

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def build_devops_docker_cmd(workspace_path: str, board: str, target_app: str) -> str:
    """
    組出 DevOps Expert 用來建置/執行一個案例的 docker 指令。抽成獨立函式，
    讓 evaluate.py 準備好 workspace 之後，可以用完全相同的指令先做一次
    「重現初始失敗」的驗收檢查，而不是各自維護一份容易漂移的複製字串。

    掛載點必須是 /zephyrproject/zephyr，不能是 /workspace：見 devops_node
    內的說明——映像檔的 ZEPHYR_BASE 環境變數在建置當下就烤死指向
    /zephyrproject/zephyr，掛在別的路徑會讓 west build 實際上去用容器內建、
    從未被我們修改過的那份 Zephyr 原始碼。

    Builds the docker command the DevOps Expert uses to build/run a case.
    Factored out so evaluate.py can run the exact same command as a
    "reproduces the initial failure" sanity check right after preparing a
    workspace, instead of maintaining a second, driftable copy of this
    string.

    The mount point must be /zephyrproject/zephyr, not /workspace — see the
    comment inside devops_node: the image bakes ZEPHYR_BASE to point at
    /zephyrproject/zephyr at build time, so mounting anywhere else makes
    west build actually use the container's stock, never-modified Zephyr
    source.

    掛載必須可寫 (不能帶 :ro)：CMake 的 toolchain capability 檢查
    (extensions.cmake 的 zephyr_check_compiler_flag) 會把快取寫進
    <zephyr>/.cache/ToolchainCapabilityDatabase/ ——這個目錄在原始碼樹
    裡面，不是獨立的 build 目錄。掛成唯讀時，west build 在真正編譯到
    app 原始碼之前就會先在這一步失敗 (`file failed to open for writing`)，
    產生的失敗特徵跟 eof_no_boot 這個狀態桶完全一樣，會把「真正的注入
    bug 造成的建置失敗」跟「這個純粹的掛載寫入權限問題」混為一談 (實測
    2026-08-30：inject_c_hello_world_brace 的初始重現檢查一路回報
    eof_no_boot，表面上跟資料集記錄的預期值吻合，但拿掉 :ro 重測後才發現
    先前每一次其實都是卡在這裡，從未真正編譯到那個少一個大括號的
    main.c)。PatchApplier 本來就是在 docker 外面直接寫 host 檔案系統，
    :ro 從未真正提供任何隔離保護，只是讓 CMake 自己的快取寫入失敗；拿掉
    它才會跟 FaultInjector 驗證資料集時 (容器原生、可寫的檔案系統) 的
    建置行為一致。

    The mount must be writable (no :ro): CMake's toolchain-capability check
    (extensions.cmake's zephyr_check_compiler_flag) writes its cache into
    <zephyr>/.cache/ToolchainCapabilityDatabase/ — inside the source tree,
    not a separate build directory. Mounted read-only, west build fails at
    this step (`file failed to open for writing`) before ever reaching the
    app's own source — and that failure lands in the exact same
    "eof_no_boot" bucket as a genuine build-time bug, conflating "the
    injected bug actually broke the build" with "this mount is merely
    read-only" (confirmed 2026-08-30: inject_c_hello_world_brace's initial
    repro-check kept reporting eof_no_boot, superficially matching the
    dataset's recorded expected value, but re-testing without :ro revealed
    every prior run had actually been stuck here the whole time, never once
    reaching the real missing-brace bug in main.c). PatchApplier already
    writes directly to the host filesystem outside docker, so :ro was never
    providing real isolation — only breaking CMake's own cache writes;
    dropping it matches the writable, native container filesystem
    FaultInjector actually verified this dataset against.
    """
    return (
        f"docker run --rm -i -v {os.path.abspath(workspace_path)}:/zephyrproject/zephyr "
        f"-w /zephyrproject/zephyr/{target_app} zephyr-sandbox "
        f"bash -c 'west build -b {board} -d /tmp/build -p always -t run .'"
    )


# route_after_apply_patch/route_after_static_check/route_after_devops
# 都用這個值決定要不要直接結束——「已經用完迭代預算」這件事只該有一種
# 算法，寫成共用函式，不要在 apply_patch_node/static_check_node/
# devops_node/qa_node 四個地方各自重算一次容易漂移的邏輯。
# route_after_apply_patch/route_after_static_check/route_after_devops all
# use this value to decide whether to stop outright — "iteration budget
# exhausted" should have exactly one formula, factored out instead of
# re-derived slightly differently in each of apply_patch_node/
# static_check_node/devops_node/qa_node.
def _compute_failure_final_status(current_iter: int, max_iterations: int) -> str:
    return "failed_max_retries" if current_iter >= max_iterations else "in_progress"


# QemuOracle 的狀態字彙裡，這三種都代表「真的編譯出執行檔並跑到 QEMU/
# native_sim 起來」(crash/missing_required_test 是跑起來後才失敗，success
# 更不用說)；其餘 (eof_no_boot/timeout/docker_infra_error/
# west_update_error/target_path_missing/unsupported_board/unknown) 代表
# 從沒真正跑起來過——tools/qemu_oracle.py 自己的註解也是這樣定義
# eof_no_boot 的 ("build-failed-before-QEMU-ever-started")。RQ1/RQ3 的
# Bounded Compilation Success Rate (Pass@k) 用這個判斷，跟 Functional Pass
# Rate (= resolved) 是兩個獨立的指標。
# In QemuOracle's status vocabulary, these three all mean "a binary was
# actually compiled and reached QEMU/native_sim boot" (crash/
# missing_required_test fail only after booting; success obviously so); the
# rest (eof_no_boot/timeout/docker_infra_error/west_update_error/
# target_path_missing/unsupported_board/unknown) mean it never genuinely
# ran — tools/qemu_oracle.py's own comments define eof_no_boot this way too
# ("build-failed-before-QEMU-ever-started"). RQ1/RQ3's Bounded Compilation
# Success Rate (Pass@k) is derived from this, a metric independent of
# Functional Pass Rate (= resolved).
_COMPILED_STATUSES = frozenset({"crash", "missing_required_test", "success"})


def evaluate_repair_attempt(workspace_path: str, board: str, target_app: str,
                             required_pass_test: str = None) -> Dict[str, Any]:
    """
    執行一次 west build -t run 並判讀結果，回傳
    {"status", "resolved", "compiled", "log"}。

    「套件整體成功，但 required_pass_test 沒有真的通過 (投機取巧/繞過)」這個
    判讀屬於防呆邏輯的核心一環，被 Build (Proposed pipeline) 跟 B1/B2/B3
    baseline 共用同一份實作——拆出來是為了不讓這個判斷在多個呼叫點各自實作、
    容易在某一處漏掉 (漏掉的後果是讓一個刪測試的投機 patch 被誤判為修復成功)。

    Runs one `west build -t run` and interprets the result, returning
    {"status", "resolved", "compiled", "log"}. The "suite as a whole reports
    success, but required_pass_test never actually PASSed (a shortcut patch
    bypassing the test)" judgment is core fail-safe logic, shared by Build
    (the Proposed pipeline) and the B1/B2/B3 baselines — factored out so
    this check isn't reimplemented at each call site (where forgetting it
    once would let a test-deleting shortcut patch be misjudged as a
    successful repair).
    """
    docker_cmd = build_devops_docker_cmd(workspace_path, board, target_app)
    oracle = QemuOracle(timeout=600)
    eval_result = oracle.evaluate(docker_cmd, required_pass_test=required_pass_test)
    log_filter = LogFilter()
    compiled = eval_result["status"] in _COMPILED_STATUSES

    if eval_result["status"] == "missing_required_test":
        compressed_log = log_filter.compress_log(eval_result["log"]) + (
            f"\n\n[判定失敗：套件整體成功，但目標測試 '{required_pass_test}' 未見 PASS，"
            f"patch 疑似繞過而非真正修復。]"
        )
        return {"status": "missing_required_test", "resolved": False, "compiled": compiled, "log": compressed_log}

    if eval_result["status"] == "success":
        return {"status": "success", "resolved": True, "compiled": compiled, "log": "Zephyr OS successfully booted."}

    return {"status": eval_result["status"], "resolved": False, "compiled": compiled,
            "log": log_filter.compress_log(eval_result["log"])}


def apply_patch_node(state: ZephyrAgentState) -> Dict[str, Any]:
    """
    ApplyPatch：把 Patch Expert 產生的 SEARCH/REPLACE 區塊套用到 workspace
    的實體檔案上。`iterations` 在這裡遞增一次，代表「第幾次修補嘗試」——
    後面的 StaticCheck/Build 不管走到哪一步都只讀這個值，不會再遞增，這樣
    「一次迭代」的定義才是單一、一致的，不會因為 StaticCheck 提早打回
    Patch 重生成而被算成兩次。
    ApplyPatch: applies the Patch Expert's SEARCH/REPLACE blocks to real
    files in the workspace. `iterations` is incremented exactly once here,
    representing "which patch attempt this is" — StaticCheck/Build further
    down only ever read this value, never increment it again, so "one
    iteration" stays a single, consistent definition regardless of whether
    StaticCheck bounces back to Patch to regenerate.
    """
    current_iter = state.get("iterations", 0) + 1
    workspace_path = state.get("workspace_path")
    patch_content = state.get("patch_content", "")
    max_iterations = state.get("max_iterations", 5)
    failure_final_status = _compute_failure_final_status(current_iter, max_iterations)

    print(f"\n⚙️ [ApplyPatch] 套用第 {current_iter} 次迭代的修補...")

    applier = PatchApplier(workspace_path=workspace_path)
    patch_result = applier.apply_patches(patch_content)

    if not patch_result["success"]:
        print("   ❌ 修補應用失敗！格式錯誤或找不到匹配的原始碼。")
        return {
            "current_error_log": f"Patch Application Failed:\n{patch_result['error']}",
            "error_type": "patch_format_error",
            "applied_files": [],
            "iterations": current_iter,
            "final_status": failure_final_status,
            **record_attempt_outcome(
                current_iter, f"套用修補失敗 (格式錯誤/找不到匹配的原始碼)：{patch_result['error']}",
                compiled=False, tool_invocation_error=True,
                pending_token_usage=state.get("pending_token_usage", []),
            ),
        }

    applied_files = patch_result.get("applied_files", [])
    print(f"   ✅ 修補成功應用至: {applied_files}")
    return {
        "error_type": "patch_applied",
        "applied_files": applied_files,
        "iterations": current_iter,
    }


def static_check_node(state: ZephyrAgentState) -> Dict[str, Any]:
    """
    StaticCheck：在真正跑一次完整 west build -t run (通常要花好幾分鐘)
    之前，先用 cppcheck + west build --cmake-only 快速檔住明顯壞掉的
    patch，沒過就直接打回 Patch 重新生成 (proposal 說的
    early-exit-back-to-GeneratePatch)，不用等一次完整建置才知道。

    StaticCheck: before spending several minutes on a full
    `west build -t run`, use cppcheck + `west build --cmake-only` to
    quickly catch an obviously broken patch; a failure bounces straight
    back to Patch to regenerate (the proposal's
    early-exit-back-to-GeneratePatch), without waiting out a full build.
    """
    current_iter = state.get("iterations", 0)
    max_iterations = state.get("max_iterations", 5)
    failure_final_status = _compute_failure_final_status(current_iter, max_iterations)

    workspace_path = state.get("workspace_path")
    target_app = state.get("target_app", ".")
    board = state.get("board", "qemu_x86")
    applied_files = state.get("applied_files", [])

    print(f"\n🔍 [StaticCheck] 對第 {current_iter} 次迭代的修補做靜態分析...")
    checker = StaticChecker()
    result = checker.check(workspace_path, target_app, board, applied_files)

    if result["passed"]:
        print("   ✅ 靜態分析通過，進入完整建置。")
        return {"error_type": "static_check_passed"}

    print("   ⚠️ 靜態分析發現問題，直接打回 Patch 重新生成，跳過這次完整建置。")
    return {
        "current_error_log": result["log"],
        "error_type": "static_check_failed",
        "final_status": failure_final_status,
        **record_attempt_outcome(
            current_iter, f"修補已套用至 {applied_files}，但靜態分析發現問題：{result['log']}",
            compiled=False, tool_invocation_error=False,
            pending_token_usage=state.get("pending_token_usage", []),
        ),
    }


def devops_node(state: ZephyrAgentState) -> Dict[str, Any]:
    """
    DevOps Expert：跑 west build (提案 Methodology 說的 "Build (west)" 階段)，
    失敗時對日誌做依賴/Kconfig 衝突分類 (tools/devops_analyzer.py)。跟
    QA Expert (qa_node) 拆成兩個節點是為了對齊提案 Methodology 描述的
    "Build (west) → Execute (QEMU) → ObserveRuntime" 兩個獨立階段，但底層
    只實際跑一次 `west build -t run` (見 evaluate_repair_attempt / state.py
    的 pending_eval_result 說明)，不是各自獨立呼叫兩次——沒必要為了架構
    對齊多花一次完整編譯的時間，也不用新增讓兩個容器共用建置產物的掛載
    機制。

    失敗時的重試路由維持跟拆分前一致，退回 Analyzer 重新診斷——這是跟
    使用者確認過的決定：提案原文只明確講 StaticCheck 失敗會跳過 Analyzer
    直接回 Patch，對 Build/Execute 失敗要怎麼重試沒有寫死；在沒有提案
    依據的情況下，不要自己發明一個可能影響修復品質的新路由行為，這次拆分
    純粹是架構層面 (匹配提案圖) + 新增依賴/Kconfig 衝突分類這個加分項，
    不改變既有的重試邏輯。

    DevOps Expert: runs the west build (the proposal Methodology's
    "Build (west)" stage), classifying the log for dependency/Kconfig
    conflicts on failure (tools/devops_analyzer.py). Split from QA Expert
    (qa_node) into two nodes to match the proposal Methodology's separate
    "Build (west) → Execute (QEMU) → ObserveRuntime" stages, but only one
    real `west build -t run` actually runs under the hood (see
    evaluate_repair_attempt / state.py's pending_eval_result docstring),
    not two independent invocations — no need to spend a second full
    compile, or add machinery for two containers to share build artifacts,
    just for architectural alignment.

    Failure-path retry routing is unchanged from before the split — still
    bounces back to Analyzer for a fresh diagnosis. Confirmed with the
    user: the proposal only explicitly states StaticCheck failures skip
    Analyzer and go straight back to Patch; it never specifies how
    Build/Execute failures should route. Absent that basis, this split
    doesn't invent a new routing behavior that could affect repair
    quality — it's purely architectural (matching the proposal's diagram)
    plus the new dependency/Kconfig-conflict classification as a value-add,
    not a change to existing retry logic.
    """
    current_iter = state.get("iterations", 0)
    max_iterations = state.get("max_iterations", 5)
    failure_final_status = _compute_failure_final_status(current_iter, max_iterations)

    workspace_path = state.get("workspace_path")
    applied_files = state.get("applied_files", [])

    # board/target_app 之前寫死成 qemu_x86 + workspace 根目錄，只能對付
    # main.py 的單一 demo 場景；現在改成從 state 讀取，才能正確對應資料集
    # 裡任何一筆案例真正的目標板子與 app 子目錄 (例如
    # "tests/subsys/input/longpress" + "native_sim")。target_app 用來組
    # 出容器內要 cd 進去的工作目錄，跟 FaultInjector 建立注入案例時
    # "cd 進 target_app 再 west build ." 的方式一致。
    # board/target_app used to be hardcoded to qemu_x86 + the workspace
    # root, which only worked for main.py's single demo scenario; now read
    # from state so this correctly targets whatever board/app subdirectory
    # a given dataset case actually needs (e.g.
    # "tests/subsys/input/longpress" + "native_sim"). target_app is used
    # to build the in-container working directory, matching how
    # FaultInjector builds injected cases ("cd into target_app, then
    # west build .").
    board = state.get("board", "qemu_x86")
    target_app = state.get("target_app", ".")
    required_pass_test = state.get("required_pass_test")

    print(f"\n🔨 [DevOps] 第 {current_iter} 次迭代：開始在隔離容器中編譯 (west build)...")
    # timeout=15 對一次真正的 west build (可能要編譯 Zephyr kernel + app)
    # 來說遠遠不夠，幾乎每次都會提早 timeout，把任何案例都誤判為建置卡住
    # ——FaultInjector 驗證資料集時本來就是用 600s (tools/fault_injector.py)，
    # evaluate_repair_attempt 內部跟著對齊，而不是沿用 QemuOracle 建構子
    # 預設值 15。
    # timeout=15 is far too short for an actual west build (which may need
    # to compile the Zephyr kernel plus the app) — nearly every run would
    # time out early and get misclassified as stuck. FaultInjector already
    # uses 600s to verify this dataset (tools/fault_injector.py);
    # evaluate_repair_attempt aligns with that instead of QemuOracle's
    # constructor default of 15.
    eval_result = evaluate_repair_attempt(workspace_path, board, target_app, required_pass_test)

    if not eval_result["compiled"]:
        conflict_tag = classify_build_failure(eval_result["log"])
        annotated_log = annotate_log_with_classification(eval_result["log"])
        print(f"   💥 建置失敗 (狀態: {eval_result['status']})"
              + (f"，日誌樣式疑似屬於「{conflict_tag}」類別" if conflict_tag else ""))
        return {
            "current_error_log": annotated_log,
            "error_type": eval_result["status"],
            "iterations": current_iter,
            "final_status": failure_final_status,
            **record_attempt_outcome(
                current_iter,
                f"修補已套用至 {applied_files}，通過靜態分析後 west build 仍然失敗 "
                f"(狀態: {eval_result['status']}"
                + (f"，疑似屬於「{conflict_tag}」類別" if conflict_tag else "")
                + f")：{annotated_log}",
                compiled=False, tool_invocation_error=False,
                pending_token_usage=state.get("pending_token_usage", []),
            ),
        }

    print("   ✅ 建置成功，交給 QA Expert 執行並觀察執行期結果。")
    return {"error_type": "devops_build_passed", "pending_eval_result": eval_result}


def qa_node(state: ZephyrAgentState) -> Dict[str, Any]:
    """
    QA Expert：解讀 DevOps Expert 已經跑完的執行結果 (提案 Methodology 說的
    "Execute (QEMU) → ObserveRuntime" 階段)——west build -t run 本身已經
    在 devops_node 執行完畢，這裡只讀 devops_node 存進
    state["pending_eval_result"] 的結果，不再跑第二次 west build。

    QA Expert: interprets the execution result DevOps Expert already
    produced (the proposal Methodology's "Execute (QEMU) →
    ObserveRuntime" stage) — `west build -t run` itself already ran inside
    devops_node; this only reads the result DevOps Expert stashed in
    state["pending_eval_result"], it doesn't run a second west build.
    """
    current_iter = state.get("iterations", 0)
    max_iterations = state.get("max_iterations", 5)
    failure_final_status = _compute_failure_final_status(current_iter, max_iterations)

    applied_files = state.get("applied_files", [])
    required_pass_test = state.get("required_pass_test")
    eval_result = state["pending_eval_result"]

    if eval_result["status"] == "missing_required_test":
        print(f"   ⚠️ [QA] 套件回報成功，但目標測試 '{required_pass_test}' 沒有真的通過——patch 疑似投機取巧 (刪除/跳過該測試)，不算修復成功。")
        return {
            "current_error_log": eval_result["log"],
            "error_type": "missing_required_test",
            "iterations": current_iter,
            "final_status": failure_final_status,
            **record_attempt_outcome(
                current_iter,
                f"修補已套用至 {applied_files}，套件整體回報成功，但目標測試 '{required_pass_test}' 未見 PASS，"
                f"疑似投機取巧而非真正修復。",
                compiled=eval_result["compiled"], tool_invocation_error=False,
                pending_token_usage=state.get("pending_token_usage", []),
            ),
        }

    if eval_result["status"] == "success":
        print("   🎉 [QA] 執行期驗證通過！")
        return {
            "current_error_log": eval_result["log"],
            "error_type": "success",
            "iterations": current_iter,
            "final_status": "resolved",
            **record_iteration_success(current_iter, state.get("pending_token_usage", [])),
        }

    print(f"   💥 [QA] 執行期驗證失敗 (狀態: {eval_result['status']})，正在過濾日誌...")
    return {
        "current_error_log": eval_result["log"],
        "error_type": eval_result["status"],
        "iterations": current_iter,
        "final_status": failure_final_status,
        **record_attempt_outcome(
            current_iter,
            f"修補已套用至 {applied_files}，通過建置後執行，但執行期驗證失敗 "
            f"(狀態: {eval_result['status']})：{eval_result['log']}",
            compiled=eval_result["compiled"], tool_invocation_error=False,
            pending_token_usage=state.get("pending_token_usage", []),
        ),
    }

# ==========================================
# 3. 定義邊界與建立狀態機
# ==========================================
def route_after_analyzer(state: ZephyrAgentState) -> str:
    return "goto_knowledge" if len(state.get("search_keywords", [])) > 0 else "goto_patch"

def build_zephyr_graph() -> StateGraph:
    workflow = StateGraph(ZephyrAgentState)
    workflow.add_node("Analyzer", analyzer_node)
    workflow.add_node("Knowledge", knowledge_expert_node)
    workflow.add_node("Patch", patch_node)
    workflow.add_node("ApplyPatch", apply_patch_node)
    workflow.add_node("StaticCheck", static_check_node)
    workflow.add_node("DevOps", devops_node)
    workflow.add_node("QA", qa_node)

    workflow.set_entry_point("Analyzer")
    workflow.add_conditional_edges("Analyzer", route_after_analyzer, {"goto_knowledge": "Knowledge", "goto_patch": "Patch"})
    workflow.add_edge("Knowledge", "Patch")
    workflow.add_edge("Patch", "ApplyPatch")
    workflow.add_conditional_edges("ApplyPatch", route_after_apply_patch, {
        "goto_static_check": "StaticCheck",
        "retry_analyzer": "Analyzer",
        "finish": END,
    })
    workflow.add_conditional_edges("StaticCheck", route_after_static_check, {
        "goto_build": "DevOps",
        "retry_patch": "Patch",
        "finish": END,
    })
    # DevOps Expert (west build) → QA Expert (QEMU 執行/觀察)，對應提案
    # Methodology 的 "Build (west) → Execute (QEMU) → ObserveRuntime" 兩個
    # 階段；DevOps 失敗 (west build 本身失敗，不是 StaticCheck 攔到的) 維持
    # 退回 Analyzer 重新診斷，跟拆分前的行為一致 (見 devops_node 說明)。
    # DevOps Expert (west build) → QA Expert (QEMU execute/observe),
    # matching the proposal Methodology's "Build (west) → Execute (QEMU) →
    # ObserveRuntime" stages; a DevOps failure (the west build itself
    # failing, not something StaticCheck already caught) still bounces back
    # to Analyzer for a fresh diagnosis, unchanged from before the split
    # (see devops_node's docstring).
    workflow.add_conditional_edges("DevOps", route_after_devops, {
        "goto_qa": "QA",
        "retry_analyzer": "Analyzer",
        "finish": END,
    })
    workflow.add_conditional_edges("QA", route_after_qa, {"finish": END, "retry": "Analyzer"})

    return workflow.compile()