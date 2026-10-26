"""The reader's Claude prompts (PROMPTS in web/chat.js) are not UI text and are not translated, but they
must not name a language either: the reply follows the answer-language setting, which the server puts in
the system prompt (app/claude_chat.py). A "用中文讲解" in a prompt would override an English setting."""
import re
from pathlib import Path

CHAT_JS = Path(__file__).resolve().parent.parent / "web" / "chat.js"


def prompts_block() -> str:
    src = CHAT_JS.read_text(encoding="utf-8")
    start = src.index("export const PROMPTS = {")
    return src[start:src.index("// The date in the audit prompt", start)]


def test_prompts_name_no_language():
    block = prompts_block()
    assert "brief:" in block and "audit:" in block  # the slice is the prompts, not an empty string
    assert not re.search(r"用中文|中文速读|用英文|用英语|in English|in Chinese", block)
