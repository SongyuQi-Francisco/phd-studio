"""Reading a paper's text can download its PDF (up to 60 s per link): the chat code must not do that on the event loop."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from app import claude_chat, library


def test_building_the_chat_options_runs_off_the_event_loop(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, options):
            self.options = options

        async def connect(self):
            pass

    def options(self):  # where the system prompt reads the paper text (and so may download its PDF)
        seen["thread"] = threading.current_thread()
        return SimpleNamespace(resume=None)

    monkeypatch.setattr(claude_chat.PaperChat, "_options", options)
    monkeypatch.setattr(claude_chat, "ClaudeSDKClient", Client)

    async def run():
        loop_thread = threading.current_thread()
        await claude_chat.PaperChat("openreview:abc12345")._ensure_client()
        return loop_thread

    assert seen.get("thread") is None
    loop_thread = asyncio.run(run())
    assert seen["thread"] is not loop_thread


def test_the_takeaway_draft_reads_the_paper_text_off_the_event_loop(monkeypatch):
    seen = {}

    def text_file(stem):
        seen["thread"] = threading.current_thread()
        return "/tmp/text.md"

    async def query(prompt, options):
        seen["prompt"] = prompt
        return
        yield  # an empty stream

    monkeypatch.setattr(library, "text_file", text_file)
    monkeypatch.setattr(claude_chat, "query", query)
    monkeypatch.setattr(claude_chat, "_load_sessions", lambda: {})

    async def run():
        loop_thread = threading.current_thread()
        await claude_chat.draft_takeaway("openreview:abc12345")
        return loop_thread

    loop_thread = asyncio.run(run())
    assert seen["thread"] is not loop_thread and "/tmp/text.md" in seen["prompt"]
