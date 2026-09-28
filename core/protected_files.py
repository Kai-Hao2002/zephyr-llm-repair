"""
評測端的受保護檔案還原 (2026-09-28 決定，參照 SWE-bench 評分前還原測試檔的做法)。

target_app 底下除了被注入錯誤的檔案以外，其他檔案 (測試原始碼、prj.conf、
CMakeLists.txt、overlay…) 都是「規格」，不是可以拿來修的地方。2026-09-28 的
B1 抽查發現，沒有這層保護時 agent 會改寫測試讓它 skip、或改測試斷言讓指定的
測試 PASS，而且被判定為修好 (B3 81 筆修好裡有 21 筆動過這些檔案)。

evaluate.py 在準備好 workspace 後呼叫 register()，把這些檔案的原始版本快照到
workspace 外面；之後每次建置/StaticCheck 之前 restore() 會把它們還原、刪掉
agent 在 target_app 底下新增的檔案。登記資料放在模組層級，刻意不經過 agent
的 state：被豁免的檔案清單等於被注入檔案的清單，不能讓 agent 看到。

Evaluation-side restore of protected files (decided 2026-09-28, following
SWE-bench's practice of restoring test files before grading).

Everything under target_app except the injected files (test sources,
prj.conf, CMakeLists.txt, overlays...) is the specification, not a place to
fix things. The 2026-09-28 B1 audit found that without this guard agents
rewrote tests to skip, or changed test assertions so the required test
PASSed, and were judged resolved (21 of B3's 81 resolved cases touched these
files).

evaluate.py calls register() once the workspace is prepared, snapshotting
the original versions outside the workspace; before every build/StaticCheck,
restore() puts them back and deletes files the agent added under target_app.
The registry is module-level and deliberately bypasses the agent state: the
exempt list equals the injected-file list, which the agent must never see.
"""
import filecmp
import logging
import os
import shutil
from typing import Dict, Iterable, List

logger = logging.getLogger(__name__)

# workspace 絕對路徑 -> {"app_dir", "snapshot_dir", "exempt"}
# Absolute workspace path -> {"app_dir", "snapshot_dir", "exempt"}
_registry: Dict[str, Dict] = {}
# 這一輪到目前為止還原過的檔案，寫軌跡時由 pop_restored() 取出。
# Files restored so far this iteration, taken by pop_restored() when writing the trajectory.
_restored_this_iteration: Dict[str, List[str]] = {}


def _walk_files(root: str) -> List[str]:
    rel_paths = []
    for dirpath, _dirs, files in os.walk(root):
        for filename in files:
            rel_paths.append(os.path.relpath(os.path.join(dirpath, filename), root))
    return rel_paths


def register(workspace_path: str, target_app: str, exempt_files: Iterable[str], snapshot_dir: str) -> int:
    """
    快照 workspace/target_app 底下所有不在 exempt_files 裡的檔案到 snapshot_dir
    (必須在 workspace 外面)，回傳快照的檔案數。exempt_files 是相對 workspace
    根目錄的路徑。
    Snapshots every file under workspace/target_app not in exempt_files into
    snapshot_dir (which must be outside the workspace); returns the number of
    files snapshotted. exempt_files are workspace-root-relative paths.
    """
    workspace_path = os.path.abspath(workspace_path)
    snapshot_dir = os.path.abspath(snapshot_dir)
    if snapshot_dir.startswith(workspace_path + os.sep):
        raise ValueError("snapshot_dir must be outside the workspace, or the agent could read it")
    app_rel = os.path.normpath(target_app)
    app_dir = os.path.join(workspace_path, app_rel)
    exempt = {os.path.normpath(p) for p in exempt_files}

    if os.path.exists(snapshot_dir):
        shutil.rmtree(snapshot_dir)
    count = 0
    for rel in _walk_files(app_dir):
        if os.path.normpath(os.path.join(app_rel, rel)) in exempt:
            continue
        dest = os.path.join(snapshot_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(os.path.join(app_dir, rel), dest)
        count += 1
    _registry[workspace_path] = {"app_dir": app_dir, "app_rel": app_rel, "snapshot_dir": snapshot_dir, "exempt": exempt}
    return count


def unregister(workspace_path: str) -> None:
    _registry.pop(os.path.abspath(workspace_path), None)
    _restored_this_iteration.pop(os.path.abspath(workspace_path), None)


def restore(workspace_path: str) -> List[str]:
    """
    把受保護檔案還原成快照版本、刪掉 target_app 底下新增的非豁免檔案，回傳被
    還原或刪除的檔案 (相對 workspace 根目錄)。workspace 沒登記時不做任何事
    (例如 main.py 的 demo 或單元測試)。
    Restores protected files to their snapshot and deletes non-exempt files
    newly added under target_app; returns the restored or deleted files
    (workspace-root-relative). A workspace that was never registered is left
    alone (e.g. main.py's demo or unit tests).
    """
    entry = _registry.get(os.path.abspath(workspace_path))
    if entry is None:
        return []
    app_dir, app_rel, snapshot_dir, exempt = entry["app_dir"], entry["app_rel"], entry["snapshot_dir"], entry["exempt"]
    snapshot_files = set(_walk_files(snapshot_dir))
    changed = []
    for rel in sorted(snapshot_files):
        src, dest = os.path.join(snapshot_dir, rel), os.path.join(app_dir, rel)
        if not os.path.isfile(dest) or not filecmp.cmp(src, dest, shallow=False):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            changed.append(os.path.join(app_rel, rel))
    for rel in _walk_files(app_dir):
        ws_rel = os.path.normpath(os.path.join(app_rel, rel))
        if rel not in snapshot_files and ws_rel not in exempt:
            os.remove(os.path.join(app_dir, rel))
            changed.append(ws_rel)
    if changed:
        logger.info(f"Restored protected test-app files before building: {changed}")
        pending = _restored_this_iteration.setdefault(os.path.abspath(workspace_path), [])
        pending.extend(f for f in changed if f not in pending)
    return changed


def pop_restored(workspace_path: str) -> List[str]:
    """取出並清空這一輪還原過的檔案 (寫進軌跡的 restored_files)。
    Returns and clears the files restored this iteration (the trajectory's restored_files)."""
    return _restored_this_iteration.pop(os.path.abspath(workspace_path), [])
