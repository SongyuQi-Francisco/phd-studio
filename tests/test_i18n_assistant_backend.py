"""The assistant's, the tools' and the generic API's text, in both languages (#52).

Each message is looked up under both interface languages (config.save_settings, as in test_i18n.py) and
has to be non-empty, different in the two languages, free of a key name, Chinese in zh and without any
Chinese in en. The tool results and prompts that go to the model are not here: they are not translated.
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from claude_agent_sdk import ToolUseBlock
from fastapi.testclient import TestClient

from app import claude_chat, config, direction, excerpt, i18n, idea_ops, library, line_chat, plan, reading, ro_tools, \
    studio_tools, tracking, venues
from app.main import app, registry

CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
KEY = re.compile(r"\b(?:tools|chat|line|api|plan)\.[a-z0-9_]+")  # a message that fell back to its key
Cases = dict[str, Callable[[], str]]


def both(fn: Callable[[], str]) -> tuple[str, str]:
    out = []
    for lang in ("zh", "en"):
        config.save_settings({"ui_language": lang})
        out.append(fn())
    return out[0], out[1]


def problems(cases: Cases) -> list[str]:
    bad = []
    for name, fn in cases.items():
        zh, en = both(fn)
        if not zh or not en or zh == en:
            bad.append(f"{name}: empty or the same in both languages: {zh!r} / {en!r}")
        elif not CJK.search(zh) or CJK.search(en):
            bad.append(f"{name}: zh must have Chinese and en none: {zh!r} / {en!r}")
        elif KEY.search(zh) or KEY.search(en):
            bad.append(f"{name}: shows a message key: {zh!r} / {en!r}")
    return bad


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def detail(r: Any) -> str:
    assert r.status_code >= 400, r.text
    return r.json()["detail"]


# ---------- generic API errors (main.py, line_api.py, idea_api.py, excerpt.py) ----------


def test_api_errors_are_in_the_interface_language(client, make_note, monkeypatch):
    make_note("P 2026")
    monkeypatch.setattr(library, "ensure_pdf", lambda stem: (_ for _ in ()).throw(FileNotFoundError("none")))
    monkeypatch.setattr("app.main.translate", lambda text: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(venues, "refresh", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    sec = {"text": "quote", "section": "Nope"}
    cases: Cases = {
        "venue": lambda: detail(client.get("/api/venues/nope")),
        "vault note": lambda: detail(client.get("/api/note/find", params={"name": "nope"})),
        "note": lambda: detail(client.get("/api/note", params={"path": "nope.md"})),
        "run": lambda: detail(client.get("/api/track/runs/nope")),
        "paper without a note": lambda: detail(client.get("/api/papers/nope")),
        "line": lambda: detail(client.get("/api/lines/nope")),
        "line chat": lambda: detail(client.get("/api/chat/line:0123456789ab")),
        "idea": lambda: detail(client.post("/api/ideas/inope/restore")),
        "not a png": lambda: detail(client.post("/api/crops", json={"png_base64": "aGVsbG8="})),
        "pdf": lambda: detail(client.get("/api/pdf/P 2026")),
        "bilingual pdf": lambda: detail(client.get("/api/pdf/P 2026", params={"variant": "mono"})),
        "translate": lambda: detail(client.post("/api/translate", json={"text": "hi"})),
        "venue refresh": lambda: detail(client.post("/api/venues/refresh")),
        "excerpt, no note": lambda: detail(client.get("/api/excerpt/arxiv:2501.00001")),
        "excerpt, no section": lambda: detail(client.post("/api/excerpt/P 2026", json=sec)),
        "excerpt, no selection": lambda: detail(client.post("/api/excerpt/P 2026", json={"text": " "})),
        "excerpt undo": lambda: detail(client.post("/api/excerpt/arxiv:2501.00001/undo", json={"line": "- x"})),
    }
    assert not problems(cases)


def test_the_websocket_says_the_previous_answer_is_still_running(client, monkeypatch):
    monkeypatch.setattr(registry.get("studio"), "lock", SimpleNamespace(locked=lambda: True))

    def busy() -> str:
        with client.websocket_connect("/ws/chat/studio") as ws:
            ws.send_json({"type": "send", "text": "hi"})
            return ws.receive_json()["message"]

    assert not problems({"busy": busy})


def test_the_default_reading_reason_is_worded_by_the_mac_that_adds_it(client, make_note):
    for lang, reason in (("zh", "自己加的"), ("en", "Added by me")):
        config.save_settings({"ui_language": lang})
        make_note(f"P {lang}")
        assert client.post("/api/reading", json={"paper": f"P {lang}"}).json()["item"]["reason"] == reason
    note = client.post("/api/reading", json={"paper": "P zh", "reason": "mine"}).json()["item"]["reason"]
    assert note.endswith("| mine")  # a reason the user gave is stored as given


# ---------- chat: errors, diff header, tool lines, labels (claude_chat.py, ro_tools.py) ----------


def test_chat_text_the_ui_shows():
    def tool_line(name: str, **inp: Any) -> Callable[[], str]:
        return lambda: claude_chat._tool_summary(ToolUseBlock(id="x", name=name, input=inp))

    def switch_while_answering() -> str:
        chat = claude_chat.PaperChat("x")
        chat.lock = SimpleNamespace(locked=lambda: True)  # type: ignore[assignment]
        with pytest.raises(RuntimeError) as e:
            asyncio.run(chat.set_model("claude", ""))
        return str(e.value)

    reads, calls = ro_tools.file_tools(), {t.name: t for t in ro_tools.studio_tools()}
    full = str(config.TEXT_DIR / "a.md")
    cases: Cases = {
        "not signed in": lambda: claude_chat._friendly_error("auth failed"),
        "usage limit": lambda: claude_chat._friendly_error("rate limit", None),
        "other error": lambda: claude_chat._friendly_error("boom"),
        "diff header": lambda: claude_chat._diff_preview("Write", {"file_path": str(config.VAULT_DIR / "n.md"), "content": "x"})[1],
        "studio tool": tool_line("mcp__studio__idea_add", text="hello"),
        "full text path": tool_line("Read", file_path=full),
        "switch while answering": switch_while_answering,
        "read": lambda: reads[0].summary({"path": full}),
        "list": lambda: reads[1].summary({"path": "."}),
        "search": lambda: reads[2].summary({"pattern": "zephyr"}),
        "studio query": lambda: calls["reading_list"].summary({}),
    }
    assert not problems(cases)
    assert claude_chat.studio_tool_label("map_read") == "map_read"  # no friendly name: shown as it is
    assert not problems({n: (lambda n=n: claude_chat.studio_tool_label(n)) for n in claude_chat.STUDIO_TOOLS_LABELLED})


def test_what_the_model_reads_stays_as_it_is():
    config.save_settings({"ui_language": "en"})
    assert ro_tools.show(config.TEXT_DIR / "a.md") == "全文：a.md"
    with pytest.raises(ro_tools.ToolError, match="隐藏文件夹不可读"):
        ro_tools.resolve(".git/config")
    assert studio_tools._err("x")["content"][0]["text"] == "错误：x"


# ---------- change cards (studio_tools.py) ----------


@pytest.fixture
def card(monkeypatch, make_note, venue_data):
    """card(tool, **args) -> the text of the change card the UI shows for that call."""
    events: list[dict[str, Any]] = []

    async def emit(e: dict[str, Any]) -> None:
        events.append(e)

    monkeypatch.setattr(studio_tools, "create_sdk_mcp_server", lambda name, tools: tools)
    built, _ = studio_tools.build(emit)
    tools = {t.name: t for t in built}
    make_note("P 2026")

    def run(name: str, **args: Any) -> str:
        events.clear()
        res = asyncio.run(tools[name].handler(args))
        assert not res.get("is_error"), res
        assert events[-1]["type"] == "studio_changed"
        return events[-1]["summary"]
    return run


def test_change_cards_follow_the_interface_language(card):
    def idea(**patch: Any) -> str:
        return card("idea_update", id=plan.add_idea("a")["id"], **patch)

    def merged() -> str:
        return card("idea_merge", ids=[plan.add_idea("a")["id"], plan.add_idea("b")["id"]])

    def reading_update() -> str:
        card("reading_push", paper="P 2026", reason="r")
        return card("reading_update", id=reading.load()[0]["id"], tier="T1")

    def unfollow() -> str:
        tracking.add_follow("keyword", "ranking", None)
        return card("follow", kind="keyword", value="ranking", remove=True)

    cases: Cases = {
        "reading push": lambda: card("reading_push", paper="P 2026", reason="r"),
        "reading update": reading_update,
        "milestone": lambda: card("milestone_add", title="Draft", date="2030-01-01"),
        "milestone update": lambda: card("milestone_update", id=plan.add_milestone("a", "2030-01-01")["id"], title="b"),
        "milestone delete": lambda: card("milestone_update", id=plan.add_milestone("a", "2030-01-01")["id"], delete=True),
        "target": lambda: card("target_add", title="Paper", venue="KDD"),
        "target update": lambda: card("target_update", id=plan.add_target("a", "KDD")["id"], title="b"),
        "target delete": lambda: card("target_update", id=plan.add_target("a", "KDD")["id"], delete=True),
        "watch": lambda: card("venue_watch", venue="CIKM", watch=True),
        "unwatch": lambda: card("venue_watch", venue="CIKM", watch=False),
        "idea": lambda: card("idea_add", text="hello"),
        "idea update": lambda: idea(status="kept"),
        "idea merge": merged,
        "follow a scholar": lambda: card("follow", kind="person", value="Ada Lovelace"),
        "follow a keyword": lambda: card("follow", kind="keyword", value="ranking"),
        "unfollow": unfollow,
    }
    assert not problems(cases)


def test_change_cards_use_the_decided_terms(card):
    config.save_settings({"ui_language": "zh"})
    assert card("idea_add", text="hello") == "Idea：hello"
    assert card("idea_update", id=plan.add_idea("a")["id"], status="kept") == "Idea：已更新（保留）"
    assert card("target_add", title="Paper", venue="KDD").startswith("投稿目标：Paper → KDD")
    assert card("milestone_add", title="Draft", date="2030-01-01") == "里程碑：Draft（2030-01-01）"
    config.save_settings({"ui_language": "en"})
    assert card("venue_watch", venue="CIKM", watch=True) == "Watched: CIKM"
    one, two = plan.add_idea("a")["id"], plan.add_idea("b")["id"]
    assert card("idea_merge", ids=[one, two]) == "Idea: merged 2 ideas"


def test_the_reading_card_words_what_the_list_answered():
    answers = {"added": "added", "already listed (queued); merged": "listed", "already read on 2026-01-01": "read",
               "dismissed earlier; not re-added (use --force to bring it back)": "dismissed"}
    for what, key in answers.items():
        for lang in ("zh", "en"):
            config.save_settings({"ui_language": lang})
            assert studio_tools._push_result(what) == i18n.t(f"tools.reading_result.{key}")
    assert studio_tools._push_result("something new") == "something new"


# ---------- plan, ideas, research lines ----------


def test_plan_idea_and_line_messages(write_vault_file):
    def raised(fn: Callable[[], Any]) -> Callable[[], str]:
        def run() -> str:
            with pytest.raises((ValueError, FileNotFoundError)) as e:
                fn()
            return str(e.value)
        return run

    def merge_a_note() -> None:
        a, b = plan.add_idea("a"), plan.add_idea("b")
        plan.idea_store.update("ideas", a["id"], {"status": "promoted", "note": "Ideas/a.md"})
        idea_ops.merge([a["id"], b["id"]])

    def latest_status() -> str:
        write_vault_file("Ideas/Research Line - Alpha.md", "---\ntype: research-line\n---\n## 2026-10-01\nRan the first ablation.\n")
        line_chat._cache.clear()
        return next(x for x in direction.lines() if x["name"] == "Research Line - Alpha")["latest_status"]

    cases: Cases = {
        "tag too long": raised(lambda: plan.norm_tags(["x" * 31])),
        "merge a promoted idea": raised(merge_a_note),
        "excerpt without text": raised(lambda: excerpt.append("x", " ", None, "Excerpts")),
        "unknown research line": raised(lambda: line_chat.resolve("line:0123456789ab")),
        "advisor packet label": lambda: line_chat.packet_prompt({"name": "Research Line - Alpha", "line": "Alpha", "path": None})["label"],
        "latest status": latest_status,
    }
    assert not problems(cases)


def test_deadline_titles_keep_a_machine_stage_and_a_title_the_pages_can_cut(venue_data):
    """Abstract / Full paper deadline in both languages (a term decision); the page cuts "<acronym> <year> " off."""
    plan.watched_keys()
    for lang in ("zh", "en"):
        config.save_settings({"ui_language": lang})
        events = [e for e in plan.timeline()["events"] if e["type"] != "milestone"]
        by_stage = {e["stage"]: e["title"] for e in events if e["venue"] == "kdd"}
        assert by_stage["abstract"].endswith(" Abstract deadline") and by_stage["deadline"].endswith(" Full paper deadline")
        assert all(re.match(r"^\S+\s\d{4}\s", e["title"]) and e["stage"] in ("abstract", "deadline") for e in events)
