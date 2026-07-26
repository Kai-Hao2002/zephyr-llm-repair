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


def _dts_reg_offbyone(content: str, hint: Optional[str] = None) -> Optional[str]:
    """對 `reg = <addr size>;` 的位址欄位做一個微小的數值位移 (加上一個
    delta)，讓它從「精準落在某個實體邊界內」悄悄跨到邊界外——不像
    `dts_corrupt_reg` 那樣整個刪掉一個 cell (那是結構性、建置期就會被
    devicetree 綁定檢查擋下來的破壞)，這裡只改一個十六進位數字，模擬
    「保留區大小算錯/邊界算式漏了一項」這種真實工程失誤，讓一段原本安全
    的 MMIO 存取變成真正跨出映射範圍的存取，在執行期才會現形。

    hint 格式為 "<addr_hex>:<delta_hex>" (兩者皆須是 `0x` 開頭的十六進位字
    串)，例如 "0x2000ff00:0x100"：鎖定檔案裡精確等於 addr_hex 的 `reg`
    位址值，把它加上 delta_hex 後寫回。沒有 hint 時退回「檔案裡第一個
    `reg = <addr size>;` 的位址值加 1」。

    Nudges the address cell of a `reg = <addr size>;` property by a small
    numeric delta, so it silently slips past a physical boundary it was
    supposed to stay within — unlike `dts_corrupt_reg` (which deletes a
    whole cell, a structural break caught by devicetree binding validation
    at build time), this only changes one hex value, modeling a real
    "miscalculated reserved-region size / off-by-one boundary arithmetic"
    engineering mistake that only manifests as a genuine out-of-bounds
    access at runtime.

    hint format is "<addr_hex>:<delta_hex>" (both must be `0x`-prefixed
    hex strings), e.g. "0x2000ff00:0x100": pins the mutation to the `reg`
    property whose address value exactly equals addr_hex, and adds
    delta_hex to it. Without a hint, falls back to "the first
    `reg = <addr size>;` in the file, address value + 1".
    """
    if hint:
        addr_str, _, delta_str = hint.partition(":")
        if not delta_str:
            return None
        try:
            old_addr = int(addr_str, 16)
            delta = int(delta_str, 16)
        except ValueError:
            return None
        pattern = re.compile(r'(reg\s*=\s*<\s*)' + re.escape(addr_str) + r'(\s+[^>]+>\s*;)')
        m = pattern.search(content)
        if not m:
            return None
        new_addr = old_addr + delta
        return content[:m.start()] + m.group(1) + f"0x{new_addr:x}" + m.group(2) + content[m.end():]

    pattern = re.compile(r'(reg\s*=\s*<\s*)(0x[0-9A-Fa-f]+|\d+)(\s+[^>]+>\s*;)')
    m = pattern.search(content)
    if not m:
        return None
    old = m.group(2)
    is_hex = old.lower().startswith("0x")
    new_val = int(old, 16 if is_hex else 10) + 1
    new_str = f"0x{new_val:x}" if is_hex else str(new_val)
    return content[:m.start()] + m.group(1) + new_str + m.group(3) + content[m.end():]


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


# ============================================================
# 執行緒排程類別 mutation operators
# ============================================================

def _thread_priority_swap(content: str, hint: Optional[str] = None) -> Optional[str]:
    """把兩個執行緒建立時指定的優先權數值對調 (例如 `K_PRIO_PREEMPT(0)`
    跟 `K_PRIO_PREEMPT(1)`，或是 `K_PRIO_COOP(n)` 跟 `K_PRIO_PREEMPT(n)`
    之間)，模擬「優先權重新指派算錯/複製貼上時對調了兩個執行緒」的真實
    工程失誤，語法上完全合法，但會讓排程順序整個變掉，製造 priority
    inversion/starvation 一類的執行期錯誤。

    hint 格式為 "<value_a>:<value_b>" (兩者皆為出現在原始碼裡的字面文字，
    例如 "K_PRIO_PREEMPT(0):K_PRIO_PREEMPT(1)")：分別找出 value_a、value_b
    在檔案裡「第一次出現」的位置，把這兩個位置的文字互換。若兩者在檔案裡
    都只出現一次，就是單純對調這兩個執行緒的優先權；若某個值重複出現多次
    (例如多個執行緒共用同一個優先權)，只有各自的第一次出現會被換掉，讓
    mutation 的影響範圍精確、可預期。沒有 hint 就直接判定無法套用——這個
    operator 需要明確知道要對調的兩個字面值，沒有樸素的「檔案裡第一個/
    第二個優先權」這種通用退回邏輯 (優先權常數在同一個檔案裡出現的順序，
    不一定對應到「哪兩個執行緒之間對調才會影響排程結果」，樸素猜測很容易
    像先前 kconfig/reg off-by-one 的教訓一樣抓到不影響建置結果的地方)。

    Swaps the literal priority values given to two thread-creation sites
    (e.g. `K_PRIO_PREEMPT(0)` and `K_PRIO_PREEMPT(1)`, or between
    `K_PRIO_COOP(n)` and `K_PRIO_PREEMPT(n)`), modeling a real
    "priority reassignment miscalculated / two threads' priorities swapped
    during a copy-paste" engineering mistake — syntactically valid either
    way, but it reshuffles scheduling order and can produce a genuine
    priority-inversion/starvation class of runtime bug.

    hint format is "<value_a>:<value_b>" (both are literal source text as
    they appear in the file, e.g. "K_PRIO_PREEMPT(0):K_PRIO_PREEMPT(1)"):
    finds the first occurrence of each in the file and swaps just those two
    occurrences. If a value happens to repeat elsewhere (e.g. several
    threads sharing one priority), only each value's first occurrence is
    touched, keeping the mutation's blast radius precise and predictable.
    Without a hint this operator always declines — it needs to be told
    exactly which two literal values to swap; there's no naive "first two
    priority constants in the file" fallback, since their textual order
    doesn't reliably correspond to "swapping these two actually changes the
    scheduling outcome" (the same naive-first-match trap documented for the
    kconfig and dts_reg_offbyone operators above).
    """
    if not hint:
        return None
    val_a, _, val_b = hint.partition(":")
    if not val_b or val_a == val_b:
        return None

    pos_a = content.find(val_a)
    pos_b = content.find(val_b)
    if pos_a == -1 or pos_b == -1:
        return None

    if pos_a < pos_b:
        first_start, first_val, first_repl = pos_a, val_a, val_b
        second_start, second_val, second_repl = pos_b, val_b, val_a
    else:
        first_start, first_val, first_repl = pos_b, val_b, val_a
        second_start, second_val, second_repl = pos_a, val_a, val_b

    if first_start + len(first_val) > second_start:
        return None  # overlapping matches — ambiguous, decline rather than guess

    return (
        content[:first_start] + first_repl
        + content[first_start + len(first_val):second_start] + second_repl
        + content[second_start + len(second_val):]
    )


MUTATION_OPERATORS: Dict[str, Callable[..., Optional[str]]] = {
    "kconfig_remove_select": _kconfig_remove_select,
    "kconfig_invert_depends": _kconfig_invert_depends,
    "dts_remove_compatible": _dts_remove_compatible,
    "dts_break_phandle": _dts_break_phandle,
    "dts_corrupt_reg": _dts_corrupt_reg,
    "dts_reg_offbyone": _dts_reg_offbyone,
    "c_remove_semicolon": _c_remove_semicolon,
    "c_remove_closing_brace": _c_remove_closing_brace,
    "c_typo_macro": _c_typo_macro,
    "runtime_off_by_one": _runtime_off_by_one,
    "runtime_remove_null_check": _runtime_remove_null_check,
    "thread_priority_swap": _thread_priority_swap,
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
