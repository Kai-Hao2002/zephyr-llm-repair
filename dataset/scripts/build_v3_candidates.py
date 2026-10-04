# dataset/scripts/build_v3_candidates.py
"""
從 v2 資料集產生 v3 的「搬遷」候選案例：每個 test app 只取一筆、注入檔不重複、
類別依 max-flow 分配 (稀缺類別優先)、commit 分散在 2026-03-17..08 每週一個、
約三成改到 QEMU 板子 (qemu_cortex_m3 只排在 2026-05-07 以後)。
已在 pilot 通過的 app 不再產生。結果交給 tools/validate_v3_cases.py 驗證。

Builds v3 'migrated' candidates from the v2 dataset: one case per test app, no injected
file reused, categories assigned by max-flow (scarce categories first), base commits spread
weekly over 2026-03-17..08, ~30% moved to QEMU boards (qemu_cortex_m3 only on commits
>= 2026-05-07). Apps already accepted in the pilot are skipped.

Usage: python dataset/scripts/build_v3_candidates.py <commits.txt> <out.json>
  commits.txt: "<sha> <YYYY-MM-DD>" per line
"""
import collections
import json
import sys

import networkx as nx

V2 = "dataset/cases/final_dataset.json"
PILOT = "dataset/v3/validation/pilot_validation.json"
PILOT_FIXED = "dataset/v3/validation/pilot_validation_fixed.json"
PILOT_CANDS = ["dataset/v3/candidates/pilot_candidates.json", "dataset/v3/candidates/pilot_candidates_r2.json",
               "dataset/v3/candidates/pilot_candidates_r3.json"]
TARGET = {"dts": 10, "compound": 13, "kconfig": 16, "c_syntax": 19, "runtime_crash": 62}
WEIGHT = {"dts": -10, "compound": -8, "kconfig": -6, "c_syntax": -2, "runtime_crash": -3}
QEMU_BOARDS = ["qemu_x86", "qemu_riscv32", "qemu_cortex_m3"]
ARM_MIN_DATE = "2026-05-07"
# 可以搬到 QEMU 的 app：不依賴 native_sim 專屬的模擬驅動/overlay
# Apps that can move to QEMU: no dependency on native_sim-only emulators/overlays
QEMU_OK_PREFIXES = ("tests/kernel/", "tests/lib/", "tests/misc/", "tests/subsys/portability/",
                    "tests/subsys/zbus/")


def injections(c):
    return c.get("injections") or [c["injection"]]


def pilot_accepted():
    cands = {c["id"]: c for p in PILOT_CANDS for c in json.load(open(p))}
    ok = {}
    for p in (PILOT, PILOT_FIXED):
        for r in json.load(open(p)):
            if r.get("accepted"):
                ok[r["id"]] = cands[r["id"]]
    return ok


def main(commits_path, out_path):
    commits = [l.split() for l in open(commits_path) if l.strip()]
    v2 = json.load(open(V2))
    pilot = pilot_accepted()
    used_apps = {c["target_app"] for c in pilot.values()}
    used_files = {i["target_file"] for c in pilot.values() for i in injections(c)}

    by_app = collections.defaultdict(list)
    for c in v2:
        if c["target_app"].startswith("samples/") or c["target_app"] in used_apps:
            continue
        by_app[c["target_app"]].append(c)

    # 類別分配：max-flow，容量 = 目標數 - pilot 已有的數量
    # Category assignment by max-flow; capacity = target minus what the pilot already has
    have = collections.Counter(c["category"] for c in pilot.values())
    G = nx.DiGraph()
    for app, cs in by_app.items():
        G.add_edge("s", app, capacity=1, weight=0)
        for cat in {c["category"] for c in cs}:
            G.add_edge(app, cat, capacity=1, weight=WEIGHT[cat])
    for cat, t in TARGET.items():
        G.add_edge(cat, "t", capacity=max(0, t - have[cat]), weight=0)
    flow = nx.max_flow_min_cost(G, "s", "t")

    picked, skipped = [], []
    for app in sorted(by_app):
        cat = next((k for k, v in flow[app].items() if v), None)
        if cat is None:
            continue
        # 同類別多筆時：優先選有 target_test、注入檔還沒用過的
        # Several cases of that category: prefer one with target_test and unused injected files
        opts = sorted((c for c in by_app[app] if c["category"] == cat),
                      key=lambda c: (c.get("target_test") is None, c["id"]))
        choice = next((c for c in opts if not {i["target_file"] for i in injections(c)} & used_files), None)
        if choice is None:
            skipped.append((app, cat, "injected file already used"))
            continue
        used_files |= {i["target_file"] for i in injections(choice)}
        picked.append(choice)

    # 板子：v2 原本就是 QEMU 的保留；其餘符合條件的 runtime/c_syntax 依序分配，直到全資料集約三成
    # Boards: keep v2's QEMU boards; move eligible runtime/c_syntax apps until ~30% overall
    n_total_goal = sum(TARGET.values())
    qemu_goal = round(0.3 * n_total_goal) - sum(c["board"] != "native_sim" for c in pilot.values())
    out = []
    qi = 0
    for c in picked:
        board = c["board"]
        native_specific = any("native" in i["target_file"] for i in injections(c))
        if (board == "native_sim" and qemu_goal > 0 and c["category"] in ("runtime_crash", "c_syntax")
                and c["target_app"].startswith(QEMU_OK_PREFIXES) and not native_specific):
            board = QEMU_BOARDS[qi % 3]
            qi += 1
        if board != "native_sim":
            qemu_goal -= 1
        out.append((c, board))

    # commit：依序輪流，ARM 只用 >= 2026-05-07 的 commit；pilot 已佔的 commit 先計入
    # Commits round-robin; ARM only on >= 2026-05-07; count commits the pilot already uses
    load = collections.Counter(c["broken_commit"] for c in pilot.values())
    date = {sha: d for sha, d in commits}
    result = []
    for c, board in out:
        pool = [sha for sha, d in commits if board != "qemu_cortex_m3" or d >= ARM_MIN_DATE]
        sha = min(pool, key=lambda s: (load[s], date[s]))
        load[sha] += 1
        x = {k: v for k, v in c.items() if k not in ("initial_error_log", "error_type", "title")}
        x["v2_source"] = c["id"]
        x["id"] = f"v3_{c['id'].removeprefix('inject_')}_{sha[:7]}"
        x["broken_commit"] = x["fixed_commit"] = sha
        x["board"] = board
        result.append(x)
    json.dump(result, open(out_path, "w"), indent=1, ensure_ascii=False)
    print(f"pilot accepted: {len(pilot)}  migrated candidates: {len(result)}")
    print("categories (pilot + migrated):", dict(have + collections.Counter(x["category"] for x in result)))
    print("still needed from new apps:", {k: TARGET[k] - have[k] - sum(x['category'] == k for x in result) for k in TARGET})
    print("boards:", dict(collections.Counter(x["board"] for x in result)))
    print("skipped:", skipped)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
