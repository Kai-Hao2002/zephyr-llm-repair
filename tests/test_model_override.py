"""ZEPHYR_GEMINI_MODEL overrides both gemini roles; unset keeps the default table.
The override is read at import time, so each case runs in a fresh interpreter.
Run: python tests/test_model_override.py"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = ("import sys; sys.path.insert(0, %r); from core import llm_provider as p; "
         "p.set_single_model(%s); print(p.get_model_name('fast'), p.get_model_name('pro'))")


def _models(env_model, single, provider="gemini"):
    env = {k: v for k, v in os.environ.items() if k not in ("ZEPHYR_GEMINI_MODEL", "ZEPHYR_LLM_PROVIDER")}
    env["ZEPHYR_LLM_PROVIDER"] = provider
    if env_model is not None:
        env["ZEPHYR_GEMINI_MODEL"] = env_model
    out = subprocess.run([sys.executable, "-c", PROBE % (ROOT, single)], env=env,
                         capture_output=True, text=True, check=True).stdout.split()
    return tuple(out)


def test_default_without_override():
    assert _models(None, True) == ("gemini-3.8-flash", "gemini-3.8-flash")


def test_override_both_roles():
    assert _models("gemini-2.5-flash", False) == ("gemini-2.5-flash", "gemini-2.5-flash")
    assert _models("gemini-2.5-flash", True) == ("gemini-2.5-flash", "gemini-2.5-flash")


def test_blank_override_ignored():
    assert _models("  ", True) == ("gemini-3.8-flash", "gemini-3.8-flash")


def test_override_does_not_touch_other_providers():
    assert _models("gemini-2.5-flash", False, "anthropic") == ("claude-haiku-4-5-20251001", "claude-sonnet-5")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
