#!/usr/bin/env python3
# tools/mutate_inject.py
"""
在容器內對目標檔案套用/還原「合成錯誤注入」的 mutation operator。
這支腳本不依賴任何第三方套件 (純標準庫)，由 tools/fault_injector.py 透過
bind-mount 掛進 zephyr-sandbox 容器後，以
    python3 mutate_inject.py <file> <operator> [--revert]
呼叫。每個 operator 都是一次定義明確、範圍侷限的文字層級變異，找不到可套用
的匹配時會回傳非 0 結束碼 (印出 "NO_MATCH: ...")，讓呼叫端能明確分辨
「這個 operator 對這個檔案不適用」跟「mutation 生效後真的讓建置/執行失敗」。

Applies/reverts a synthetic fault-injection mutation operator on a target
file inside the sandbox container. Pure stdlib, invoked by
tools/fault_injector.py after bind-mounting this script into the container:
    python3 mutate_inject.py <file> <operator> [--revert]
Each operator is a well-defined, narrowly scoped textual mutation. When no
match is found, it exits non-zero and prints "NO_MATCH: ..." so the caller
can distinguish "this operator doesn't apply to this file" from "the
mutation took effect and genuinely broke the build/run".
"""
import argparse
import re
import shutil
import sys
from typing import Callable, Dict, Optional


# ============================================================
# Kconfig 類別 mutation operators
# ============================================================

def _find_config_block(content: str, config_name: str) -> Optional[tuple]:
    """找出 `config NAME` / `menuconfig NAME` 區塊本體的 (start, end) 範圍
    (從宣告行之後，到下一個同層級的 config/menuconfig/endif/endmenu/menu 為
    止)。同一個符號名稱 (例如 select/depends 的目標) 常常在檔案裡出現不只
    一次，散落在互不相關的 config 區塊中；只用符號名稱去比對第一個相符的
    行，很容易抓到一個實際上沒被啟用、改了也不影響建置結果的區塊。鎖定到
    「哪個 config 區塊」比鎖定到「哪個符號名稱」更能精準命中我們真正要注入
    錯誤的地方。"""
    m = re.search(r'^(?:menuconfig|config)\s+' + re.escape(config_name) + r'\b[^\n]*\n', content, re.MULTILINE)
    if not m:
        return None
    start = m.end()
    next_m = re.search(r'^(?:config|menuconfig|endif|endmenu|menu)\b', content[start:], re.MULTILINE)
    end = start + next_m.start() if next_m else len(content)
    return start, end


def _kconfig_remove_select(content: str, hint: Optional[str] = None) -> Optional[str]:
    """刪除 `select <SYMBOL>` 行，破壞該符號原本會連帶啟用的依賴鏈。有給
    hint 時，hint 是要鎖定的 `config <hint>` 區塊名稱，只在該區塊內找
    select 行；沒給 hint 時退回原本「抓檔案裡第一個」的行為。"""
    if hint:
        block = _find_config_block(content, hint)
        if block is None:
            return None
        start, end = block
        pattern = re.compile(r'^[ \t]*select\s+[A-Za-z0-9_]+(?:\s+if\s+[^\n]+)?[ \t]*\n', re.MULTILINE)
        m = pattern.search(content, start, end)
        if not m:
            return None
        return content[:m.start()] + content[m.end():]
    pattern = re.compile(r'^[ \t]*select\s+[A-Za-z0-9_]+[ \t]*\n', re.MULTILINE)
    new_content, n = pattern.subn('', content, count=1)
    return new_content if n else None


def _kconfig_invert_depends(content: str, hint: Optional[str] = None) -> Optional[str]:
    """把 `depends on <SYMBOL>` 反轉成 `depends on !<SYMBOL>`，讓原本能被
    滿足的依賴關係變成無法被滿足。有給 hint 時，hint 是要鎖定的
    `config <hint>` 區塊名稱，只在該區塊內找 depends on 行；沒給 hint 時
    退回原本「抓檔案裡第一個」的行為。"""
    if hint:
        block = _find_config_block(content, hint)
        if block is None:
            return None
        start, end = block
        pattern = re.compile(r'^([ \t]*depends on\s+)([A-Za-z0-9_]+)([ \t]*)$', re.MULTILINE)
        m = pattern.search(content, start, end)
        if not m:
            return None
        return content[:m.start()] + f"{m.group(1)}!{m.group(2)}{m.group(3)}" + content[m.end():]
    pattern = re.compile(r'^([ \t]*depends on\s+)([A-Za-z0-9_]+)([ \t]*)$', re.MULTILINE)
    m = pattern.search(content)
    if not m:
        return None
    return content[:m.start()] + f"{m.group(1)}!{m.group(2)}{m.group(3)}" + content[m.end():]


# ============================================================
# Device Tree (DTS) 類別 mutation operators
# ============================================================

def _dts_remove_compatible(content: str, hint: Optional[str] = None) -> Optional[str]:
    """刪除 `compatible = "...";` 屬性，讓對應節點找不到 binding。有給 hint
    時只鎖定 compatible 字串等於 hint 的那一行，避免樸素地抓到檔案裡第一個
    compatible (可能是 build 不會實際檢查 binding 的根節點，改了也不影響
    建置結果)；沒給 hint 時退回原本「抓第一個」的行為。"""
    if hint:
        pattern = re.compile(r'^[ \t]*compatible\s*=\s*"' + re.escape(hint) + r'"\s*;\s*\n', re.MULTILINE)
    else:
        pattern = re.compile(r'^[ \t]*compatible\s*=\s*"[^"]*"\s*;\s*\n', re.MULTILINE)
    new_content, n = pattern.subn('', content, count=1)
    return new_content if n else None


def _dts_break_phandle(content: str, hint: Optional[str] = None) -> Optional[str]:
    """把 `&label` phandle 參照改成一個不存在的標籤，模擬節點參照錯誤。有給
    hint 時只鎖定 `&hint` 這個特定的參照，沒給 hint 時退回原本「抓第一個」
    的行為。"""
    label_pattern = re.escape(hint) if hint else r'[A-Za-z_][A-Za-z0-9_]*'
    pattern = re.compile(r'&(' + label_pattern + r')\b')
    m = pattern.search(content)
    if not m:
        return None
    return content[:m.start()] + f"&{m.group(1)}_broken_ref" + content[m.end():]


def _dts_corrupt_reg(content: str, hint: Optional[str] = None) -> Optional[str]:
    """把第一個找到的 `reg = <...>;` 屬性刪掉最後一個 cell，讓暫存器長度不合法。"""
    pattern = re.compile(r'(reg\s*=\s*<)([^>]+)(>\s*;)')
    m = pattern.search(content)
    if not m:
        return None
    cells = m.group(2).split()
    if len(cells) < 2:
        return None
    corrupted = " ".join(cells[:-1])
    return content[:m.start()] + m.group(1) + corrupted + m.group(3) + content[m.end():]


# ============================================================
# C 語法/巨集類別 mutation operators
# ============================================================

def _c_remove_semicolon(content: str, hint: Optional[str] = None) -> Optional[str]:
    """刪掉第一個以 `);` 結尾的函式呼叫敘述句的分號，製造編譯期語法錯誤。"""
    pattern = re.compile(r'(\)\s*);(\s*\n)')
    m = pattern.search(content)
    if not m:
        return None
    return content[:m.start()] + m.group(1) + m.group(2) + content[m.end():]


def _c_remove_closing_brace(content: str, hint: Optional[str] = None) -> Optional[str]:
    """刪掉檔案裡最後一個獨立成行的 `}`，破壞大括號配對。"""
    matches = list(re.finditer(r'^\}\s*$', content, re.MULTILINE))
    if not matches:
        return None
    m = matches[-1]
    return content[:m.start()] + content[m.end():]


def _c_typo_macro(content: str, hint: Optional[str] = None) -> Optional[str]:
    """把第一個 `DT_NODELABEL` 巨集呼叫改成拼錯的名稱，模擬未定義符號錯誤。"""
    pattern = re.compile(r'\bDT_NODELABEL\b')
    m = pattern.search(content)
    if not m:
        return None
    return content[:m.start()] + "DT_NODELABE" + content[m.end():]


# ============================================================
# 執行期崩潰類別 mutation operators
# ============================================================

def _runtime_off_by_one(content: str, hint: Optional[str] = None) -> Optional[str]:
    """把 `x < y` 比較改成 `x <= y`，製造邊界檢查的 off-by-one 錯誤。

    hint="postinc_loop_bound" 時，鎖定 `ident++ < ident2` 這種寫在
    do/while 迴圈結尾的邊界寫法 (例如 `while (i++ < cnt)`)——這種迴圈邊界
    通常控制「還要不要再繼續掃下一個」，多繞一圈很容易讓呼叫端把不該用的
    項目當成可用的而錯誤地跨界存取，比起單純把一個「安全餘裕」比較改嚴格
    (那種只是讓函式更早回傳 -ENOSPC，屬於不痛不癢的保守方向) 更容易被測試
    套件實際命中並產生真正的當機。

    沒給 hint 時退回原本「抓第一個非前處理器行、且不是 `ident++` 的
    `x < y`」的樸素掃描：會跳過 `#include <...>` / `#define` 等前處理器
    行，因為 `#include <stddef.h>` 的 `include` 和 `stddef` 都符合識別字
    模式，樸素的掃描會誤把它當成比較運算式，改出語法錯誤而非真正的邊界
    錯誤。

    hint="postinc_loop_bound" pins the mutation to a `ident++ < ident2`
    loop-bound idiom (e.g. `while (i++ < cnt)`) — this kind of bound
    controls "keep scanning or stop", so running one extra iteration is
    much more likely to make a caller treat an out-of-range item as valid
    and actually crash, versus just tightening a "safety margin" comparison
    (which merely makes a function return -ENOSPC a bit earlier — harmless).

    Without a hint, falls back to the original naive scan: the first
    non-preprocessor-line `x < y` that isn't actually a `ident++ < ...`
    idiom. Preprocessor lines are skipped because `#include <stddef.h>`'s
    `include`/`stddef` both look like identifiers to the naive scanner,
    which would otherwise mutate it into a syntax error instead of a real
    boundary bug.
    """
    if hint == "postinc_loop_bound":
        pattern = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*)(\+\+)\s*<\s*([a-zA-Z_][a-zA-Z0-9_]*)\b')
        m = pattern.search(content)
        if not m:
            return None
        return content[:m.start()] + f"{m.group(1)}{m.group(2)} <= {m.group(3)}" + content[m.end():]

    pattern = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(\+\+)?\s*<\s*([a-zA-Z_][a-zA-Z0-9_]*)\b')
    for m in pattern.finditer(content):
        if m.group(2):
            continue
        line_start = content.rfind('\n', 0, m.start()) + 1
        line_end = content.find('\n', m.end())
        line = content[line_start:line_end if line_end != -1 else len(content)]
        if line.lstrip().startswith('#'):
            continue
        return content[:m.start()] + f"{m.group(1)} <= {m.group(3)}" + content[m.end():]
    return None


_LVALUE = r'[a-zA-Z_][a-zA-Z0-9_]*(?:\s*->\s*[a-zA-Z_][a-zA-Z0-9_]*)*'


def _runtime_remove_null_check(content: str, hint: Optional[str] = None) -> Optional[str]:
    """拿掉第一個 NULL 檢查的實際保護效果：`if (x != NULL) {` 換成永遠成立
    的 `if (1) {`；找不到的話改找 `if (x == NULL) {` 換成永遠不成立的
    `if (0) {`。支援 `a->b` 形式的成員存取，不只是單一識別字。"""
    pattern_ne = re.compile(r'if\s*\(\s*(' + _LVALUE + r')\s*!=\s*NULL\s*\)\s*\{')
    m = pattern_ne.search(content)
    if m:
        return content[:m.start()] + "if (1) {" + content[m.end():]

    pattern_eq = re.compile(r'if\s*\(\s*(' + _LVALUE + r')\s*==\s*NULL\s*\)\s*\{')
    m = pattern_eq.search(content)
    if m:
        return content[:m.start()] + "if (0) {" + content[m.end():]

    return None


MUTATION_OPERATORS: Dict[str, Callable[..., Optional[str]]] = {
    "kconfig_remove_select": _kconfig_remove_select,
    "kconfig_invert_depends": _kconfig_invert_depends,
    "dts_remove_compatible": _dts_remove_compatible,
    "dts_break_phandle": _dts_break_phandle,
    "dts_corrupt_reg": _dts_corrupt_reg,
    "c_remove_semicolon": _c_remove_semicolon,
    "c_remove_closing_brace": _c_remove_closing_brace,
    "c_typo_macro": _c_typo_macro,
    "runtime_off_by_one": _runtime_off_by_one,
    "runtime_remove_null_check": _runtime_remove_null_check,
}


def main():
    parser = argparse.ArgumentParser(description="Apply/revert a synthetic fault-injection mutation on a file.")
    parser.add_argument("file_path", help="Absolute path to the target file inside the sandbox")
    parser.add_argument(
        "operator",
        help="Mutation operator to apply. Accepts 'name' or 'name:hint' where hint pins the "
             "operator to a specific symbol/label/compatible-string instead of grabbing the "
             "first naive match in the file (choices: " + ", ".join(sorted(MUTATION_OPERATORS.keys())) + ")",
    )
    parser.add_argument("--revert", action="store_true", help="Restore the file from its .orig backup instead of mutating")
    args = parser.parse_args()

    if args.revert:
        backup_path = args.file_path + ".orig"
        try:
            shutil.copyfile(backup_path, args.file_path)
        except FileNotFoundError:
            print(f"NO_BACKUP: {backup_path} not found, nothing to revert", file=sys.stderr)
            sys.exit(1)
        print(f"REVERTED: {args.file_path}")
        sys.exit(0)

    op_name, _, hint = args.operator.partition(":")
    if op_name not in MUTATION_OPERATORS:
        print(f"NO_MATCH: unknown operator '{op_name}'", file=sys.stderr)
        sys.exit(1)

    with open(args.file_path, "r", encoding="utf-8") as f:
        original = f.read()

    mutate = MUTATION_OPERATORS[op_name]
    mutated = mutate(original, hint or None)

    if mutated is None:
        print(f"NO_MATCH: operator '{args.operator}' found nothing to mutate in {args.file_path}", file=sys.stderr)
        sys.exit(1)

    # 先備份原始內容，供 --revert 使用
    shutil.copyfile(args.file_path, args.file_path + ".orig")
    with open(args.file_path, "w", encoding="utf-8") as f:
        f.write(mutated)

    print(f"MUTATED: {args.file_path} via {args.operator}")
    sys.exit(0)


if __name__ == "__main__":
    main()
