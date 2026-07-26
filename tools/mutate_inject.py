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
    mutation 的影響範圍精確、可預期。

    也支援可選的函式/測試案例範圍限定，格式為
    "<scope_name>@<value_a>:<value_b>" (用 `_find_ztest_block` 鎖定，同時
    接受 ZTEST 巨集本體或一般 C 函式定義)：當同一個字面值在目標函式之前
    就已經在檔案裡其他地方出現過時 (例如某個完全不相關的函式也用了
    `K_PRIO_PREEMPT(0)`)，不加範圍限定的話「檔案裡第一次出現」很容易抓到
    那個無關的地方而不是我們真正要對調的那一對——這正是實測 (west build
    -t run 前，先讀 mutate 後的檔案內容確認) 在 msgq_thread_data_passing
    這個案例上踩到的坑，加上範圍限定才修正。

    沒有 hint 就直接判定無法套用——這個 operator 需要明確知道要對調的兩個
    字面值，沒有樸素的「檔案裡第一個/第二個優先權」這種通用退回邏輯 (優先
    權常數在同一個檔案裡出現的順序，不一定對應到「哪兩個執行緒之間對調才
    會影響排程結果」，樸素猜測很容易像先前 kconfig/reg off-by-one 的教訓
    一樣抓到不影響建置結果的地方)。

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

    Also supports an optional function/test-case scope, in the form
    "<scope_name>@<value_a>:<value_b>" (resolved via `_find_ztest_block`,
    which accepts either a ZTEST macro body or a plain C function
    definition): when the same literal value already appears elsewhere in
    the file *before* the target function (e.g. some unrelated function
    also happens to use `K_PRIO_PREEMPT(0)`), an unscoped "first occurrence
    in the file" search can land on that unrelated spot instead of the pair
    actually intended — exactly the trap hit empirically (caught by reading
    the mutated file's content before ever running west build) on the
    msgq_thread_data_passing case; scoping fixes it.

    Without a hint this operator always declines — it needs to be told
    exactly which two literal values to swap; there's no naive "first two
    priority constants in the file" fallback, since their textual order
    doesn't reliably correspond to "swapping these two actually changes the
    scheduling outcome" (the same naive-first-match trap documented for the
    kconfig and dts_reg_offbyone operators above).
    """
    if not hint:
        return None

    scope_name, sep, rest = hint.partition("@")
    if sep:
        block = _find_ztest_block(content, scope_name)
        if block is None:
            return None
        search_start, search_end = block
        swap_hint = rest
    else:
        search_start, search_end = 0, len(content)
        swap_hint = hint

    val_a, _, val_b = swap_hint.partition(":")
    if not val_b or val_a == val_b:
        return None

    pos_a = content.find(val_a, search_start, search_end)
    pos_b = content.find(val_b, search_start, search_end)
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


def _find_ztest_block(content: str, test_name: str) -> Optional[tuple]:
    """找出 `ZTEST(suite, test_name) { ... }` 這個測試函式本體的 (start, end)
    範圍 (從左大括號之後，到配對的右大括號為止，用括號計數找配對，不是
    抓下一個 ZTEST——測試函式內部常有巢狀的 for/if 區塊，樸素抓「下一行
    `}`」很容易抓到函式中間的區塊結尾)。跟 Kconfig 的 `_find_config_block`
    是同樣的設計理由：同一個字面文字 (例如 `k_sleep(K_MSEC(100));`) 常常在
    同一個檔案裡的不同測試案例中重複出現，只鎖定「哪個測試案例」才能精準
    命中我們真正要注入錯誤的地方。

    Finds the (start, end) span of a `ZTEST(suite, test_name) { ... }` test
    function's body (from just after the opening brace to the matching
    closing brace, found via brace counting — not "the next `}`", since
    test bodies routinely contain nested for/if blocks and a naive "next
    closing brace" would land on one of those instead). Same rationale as
    Kconfig's `_find_config_block`: the same literal text (e.g.
    `k_sleep(K_MSEC(100));`) often repeats verbatim across different test
    cases in the same file, so pinning to *which test case* is the only way
    to reliably hit the intended target.
    """
    # 用 [A-Z_]*ZTEST[A-Z_]* 而不是列舉 ZTEST/ZTEST_USER/ZTEST_F/ZTEST_USER_F
    # 這幾個標準巨集：有些測試檔案會自訂形如
    # `#define ZTEST_USER_OR_NOT ZTEST_USER` 這種本地巨集，呼叫慣例
    # (suite, test_name) 完全相同，只是巨集名稱多了前後綴——只要名稱裡包含
    # "ZTEST" 就一併認得，不用每遇到一種新的自訂變體就再加一次列舉。
    # Using [A-Z_]*ZTEST[A-Z_]* instead of enumerating
    # ZTEST/ZTEST_USER/ZTEST_F/ZTEST_USER_F: some test files define their
    # own local macro like `#define ZTEST_USER_OR_NOT ZTEST_USER`, with the
    # exact same (suite, test_name) calling convention, just a
    # different name — matching anything containing "ZTEST" covers these
    # without having to special-case every new local variant we run into.
    m = re.search(r'[A-Z_]*ZTEST[A-Z_]*\([A-Za-z0-9_]+,\s*' + re.escape(test_name) + r'\)\s*\n\{', content)
    if not m:
        # 不少會被注入錯誤的 k_sleep/k_yield 呼叫其實寫在一般的執行緒進入
        # 函式 (例如 thread_09(void *p1, void *p2, void *p3)) 裡，不是直接
        # 寫在 ZTEST(...) 巨集本體——退回成比對「一般 C 函式定義」的寫法。
        # A lot of the k_sleep/k_yield calls worth mutating live in plain
        # thread-entry functions (e.g. thread_09(void *p1, void *p2, void
        # *p3)), not directly inside a ZTEST(...) macro body — fall back to
        # matching a plain C function definition.
        m = re.search(r'\b' + re.escape(test_name) + r'\s*\([^;{}]*\)\s*\n\{', content)
    if not m:
        return None
    start = m.end()
    depth = 1
    i = start
    while i < len(content) and depth > 0:
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
        i += 1
    if depth != 0:
        return None
    return start, i - 1


def _c_api_substitute(content: str, hint: Optional[str] = None) -> Optional[str]:
    """在指定測試案例的函式本體內，把一段呼叫替換成另一段行為相關但語意
    不同的呼叫 (例如把 `k_sleep(K_MSEC(100));` 換成 `k_yield();`)——兩者
    語法上都合法，但阻塞/排程語意不同：`k_sleep` 會讓目前的執行緒無條件
    睡滿指定時間，讓包括「優先權比自己低」的所有就緒執行緒都有機會執行；
    `k_yield` 只會讓「優先權跟自己相同或更高」的就緒執行緒有機會執行，
    優先權更低的執行緒完全不會被排到，且沒有其他同/高優先權執行緒時幾乎
    立刻繼續執行、不會有任何延遲。這個語意差異在有低優先權執行緒依賴
    `k_sleep` 才能拿到 CPU 時間的地方會製造真正的 starvation。

    hint 格式為 "<test_name>:<old_call>:<new_call>" (以第一個、第二個冒號
    切開，old_call/new_call 皆為原始碼裡的字面文字，例如
    "test_sleep_cooperative:k_sleep(K_MSEC(100));:k_yield();")：先用
    `_find_ztest_block` 鎖定 `ZTEST(suite, test_name)` 這個測試函式本體，
    只在該函式內找 old_call 的第一次出現並換成 new_call。沒有 hint 就直接
    判定無法套用——理由與 `thread_priority_swap` 相同：呼叫的字面文字在
    同一個檔案裡常常重複出現在不同測試案例中，樸素的「檔案裡第一個」
    退回邏輯很容易抓錯地方。

    Replaces one call with a behaviorally-related but semantically
    different call (e.g. `k_sleep(K_MSEC(100));` -> `k_yield();`) inside a
    named test case's function body — both are syntactically valid, but
    their blocking/scheduling semantics differ: `k_sleep` unconditionally
    blocks the calling thread for the given duration, letting *every*
    ready thread run, including ones at a *lower* priority; `k_yield` only
    lets threads at the *same or higher* priority run, never a lower-
    priority one, and returns almost immediately if no such thread is
    ready. Where a lower-priority thread depends on a `k_sleep` call to
    ever get CPU time, this substitution creates genuine starvation.

    hint format is "<test_name>:<old_call>:<new_call>" (split on the first
    two colons; old_call/new_call are literal source text, e.g.
    "test_sleep_cooperative:k_sleep(K_MSEC(100));:k_yield();"): pins the
    mutation to the `ZTEST(suite, test_name)` function body via
    `_find_ztest_block`, then replaces the first occurrence of old_call
    with new_call inside just that function. Without a hint this operator
    always declines — same reasoning as `thread_priority_swap`: the same
    literal call text commonly repeats across different test cases in one
    file, so a naive "first occurrence in the file" fallback is unreliable.

    test_name may carry an optional "#N" suffix (e.g.
    "k_yield_entry#2:k_yield();:k_sleep(K_MSEC(50));") to target the Nth
    occurrence of old_call within that function's scope instead of the
    default first — the same literal call text often appears more than
    once in one function (e.g. testing "should yield to a higher-priority
    thread" and "should NOT yield to a lower-priority thread" back to
    back, both spelled `k_yield();`), and picking the wrong one silently
    tests the wrong thing. Deliberately not using "extra surrounding text
    as an anchor" to disambiguate instead, since that text would likely
    span a newline, and a literal newline can't safely survive
    `fault_injector.py`'s two-layer parse (shlex.split(), then the real
    bash inside the container) — bash collapses a backslash immediately
    followed by a newline as a line-continuation, not a literal newline.
    """
    if not hint:
        return None
    parts = hint.split(":", 2)
    if len(parts) != 3:
        return None
    test_name, old_call, new_call = parts
    if not test_name or not old_call:
        return None

    occurrence = 1
    if "#" in test_name:
        test_name, _, occurrence_str = test_name.rpartition("#")
        if not test_name or not occurrence_str.isdigit():
            return None
        occurrence = int(occurrence_str)
        if occurrence < 1:
            return None

    block = _find_ztest_block(content, test_name)
    if block is None:
        return None
    start, end = block

    idx = -1
    search_from = start
    for _ in range(occurrence):
        idx = content.find(old_call, search_from, end)
        if idx == -1:
            return None
        search_from = idx + len(old_call)
    return content[:idx] + new_call + content[idx + len(old_call):]


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
    "c_api_substitute": _c_api_substitute,
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
