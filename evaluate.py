# evaluate.py
"""
Zephyr-Eval 跑分入口 (Phase 1 雛形)。

目前只做「單一案例 end-to-end」：從 final_dataset.json 讀一筆案例、在容器內
checkout broken_commit 並套用該案例的 mutation、把結果複製到 host 上一個
乾淨的 (不含 .git) workspace 目錄、用 board/target_app/target_test 餵給
create_initial_state()，再跑 build_zephyr_graph()。

Phase 1 evaluate.py prototype: single-case end-to-end only. Reads one case
from final_dataset.json, checks out broken_commit and applies that case's
mutation inside a container, copies the result out to a clean (no .git)
host workspace directory, feeds board/target_app/target_test into
create_initial_state(), and runs build_zephyr_graph().

README.md 記載了兩條硬性設計要求，這支腳本從一開始就把它們做進
prepare_broken_workspace()/build_agent_initial_state()，而不是事後補：
1. 交給 agent 的 workspace 絕對不能含 .git (否則 `git checkout -- .`
   就能讓任何案例免修復地「通過」)。
2. 交給 agent 的任何內容都不能含 injection/injections/fixed_commit 欄位
   或由它們反推得出的資訊。

This script bakes in the two hard design requirements README.md documents,
from the start rather than retrofitted:
1. The agent's workspace must never contain .git (otherwise `git checkout
   -- .` trivially "resolves" any case without any real repair).
2. Nothing handed to the agent may contain the injection/injections/
   fixed_commit fields, or anything derived from them.
"""
import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from core.state import create_initial_state, ZephyrAgentState
from core.workflow import build_zephyr_graph, build_devops_docker_cmd
from tools.fault_injector import MUTATE_SCRIPT_HOST_PATH, MUTATE_SCRIPT_CONTAINER_PATH
from tools.qemu_oracle import QemuOracle

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("evaluate")

DEFAULT_DATASET_PATH = os.path.join("dataset", "cases", "final_dataset.json")

# 交給 agent (create_initial_state) 的唯一白名單欄位——刻意用 allowlist 而不是
# blacklist：往資料集 schema 加新欄位是常態，allowlist 預設拒絕未知欄位，
# blacklist 則要求每次加欄位都記得同步更新排除清單，忘記就是一次外洩。
# The only fields ever passed to the agent (via create_initial_state) — an
# allowlist rather than a blacklist on purpose: the dataset schema gaining a
# new field over time is the normal case, and an allowlist defaults to
# rejecting anything unrecognized, whereas a blacklist requires remembering
# to update the exclusion list every time a field is added — forgetting once
# is a leak.
_AGENT_VISIBLE_CASE_FIELDS = {"board", "target_app", "target_test", "initial_error_log"}
_NEVER_AGENT_VISIBLE_FIELDS = {"injection", "injections", "fixed_commit", "broken_commit"}

_ESCAPE_OPERATOR_RE = re.compile(r"([^A-Za-z0-9_./:-])")


def _escape_operator(operator: str) -> str:
    """跟 tools/fault_injector.py 的跳脫邏輯逐字元一致 (理由見該檔案內
    的長篇註解：docker_cmd 會先被 shlex.split() 解析一次，容器內的 bash
    才會解析第二次，只有反斜線跳脫能同時撐過兩層)。
    Character-for-character identical to tools/fault_injector.py's escaping
    (see that file's comment for why: docker_cmd is parsed once by
    shlex.split() and again by the container's bash; only backslash-escaping
    survives both passes)."""
    return _ESCAPE_OPERATOR_RE.sub(r"\\\1", operator)


def _normalize_injections(case: Dict[str, Any]) -> List[Dict[str, str]]:
    if "injections" in case:
        return case["injections"]
    return [case["injection"]]


def load_dataset(dataset_path: str) -> List[Dict[str, Any]]:
    with open(dataset_path, "r", encoding="utf-8") as f:
        return json.load(f)


def prepare_broken_workspace(case: Dict[str, Any], dest_dir: str) -> str:
    """
    在一個拋棄式容器內對 case['broken_commit'] 做
    checkout -> west update --narrow -> 套用 mutation，完全比照
    tools/fault_injector.py._run() 已經拿來驗證過整個資料集的流程 (同一棵
    /zephyrproject/zephyr 樹、同一種 operator 跳脫方式)，再把容器內產生的
    /zephyrproject/zephyr 複製到 host 上的 dest_dir，移除 .git，回傳
    dest_dir 的絕對路徑。

    只有這個函式讀取 case['broken_commit']/['injection']/['injections']；
    回傳值只是一個檔案系統路徑，這幾個欄位的內容不會出現在回傳值、
    workspace 內容，或任何寫出的檔案裡。

    Runs checkout -> west update --narrow -> mutation-apply inside a
    disposable container, mirroring tools/fault_injector.py._run() exactly
    (same /zephyrproject/zephyr tree, same operator-escaping scheme) — the
    process this dataset was originally verified with. Copies the
    container's resulting /zephyrproject/zephyr out to dest_dir on the
    host, strips .git, and returns dest_dir's absolute path.

    Only this function ever reads case['broken_commit']/['injection']/
    ['injections']; the return value is just a filesystem path — none of
    those fields' contents end up in the return value, the workspace
    contents, or any file written to disk.
    """
    case_id = case["id"]
    injections = _normalize_injections(case)
    broken_commit = case["broken_commit"]
    container_name = f"evalprep_{case_id}_{int(time.time() * 1000)}"

    mutate_cmds = [
        f"python3 {MUTATE_SCRIPT_CONTAINER_PATH} /zephyrproject/zephyr/{inj['target_file']} "
        f"{_escape_operator(inj['operator'])}"
        for inj in injections
    ]

    inner_script = (
        "cd /zephyrproject/zephyr && "
        f"git fetch origin {broken_commit} && "
        f"git checkout {broken_commit} && "
        "west update --narrow && "
        + " && ".join(mutate_cmds)
    )

    docker_run_cmd = [
        "docker", "run", "-i", "--name", container_name,
        "--cpus=2", "--memory=2400m",
        "-v", f"{MUTATE_SCRIPT_HOST_PATH}:{MUTATE_SCRIPT_CONTAINER_PATH}:ro",
        "zephyr-sandbox", "bash", "-c", inner_script,
    ]

    logger.info(f"[{case_id}] 準備 broken workspace (checkout {broken_commit[:12]} + 套用 mutation)...")
    try:
        result = subprocess.run(docker_run_cmd, capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            raise RuntimeError(
                f"workspace prep failed for case '{case_id}' (container exit {result.returncode}):\n"
                f"--- stdout tail ---\n{result.stdout[-4000:]}\n"
                f"--- stderr tail ---\n{result.stderr[-2000:]}"
            )

        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir)
        os.makedirs(os.path.dirname(os.path.abspath(dest_dir)), exist_ok=True)

        logger.info(f"[{case_id}] 從容器複製 /zephyrproject/zephyr -> {dest_dir} ...")
        cp_result = subprocess.run(
            ["docker", "cp", f"{container_name}:/zephyrproject/zephyr", dest_dir],
            capture_output=True, text=True, timeout=300,
        )
        if cp_result.returncode != 0:
            raise RuntimeError(f"docker cp failed for case '{case_id}': {cp_result.stderr}")
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=30)

    # 硬性要求 1：拿掉 .git，讓 `git checkout -- .`/`git restore .` 這種
    # 完全不需要讀懂錯誤訊息的投機解法失效。
    # Hard requirement 1: strip .git so `git checkout -- .`/`git restore .`
    # — a shortcut that needs zero understanding of the error — can't work.
    git_path = os.path.join(dest_dir, ".git")
    if os.path.isdir(git_path):
        shutil.rmtree(git_path)
    elif os.path.exists(git_path):
        os.remove(git_path)

    _assert_no_git_residue(dest_dir, case_id)

    return os.path.abspath(dest_dir)


def _assert_no_git_residue(workspace_dir: str, case_id: str) -> None:
    """README 硬性要求第 3 點：把「workspace 底下確實沒有 .git」寫成程式碼
    自己的驗收測試，不能只靠人工檢查。
    README's hard requirement point 3: turn "no .git residue in the
    workspace" into the script's own assertion, not something checked only
    by hand."""
    for root, dirs, files in os.walk(workspace_dir):
        if ".git" in dirs or ".git" in files:
            raise AssertionError(
                f"[{case_id}] workspace still contains .git residue at {root} — "
                "refusing to hand this workspace to an agent"
            )


def build_agent_initial_state(case: Dict[str, Any], workspace_path: str, max_iters: int) -> ZephyrAgentState:
    """
    只從 case 裡挑出 _AGENT_VISIBLE_CASE_FIELDS 白名單裡的欄位餵給
    create_initial_state()。硬性要求 2 的驗收測試：明確斷言白名單本身
    不含任何一個絕對不能外洩的欄位——防的是「以後改白名單時手滑把
    injection 之類的欄位也加進去」這種未來才會發生的錯誤，而不是現在的
    case dict。

    Only fields in _AGENT_VISIBLE_CASE_FIELDS are ever read from `case` and
    passed to create_initial_state(). Hard requirement 2's acceptance
    check: assert the allowlist itself never contains a field that must
    never leak — guards against someone later editing the allowlist to
    accidentally include `injection` or similar, not against today's case
    dict.
    """
    assert not (_AGENT_VISIBLE_CASE_FIELDS & _NEVER_AGENT_VISIBLE_FIELDS), (
        "_AGENT_VISIBLE_CASE_FIELDS allowlist has been edited to include a field "
        "that must never reach the agent"
    )

    return create_initial_state(
        workspace_path=workspace_path,
        initial_log=case.get("initial_error_log", ""),
        max_iters=max_iters,
        board=case.get("board", "qemu_x86"),
        target_app=case.get("target_app", "."),
        required_pass_test=case.get("target_test"),
    )


def verify_reproduces_initial_failure(case: Dict[str, Any], workspace_path: str) -> Dict[str, Any]:
    """
    在把 workspace 交給 LangGraph 迴圈之前，先用完全跟 devops_node 相同的
    docker 指令 (build_devops_docker_cmd) 跑一次「什麼都還沒修」的建置，
    確認真的重現預期的失敗——防的是環境漂移 (映像檔更新、broken_commit
    在 upstream 被 rebase 等) 讓某個案例的初始狀態悄悄變成「其實一開始就
    建置成功」，那樣不管 agent 有沒有真的修，都會被誤判為修復成功。

    不會、也不需要傳入 required_pass_test：這裡只是確認案例還在壞，不是
    在評分一次修復嘗試。

    Before handing the workspace to the LangGraph loop, runs one "nothing
    patched yet" build using the exact same docker command devops_node uses
    (build_devops_docker_cmd), confirming it actually reproduces the
    expected failure — guards against environment drift (image updates,
    broken_commit rebased upstream, etc.) silently turning a case's initial
    state into "actually builds fine already", which would make any repair
    attempt (real or not) look like a false success.

    Deliberately doesn't pass required_pass_test: this only confirms the
    case is still broken, it isn't grading a repair attempt.
    """
    category = case.get("category", "")
    wait_for_completion = category in ("runtime_crash", "compound")
    docker_cmd = build_devops_docker_cmd(workspace_path, case.get("board", "qemu_x86"), case.get("target_app", "."))
    oracle = QemuOracle(timeout=600)
    result = oracle.evaluate(docker_cmd, wait_for_completion=wait_for_completion)
    return result


def run_case(case: Dict[str, Any], runs_dir: str, max_iters: int, skip_repro_check: bool) -> Dict[str, Any]:
    case_id = case["id"]
    dest_dir = os.path.join(runs_dir, case_id)

    workspace_path = prepare_broken_workspace(case, dest_dir)

    if not skip_repro_check:
        repro_result = verify_reproduces_initial_failure(case, workspace_path)
        logger.info(
            f"[{case_id}] 初始重現檢查: status={repro_result['status']} "
            f"(資料集記錄的預期 error_type={case.get('error_type')})"
        )
        if repro_result["status"] == "success":
            raise RuntimeError(
                f"[{case_id}] workspace 準備完成後直接建置成功，沒有重現預期的失敗——"
                "案例可能已經因環境漂移失效，拒絕交給 agent 修復一個其實沒壞的專案。"
            )

    state = build_agent_initial_state(case, workspace_path, max_iters)
    graph = build_zephyr_graph()

    logger.info(f"[{case_id}] 開始 LangGraph 閉環修復...")
    final_status = "in_progress"
    total_iterations = 0
    for step_event in graph.stream(state):
        for node_name, updated_state in step_event.items():
            logger.info(f"[{case_id}] 節點 [{node_name}] 執行完畢.")
            total_iterations = updated_state.get("iterations", total_iterations)
            if updated_state.get("final_status") in ("resolved", "failed_max_retries"):
                final_status = updated_state["final_status"]

    return {
        "case_id": case_id,
        "category": case.get("category"),
        "final_status": final_status,
        "iterations": total_iterations,
        "workspace_path": workspace_path,
    }


def select_cases(dataset: List[Dict[str, Any]], args: argparse.Namespace) -> List[Dict[str, Any]]:
    if args.case_id:
        matches = [c for c in dataset if c["id"] == args.case_id]
        if not matches:
            raise SystemExit(f"case id '{args.case_id}' not found in {args.dataset}")
        return matches
    if args.limit is not None:
        return dataset[: args.limit]
    if args.all:
        return dataset
    raise SystemExit(
        "拒絕預設跑全部案例：請指定 --case-id <id>、--limit N (先跑一小批)，"
        "或明確加上 --all (真的要跑全部 143 筆時)。\n"
        "Refusing to default to running every case: pass --case-id <id>, "
        "--limit N (pilot a small batch first), or explicitly pass --all."
    )


def main():
    parser = argparse.ArgumentParser(description="Zephyr-Eval evaluation runner (Phase 1 prototype).")
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH, help="Path to a cases JSON file (final_dataset.json schema).")
    parser.add_argument("--case-id", help="Run exactly one case by its 'id' field.")
    parser.add_argument("--limit", type=int, help="Run only the first N cases (for piloting).")
    parser.add_argument("--all", action="store_true", help="Run every case in the dataset. Requires explicit opt-in.")
    parser.add_argument("--runs-dir", default=os.path.join("eval_runs", time.strftime("%Y%m%d_%H%M%S")),
                         help="Directory to prepare per-case workspaces under.")
    parser.add_argument("--max-retries", type=int, default=5, help="max_iterations passed to create_initial_state.")
    parser.add_argument("--skip-repro-check", action="store_true",
                         help="Skip the pre-flight 'does the freshly prepared workspace still reproduce the "
                              "expected failure' build (saves one build, but loses the environment-drift guard).")
    parser.add_argument("--results-out", help="Where to write the JSON results summary (default: <runs-dir>/results.json).")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    cases = select_cases(dataset, args)
    logger.info(f"選定 {len(cases)} / {len(dataset)} 筆案例，workspace 將準備於 {args.runs_dir}")

    results = []
    for case in cases:
        try:
            results.append(run_case(case, args.runs_dir, args.max_retries, args.skip_repro_check))
        except Exception as e:
            logger.error(f"[{case['id']}] 執行失敗: {e}")
            results.append({"case_id": case["id"], "category": case.get("category"), "final_status": "error", "error": str(e)})

    results_out = args.results_out or os.path.join(args.runs_dir, "results.json")
    os.makedirs(os.path.dirname(results_out) or ".", exist_ok=True)
    with open(results_out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    resolved = sum(1 for r in results if r.get("final_status") == "resolved")
    logger.info(f"完成：{resolved}/{len(results)} 案例修復成功。結果寫入 {results_out}")


if __name__ == "__main__":
    main()
