"""PROTOTYPE, scratch only: what the shared LogFilter fix would do, to count changed logs. Not repo code."""
import re, sys
sys.path.insert(0, "/Users/yangkaihao/zephyr-llm-repair")
from tools.log_filter import LogFilter
ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
FAILED_RE = re.compile(r"^FAILED:\s+(?:\[code=\d+\]\s+)?(\S+\.(?:obj|o))\b")
MACRO_RE = re.compile(r"^.*:\d+:\d+:\s+note:\s+in expansion of macro\b.*")
class Proto(LogFilter):
    max_macro = 8
    def compress_log(self, raw):
        base = super().compress_log(raw)
        lines = [ANSI.sub('', l).strip() for l in raw.splitlines()]
        units, macros = [], []
        for l in lines:
            m = FAILED_RE.match(l)
            if m and m.group(1) not in units: units.append(m.group(1))
            if MACRO_RE.match(l) and l not in macros and len(macros) < self.max_macro: macros.append(l)
        head = ""
        if units:
            head += "[Failed Compilation Units]\n" + "\n".join(f"FAILED: {u}" for u in units[:10]) + "\n\n"
        out = base
        if macros:
            out += "\n\n[Macro Expansion Sites]\n" + "\n".join(macros)
        # runtime tail first when it holds an assertion and build-phase text precedes it
        marker = "\n\n[Runtime Log Tail]\n"
        moved = False
        if marker in out:
            pre, tail = out.split(marker, 1)
            if self._FAILURE_RE.search(tail) and pre.strip():
                out = "[Runtime Log Tail]\n" + tail.rstrip() + "\n\n[Build Log Messages]\n" + pre.strip()
                moved = True
        return head + out, dict(units=len(units), macros=len(macros), moved=moved)
