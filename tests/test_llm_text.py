"""gemini-3.x returns AIMessage.content as a list of blocks (text + thinking/signature).
Agents must read replies through .text, never .content. Run: python tests/test_llm_text.py"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import AIMessage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_text_of_block_list_is_plain_string():
    m = AIMessage(content=[{"type": "text", "text": "a.c\n<<<<<<<< SEARCH", "extras": {"signature": "s"}},
                           {"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "\nx"}])
    assert str(m.text) == "a.c\n<<<<<<<< SEARCH\nx" and isinstance(str(m.text), str)


def test_agents_do_not_read_response_content():
    for name in ("patch_expert.py", "baselines.py", "supervisor.py", "analyzer.py"):
        src = open(os.path.join(ROOT, "agents", name)).read()
        assert not re.search(r"response\.content\b", src), name


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
