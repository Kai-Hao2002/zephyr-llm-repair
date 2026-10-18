"""Tests for core/openhands_pipeline.py helpers that need neither Docker nor an LLM: the task text
(same information as the other pipelines, no injection details), the model string, and changed-file
detection. Run: python tests/test_openhands_pipeline.py"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.openhands_pipeline as oh
from agents.spec_rule import SPEC_RULE


def test_task_contains_log_build_command_and_spec_rule_only():
    state = {"target_app": "tests/lib/foo", "board": "native_sim", "current_error_log": "error: boom",
             "required_pass_test": "test_secret_name", "workspace_path": "/x"}
    task = oh.build_task(state)
    assert "error: boom" in task and SPEC_RULE in task
    assert "west build -b native_sim -d /tmp/build -p always -t run ." in task
    assert "cd /zephyrproject/zephyr/tests/lib/foo" in task
    assert "test_secret_name" not in task  # the other pipelines are not given the target test either


def test_litellm_model_follows_provider_settings():
    assert oh.litellm_model().startswith("gemini/")


def test_changed_files_detection():
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "a", ".cache"))
    for p in ("a/x.c", "a/y.c", "a/.cache/c"):
        open(os.path.join(root, p), "w").write("1")
    before = oh._snapshot(root)
    time.sleep(0.01)
    open(os.path.join(root, "a/x.c"), "w").write("22")
    open(os.path.join(root, "a/new.c"), "w").write("3")
    open(os.path.join(root, "a/.cache/c"), "w").write("44")
    os.remove(os.path.join(root, "a/y.c"))
    changed, contents = oh._changed_files(root, before)
    assert changed == ["a/new.c", "a/x.c", "(deleted) a/y.c"]
    assert contents == {"a/new.c": "3", "a/x.c": "22"}


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
