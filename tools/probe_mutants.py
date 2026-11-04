# tools/probe_mutants.py
"""
為新的 runtime 案例找注入點：在一個容器裡對指定原始碼檔的每個比較運算子 (< <= > >=)
與 NULL 檢查逐一產生 mutant，增量編譯 + 執行 target_app 的 ztest，記錄哪些測試失敗。
只是「找候選」的工具；選中的 mutant 之後一律再經 tools/validate_v3_cases.py 完整驗證。

Finds injection sites for new runtime cases: in one container, applies one mutant at a time
(flip each < <= > >= comparison, neutralize NULL checks) to a source file, incrementally
rebuilds and runs the target app's ztest suite, and records which tests fail. Discovery only;
chosen mutants are always re-validated end to end with tools/validate_v3_cases.py.

Usage: venv/bin/python tools/probe_mutants.py <commit> <target_app> <source_file> <out.json>
       [--board native_sim] [--max 25]
"""
import argparse
import json
import re
import subprocess
import sys
import uuid

IDENT = r"[A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*|\[[^\]\n]{1,20}\])*"
OPERAND = rf"(?:{IDENT}|\d+[uUlL]*)(?:\s*[-+]\s*(?:{IDENT}|\d+))?"
CMP_RE = re.compile(rf"({IDENT})\s*(<=|>=|<(?![<=])|>(?![>=]))\s*({OPERAND})")
NULL_RE = re.compile(rf"if \(({IDENT}) (!=|==) NULL\)")
FLIP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}


def source_at(commit, path):
    return subprocess.run(["docker", "run", "--rm", "zephyr-sandbox", "bash", "-c",
                           f"cd /zephyrproject/zephyr && git show {commit}:{path}"],
                          capture_output=True, text=True, check=True).stdout


def code_lines(content):
    """(line_start_offset, line) for lines that are code, not preprocessor/comment."""
    pos, in_comment = 0, False
    for line in content.splitlines(keepends=True):
        s = line.strip()
        if in_comment:
            in_comment = "*/" not in s
        elif s.startswith("/*"):
            in_comment = "*/" not in s
        elif not (s.startswith("#") or s.startswith("//") or s.startswith("*")):
            yield pos, line
        pos += len(line)


def mutants(content, limit):
    out, seen = [], set()
    for start, line in code_lines(content):
        if "#include" in line or "->" not in line and "<" not in line and ">" not in line and "NULL" not in line:
            continue
        for m in CMP_RE.finditer(line):
            if line[max(0, m.start(2) - 1)] == "-":  # part of '->'
                continue
            old = m.group(0)
            new = f"{m.group(1)} {FLIP[m.group(2)]} {m.group(3)}"
            kind = "off_by_one"
            out.append((kind, start + m.start(), old, new))
        for m in NULL_RE.finditer(line):
            new = "if (1)" if m.group(2) == "!=" else "if (0)"
            out.append(("remove_null_check", start + m.start(), m.group(0), new))
    res = []
    for kind, off, old, new in out:
        if ":" in old or ":" in new:
            continue
        n = content.count(old, 0, off) + 1  # occurrence index of this span
        key = (old, n)
        if key in seen:
            continue
        seen.add(key)
        op = "runtime_off_by_one" if kind == "off_by_one" else "runtime_remove_null_check"
        res.append({"operator": f"{op}:{old}#{n}:{new}", "line": content.count("\n", 0, off) + 1,
                    "old": old, "new": new})
    return res[:limit]


def esc(s):
    return re.sub(r"([^A-Za-z0-9_./:-])", r"\\\1", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("commit"); ap.add_argument("app"); ap.add_argument("src"); ap.add_argument("out")
    ap.add_argument("--board", default="native_sim"); ap.add_argument("--max", type=int, default=25)
    a = ap.parse_args()
    content = source_at(a.commit, a.src)
    ms = mutants(content, a.max)
    print(f"{len(ms)} mutants for {a.src}", flush=True)
    run = (f"timeout 240 west build -b {a.board} -d /tmp/b -t run . > /tmp/run.log 2>&1; "
           "grep -E \"^\\s*(START|PASS|FAIL) - |Assertion failed|ZEPHYR FATAL|Segmentation fault|PROJECT EXECUTION|"
           "[0-9]: error:|FATAL ERROR: command\" /tmp/run.log | tail -60")
    script = [f"cd /zephyrproject/zephyr && git checkout -q {a.commit} && cd {a.app}",
              f"west build -b {a.board} -d /tmp/b -p always . > /tmp/build.log 2>&1 || tail -20 /tmp/build.log",
              "echo '@@@ BASELINE'", run]
    for i, m in enumerate(ms):
        script += [f"echo '@@@ MUTANT {i}'",
                   f"python3 /mutate.py /zephyrproject/zephyr/{a.src} {esc(m['operator'])} || echo NO_MATCH_X",
                   run,
                   f"python3 /mutate.py /zephyrproject/zephyr/{a.src} {esc(m['operator'])} --revert > /dev/null"]
    name = f"probe_{uuid.uuid4().hex[:8]}"
    r = subprocess.run(["docker", "run", "--rm", "--name", name, "--cpus=2", "--memory=2400m",
                        "-v", f"{sys.path[0]}/mutate_inject.py:/mutate.py:ro",
                        "zephyr-sandbox", "bash", "-c", " ; ".join(script)],
                       capture_output=True, text=True, timeout=7200)
    blocks = r.stdout.split("@@@ ")
    base = next((b for b in blocks if b.startswith("BASELINE")), "")
    base_pass = set(re.findall(r"PASS - (\w+)", base))
    results = {"baseline_raw": base[-3000:], "commit": a.commit, "app": a.app, "src": a.src, "board": a.board,
               "baseline_pass": sorted(base_pass),
               "baseline_ok": "PROJECT EXECUTION SUCCESSFUL" in base, "mutants": []}
    for b in blocks:
        if not b.startswith("MUTANT"):
            continue
        i = int(b.split()[1])
        m = dict(ms[i])
        m["applied"] = "NO_MATCH" not in b
        # 執行期 segfault 也會讓 west 印 "FATAL ERROR: command ... --target run"，有跑到測試就算有編譯成功
        # A runtime segfault also makes west print "FATAL ERROR: command ... --target run"
        m["built"] = "START - " in b or not re.search(r"\d: error:|FATAL ERROR: command", b)
        m["raw"] = b[-3000:]
        m["failed_tests"] = sorted(set(re.findall(r"FAIL - (\w+)", b)))
        starts = re.findall(r"START - (\w+)", b)
        m["crashed"] = bool(re.search(r"ZEPHYR FATAL|Segmentation fault", b))
        m["crash_in"] = starts[-1] if m["crashed"] and starts else None
        m["assertion"] = bool(re.search(r"Assertion failed", b))
        m["passed_all"] = "PROJECT EXECUTION SUCCESSFUL" in b
        results["mutants"].append(m)
    json.dump(results, open(a.out, "w"), indent=1)
    good = [m for m in results["mutants"] if m["applied"] and m["built"] and not m["passed_all"]
            and (1 <= len(m["failed_tests"]) <= 3 or (m["crashed"] and m["crash_in"]))]
    print(f"baseline_ok={results['baseline_ok']} pass={len(base_pass)} usable={len(good)}")
    for m in good:
        print(f"  L{m['line']} {m['old']!r}->{m['new']!r} fail={m['failed_tests']} crash_in={m['crash_in']}")


if __name__ == "__main__":
    main()
