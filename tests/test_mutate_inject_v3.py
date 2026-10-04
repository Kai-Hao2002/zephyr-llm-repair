"""Tests for the v3 mutation operators/hints in tools/mutate_inject.py.
Run: python tests/test_mutate_inject_v3.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.mutate_inject import MUTATION_OPERATORS as OPERATORS

KCONF = "config FCB\n\tbool \"FCB\"\n\tdepends on FLASH_MAP\n\tselect CRC\n\nconfig OTHER\n\tdepends on FLASH_MAP\n"


def test_break_phandle_custom_label_and_default_unchanged():
    dts = "/ { aliases { gnss = &gnss_emul; }; };\n"
    assert OPERATORS["dts_break_phandle"](dts, "gnss_emul=>gnss_emu") == "/ { aliases { gnss = &gnss_emu; }; };\n"
    assert "_broken_ref" in OPERATORS["dts_break_phandle"](dts, None)


def test_remove_semicolon_hint_targets_statement():
    c = "K_THREAD_DEFINE(t, 1, f);\nvoid g(void)\n{\n\tfoo(1);\n\tfoo(1);\n}\n"
    out = OPERATORS["c_remove_semicolon"](c, "foo(1);#2")
    assert out == "K_THREAD_DEFINE(t, 1, f);\nvoid g(void)\n{\n\tfoo(1);\n\tfoo(1)\n}\n"
    assert OPERATORS["c_remove_semicolon"](c, None).startswith("K_THREAD_DEFINE(t, 1, f)\n")  # old default kept


def test_typo_identifier_whole_word_and_occurrence():
    c = "ring_buf_put_claim(x);\nring_buf_put(a);\nring_buf_put(b);\n"
    assert OPERATORS["c_typo_identifier"](c, "ring_buf_put#2:ring_buf_pt") == \
        "ring_buf_put_claim(x);\nring_buf_put(a);\nring_buf_pt(b);\n"
    assert OPERATORS["c_typo_identifier"](c, "nope:x") is None


def test_kconfig_typo_depends_only_in_block():
    out = OPERATORS["kconfig_typo_depends"](KCONF, "FCB=>FLASH_MAPP")
    assert "config FCB\n\tbool \"FCB\"\n\tdepends on FLASH_MAPP\n" in out
    assert out.endswith("config OTHER\n\tdepends on FLASH_MAP\n")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
