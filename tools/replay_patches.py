# tools/replay_patches.py
"""
修好的案例：把被注入檔從原始內容開始重建「注入 → 依序套用每一輪的 patch」，
比對最後的內容與原始 (上游) 內容，判斷是完全還原、還是改法不同 (可能改了測試語意)。
純離線：原始檔用一個 Docker 容器從 image 的 git 歷史讀出，不呼叫 LLM、不改評測程式。

For resolved cases: rebuild each injected file as original -> mutation -> every iteration's
patch in order (the workspace keeps edits across iterations; injected files are never restored),
then compare the final content with the original upstream content: exact restore vs. a different
edit (which may change test semantics). Offline: originals come from the image's git history via
one Docker container.

Usage: python tools/replay_patches.py --runs eval_runs/v3_25flash --dataset dataset/cases/final_dataset_v3.json \
           --out-dir eval_runs/v3_25flash/analysis [--tag main]
"""
import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, REPO)
from failure_taxonomy import injected_files, load_pipeline_results  # noqa: E402
from mutate_inject import MUTATION_OPERATORS  # noqa: E402
from patch_applier import PatchApplier  # noqa: E402
from core.protected_files import spec_changes  # noqa: E402

PIPELINES = ["b1", "b2", "b3", "proposed"]

_FETCH = r"""
import json, subprocess, sys
out = {}
for key, commit, path in json.load(sys.stdin):
    r = subprocess.run(["git", "-C", "/zephyrproject/zephyr", "show", f"{commit}:{path}"], capture_output=True)
    out[key] = r.stdout.decode("utf-8") if r.returncode == 0 else None
print(json.dumps(out))
"""


def fetch_originals(requests: List[Tuple[str, str, str]]) -> Dict[str, Optional[str]]:
    r = subprocess.run(["docker", "run", "-i", "--rm", "zephyr-sandbox", "python3", "-c", _FETCH],
                       input=json.dumps(requests), capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def extra_file_content(case: Dict, rel: str) -> Optional[str]:
    """extra_files 的檔案 (例如 v2 自製測試) 不在 git 歷史裡，從 host 讀。"""
    import evaluate  # noqa: E402  (heavy import; only when needed)
    extra = case.get("extra_files") or (case.get("injections") or [case.get("injection") or {}])[0].get("extra_files") or {}
    if rel not in extra:
        return None
    return open(evaluate._resolve_extra_file_host_path(extra[rel]), encoding="utf-8").read()


def replay(case: Dict, res: Dict, originals: Dict[str, str]) -> Dict:
    injs = case.get("injections") or [case["injection"]]
    with tempfile.TemporaryDirectory() as ws:
        mutated = {}
        for inj in injs:
            path = inj["target_file"]
            content = originals[path]
            op, _, hint = inj["operator"].partition(":")
            content = MUTATION_OPERATORS[op](content, hint or None)
            assert content is not None, f"mutation did not apply: {case['id']} {path}"
            originals.setdefault(path, originals[path])
            os.makedirs(os.path.dirname(os.path.join(ws, path)), exist_ok=True)
            open(os.path.join(ws, path), "w", encoding="utf-8").write(content)
            mutated[path] = content
        applier = PatchApplier(ws)
        import logging
        applier.logger.setLevel(logging.CRITICAL)
        # 每一輪都核對：重建時套用到的被注入檔，要和實際紀錄 (applied_files ∩ 被注入檔) 一致；
        # 不一致 (例如 trajectory 的 patch 超過 20,000 字被截斷) 就標成 replay_mismatch，不下結論。
        # Check every iteration: injected files touched during replay must match the recorded
        # applied_files ∩ injected; on a mismatch (e.g. a patch clipped at 20,000 chars in the
        # trajectory) the case is marked replay_mismatch instead of being classified.
        inj_paths = {i["target_file"] for i in injs}
        mismatch = []
        for it in res.get("iteration_log") or []:
            t = it.get("trajectory") or {}
            patch = t.get("patch")
            got = set()
            if isinstance(patch, dict):
                fp = os.path.join(ws, patch.get("filepath", "").strip().strip("`").strip())
                if os.path.exists(fp):
                    open(fp, "w", encoding="utf-8").write(patch.get("content", ""))
                    got = {patch.get("filepath", "").strip().strip("`").strip()}
            elif isinstance(patch, str) and patch:
                got = set(applier.apply_patches(patch).get("applied_files") or [])
            if (got & inj_paths) != (set(t.get("applied_files") or []) & inj_paths):
                mismatch.append(it.get("iteration"))
            if it.get("resolved"):
                break
        if mismatch:
            return {"_mismatch": mismatch}
        files = {}
        for inj in injs:
            path = inj["target_file"]
            final = open(os.path.join(ws, path), encoding="utf-8").read()
            orig = originals[path]
            if final == orig:
                state = "exact_restore"
            elif final.split() == orig.split():
                state = "whitespace_only"
            elif final == mutated[path]:
                state = "untouched"
            else:
                state = "differs"
            # 2026-10-12 起的嚴格規格檢查 (只套用在 target_app 底下的被注入檔)，離線估計用。
            # The strict spec check (from 2026-10-12; injected files under target_app only), for offline estimates.
            spec = (spec_changes(orig, mutated[path], final, path)
                    if path.startswith(case["target_app"].rstrip("/") + "/") else [])
            diff = "" if state in ("exact_restore", "untouched") else "".join(difflib.unified_diff(
                orig.splitlines(True), final.splitlines(True), f"a/{path} (original)", f"b/{path} (final)", n=2))
            files[path] = {"state": state, "diff_vs_original": diff, "spec_changes": spec}
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tag", default="main")
    args = ap.parse_args()
    cases = {c["id"]: c for c in json.load(open(args.dataset))}
    resolved = []
    for p in PIPELINES:
        for r in load_pipeline_results(args.runs, p):
            if r["final_status"] == "resolved":
                resolved.append((p, r))
    need = {}
    for _, r in resolved:
        c = cases[r["case_id"]]
        for f in injected_files(c):
            need[(c["broken_commit"], f)] = c
    host = {}
    reqs = []
    for (commit, f), c in need.items():
        content = extra_file_content(c, f)
        if content is not None:
            host[f"{commit}:{f}"] = content
        else:
            reqs.append((f"{commit}:{f}", commit, f))
    fetched = fetch_originals(reqs) if reqs else {}
    fetched.update(host)

    rows = []
    for p, r in resolved:
        c = cases[r["case_id"]]
        originals = {f: fetched[f"{c['broken_commit']}:{f}"] for f in injected_files(c)}
        missing = [f for f, v in originals.items() if v is None]
        if missing:
            rows.append({"pipeline": p, "case_id": c["id"], "error": f"original not found: {missing}"})
            continue
        files = replay(c, r, originals)
        if "_mismatch" in files:
            rows.append({"pipeline": p, "case_id": c["id"], "category": c["category"], "overall": "replay_mismatch",
                         "mismatch_iterations": files["_mismatch"], "files": {}})
            continue
        states = [v["state"] for v in files.values()]
        overall = ("exact_restore" if all(s in ("exact_restore", "whitespace_only") for s in states)
                   else "differs" if "differs" in states else "partial")
        rows.append({"pipeline": p, "case_id": c["id"], "category": c["category"], "overall": overall,
                     "spec_violation": any(v["spec_changes"] for v in files.values()), "files": files})

    os.makedirs(args.out_dir, exist_ok=True)
    json.dump(rows, open(os.path.join(args.out_dir, f"replay_{args.tag}.json"), "w"), indent=1, ensure_ascii=False)
    with open(os.path.join(args.out_dir, f"replay_{args.tag}_diffs.md"), "w") as f:
        for x in rows:
            if x.get("overall") in ("differs", "partial") or x.get("error"):
                f.write(f"## [{x['pipeline']}] {x['case_id']} ({x.get('category')}) -> {x.get('overall') or x.get('error')}\n")
                for path, v in (x.get("files") or {}).items():
                    f.write(f"- {path}: {v['state']}\n")
                    if v["diff_vs_original"]:
                        f.write("```diff\n" + v["diff_vs_original"] + "```\n")
                f.write("\n")
    from collections import Counter
    for p in PIPELINES:
        print(p, dict(Counter(x.get("overall") or "error" for x in rows if x["pipeline"] == p)))


if __name__ == "__main__":
    main()
