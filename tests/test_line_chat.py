"""Research-line chats: session keys, the context a line's chat is pointed at, and the
advisor packet template and prompt."""
from __future__ import annotations

import asyncio
import threading
import unicodedata
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import claude_chat, config, direction, line_chat
from app.claude_chat import ChatRegistry, LineChat

LINE_NOTE = """---
type: research-line
created: 2026-09-01
priority: 1
status: active
tags: [idea, project-a]
---

# Research Line - Project A Ranking Study

## One-line Pitch

Check whether a second-stage reranker changes what users pick.

## First Experiment

Replay 100 sessions with and without the reranker.

## Kill Criteria

- K1: no measurable change in picks
- K2: a simple baseline gets the same change

## 2026-10-01 Status

SENTINEL-BODY-TEXT K2 partially triggered by the replay study.
"""


@pytest.fixture
def line_vault(write_vault_file):
    """A research-line note for 'Project A', a roadmap, an idea radar, evidence and review packet folders."""
    write_vault_file("Ideas/Research Line - Project A Ranking Study.md", LINE_NOTE)
    write_vault_file("Atlas/PhD Roadmap.md", "# Roadmap\n")
    write_vault_file("Ideas/Idea Radar.md", "# Radar\n")
    write_vault_file("07 Evidence/Verification Log.md", "# Log\n")
    write_vault_file("08 Review Packets/Unrelated Audit 2026-07-03.md", "---\ntype: review-packet\ncreated: 2026-07-03\n---\n")
    return config.VAULT_DIR


NOTE_A = "Research Line - Project A Ranking Study"


@pytest.fixture(autouse=True)
def fresh_cache():
    line_chat._cache.clear()


def research_lines() -> list[dict]:
    return [x for x in direction.lines() if x["kind"] == "research-line"]


def line_named(name: str) -> dict:
    return next(x for x in research_lines() if x["name"] == name)


def key_a() -> str:
    """The chat key of the Project A note."""
    return line_named(NOTE_A)["chat_key"]


def key_b() -> str:
    """The chat key of Project B, a configured line without a note."""
    return line_named("Project B")["chat_key"]


def research_note(name: str, line_tag: str, priority: int = 1, status: str = "active") -> str:
    return f"---\ntype: research-line\npriority: {priority}\nstatus: {status}\ntags: [{line_tag}]\n---\n# {name}\n"


# ---------------- keys ----------------


@pytest.mark.parametrize("name", ["Project A", "研究线 记忆 审计", "Alpha@k / Beta: Gamma?", "  a  b ", "x" * 200])
def test_line_key_is_safe_for_urls_and_file_names(name):
    key = line_chat.line_key(name)
    assert line_chat.is_line_key(key)
    assert key.startswith("line:")
    assert all(c.isascii() and (c.isalnum() or c in ":-") for c in key)
    assert len(key) <= 5 + 32 + 1 + 12
    assert "/" not in claude_chat.library.safe_name(key)


def test_line_key_ignores_case_spacing_and_unicode_form():
    assert line_chat.line_key("Project A") == line_chat.line_key("  project   a ")
    nfc = "Café 记忆"
    assert line_chat.line_key(unicodedata.normalize("NFC", nfc)) == line_chat.line_key(unicodedata.normalize("NFD", nfc))


def test_line_keys_differ_between_lines_even_with_the_same_slug():
    keys = {line_chat.line_key(n) for n in ("Project A", "Project B", "Project-A!", "记忆", "审计")}
    assert len(keys) == 5
    assert line_chat.line_key("Research Line - Project A").startswith("line:project-a-")
    assert line_chat.line_key("记忆审计").count("-") == 0  # no ASCII slug: the hash alone
    # the slug is only a label: the hash is the identity's
    assert line_chat.line_key("note:X", "Same") != line_chat.line_key("config:X", "Same")
    assert line_chat.line_key("note:X", "Same").startswith("line:same-")


@pytest.mark.parametrize("key", ["studio", "line:", "line:../x-0123456789ab", "line:Project A-0123456789ab",
                                 "line:abc-0123456789", "Paper 2024", "arxiv:2610.11920"])
def test_is_line_key_rejects_everything_else(key):
    assert not line_chat.is_line_key(key)


def test_each_research_line_gets_a_chat_key(line_vault):
    lines = research_lines()
    by_name = {x["name"]: x for x in lines}
    note = by_name[NOTE_A]
    assert note["line"] == "Project A"
    assert note["chat_key"] == line_chat.line_key(f"note:{NOTE_A}", "Project A Ranking Study")  # keyed by its note
    assert note["chat_key"].startswith("line:project-a-ranking-study-")
    virtual = by_name["Project B"]  # configured, no note of its own
    assert virtual["chat_key"] == line_chat.line_key("config:Project B", "Project B")
    assert note["chat"] == {"turns": 0, "last": None}
    assert all("chat_key" not in x for x in direction.lines() if x["kind"] != "research-line")


def test_keys_do_not_move_when_lines_are_resorted(write_vault_file):
    """Two notes on one configured line: priorities, pausing and a new higher-ranked note never swap chats."""
    write_vault_file("Ideas/Research Line - Alpha.md", research_note("Alpha", "project-a", priority=1))
    write_vault_file("Ideas/Research Line - Beta.md", research_note("Beta", "project-a", priority=2))
    before = {x["name"]: x["chat_key"] for x in research_lines()}
    assert len(set(before.values())) == len(before)
    write_vault_file("Ideas/Research Line - Alpha.md", research_note("Alpha", "project-a", priority=3, status="paused"))
    write_vault_file("Ideas/Research Line - Beta.md", research_note("Beta", "project-a", priority=1))
    write_vault_file("Ideas/Research Line - Aardvark.md", research_note("Aardvark", "project-a", priority=0))
    after = {x["name"]: x["chat_key"] for x in research_lines()}
    assert {k: after[k] for k in before} == before
    assert research_lines()[0]["name"] == "Research Line - Aardvark"  # the order did change
    assert len(set(after.values())) == len(after)


def test_configured_names_that_contain_each_other_get_their_own_cards_and_keys(write_vault_file, monkeypatch):
    monkeypatch.setitem(config.PROFILE, "lines", ["Project A", "Project AB"])
    write_vault_file("Ideas/Research Line - Project AB.md", research_note("Project AB", "project-ab"))
    lines = {x["name"]: x for x in research_lines()}
    assert lines["Research Line - Project AB"]["line"] == "Project AB"  # not "Project A"
    assert "Project AB" not in lines  # covered by its note
    assert lines["Project A"]["chat_key"] != lines["Research Line - Project AB"]["chat_key"]


def test_a_note_for_a_configured_line_takes_over_its_chat(write_vault_file):
    before = key_b()
    claude_chat._append_log(before, {"role": "user", "text": "earlier"})
    write_vault_file("Ideas/Research Line - Project B Planner.md", research_note("Planner", "project-b"))
    write_vault_file("Ideas/Research Line - Project B Other.md", research_note("Other", "project-b", priority=0))
    lines = {x["name"]: x["chat_key"] for x in research_lines()}
    assert lines["Research Line - Project B Other"] == before  # first by name, not by rank
    assert lines["Research Line - Project B Planner"] != before
    assert line_chat._load_aliases() == {"note:Research Line - Project B Other": before}
    # decided once: re-ranking later changes nothing
    write_vault_file("Ideas/Research Line - Project B Planner.md", research_note("Planner", "project-b", priority=0))
    assert {x["name"]: x["chat_key"] for x in research_lines()} == lines


def test_no_takeover_without_an_earlier_chat(write_vault_file):
    write_vault_file("Ideas/Research Line - Project B Planner.md", research_note("Planner", "project-b"))
    assert line_named("Research Line - Project B Planner")["chat_key"] == line_chat.line_key(
        "note:Research Line - Project B Planner", "Project B Planner")
    assert not line_chat.ALIASES_FILE.exists()


def test_a_taken_over_chat_goes_back_when_the_configured_line_returns(write_vault_file):
    virtual = key_b()
    claude_chat._append_log(virtual, {"role": "user", "text": "earlier"})
    note = write_vault_file("Ideas/Research Line - Planner.md", research_note("Planner", "Project B"))
    assert line_named("Research Line - Planner")["chat_key"] == virtual
    note.write_text(research_note("Planner", "something-else"), encoding="utf-8")  # no longer on Project B
    keys = {x["name"]: x["chat_key"] for x in research_lines()}
    assert keys["Project B"] == virtual
    assert keys["Research Line - Planner"] != virtual


def test_two_notes_on_one_line_keep_separate_chats(line_vault, write_vault_file):
    write_vault_file("Ideas/Research Line - Project A Second Take.md", "---\ntype: research-line\npriority: 2\n---\n")
    keys = [x["chat_key"] for x in research_lines()]
    assert len(keys) == len(set(keys))


def test_known_is_cheap(line_vault, monkeypatch):
    key = key_a()

    def boom():
        raise AssertionError("known() must not build the whole 方向 page")

    monkeypatch.setattr(direction, "lines", boom)
    assert line_chat.known(key)
    assert not line_chat.known(line_chat.line_key("Nobody's line"))
    assert not line_chat.known("studio")


def test_resolve(line_vault):
    key = key_a()
    assert line_chat.resolve(key)["name"] == NOTE_A
    with pytest.raises(FileNotFoundError):
        line_chat.resolve(line_chat.line_key("Nobody's line"))
    with pytest.raises(FileNotFoundError):
        line_chat.resolve("studio")


def test_registry_gives_each_line_its_own_session(line_vault):
    reg = ChatRegistry()
    a, b = key_a(), key_b()
    chat_a = reg.get(a)
    assert isinstance(chat_a, LineChat) and reg.get(a) is chat_a
    assert reg.get(b) is not chat_a
    assert type(reg.get("studio")) is claude_chat.StudioChat
    with pytest.raises(FileNotFoundError):
        reg.get(line_chat.line_key("Nobody's line"))


def test_line_sessions_and_logs_are_kept_per_line(line_vault):
    a, b = key_a(), key_b()
    claude_chat._save_session(a, "session-a")
    claude_chat._save_session(b, "session-b")
    assert claude_chat._load_sessions() == {a: "session-a", b: "session-b"}
    assert claude_chat.SESSIONS_FILE.is_relative_to(config.STATE_DIR)
    claude_chat._append_log(a, {"role": "user", "text": "hi"})
    claude_chat._append_log(a, {"role": "assistant", "segments": []})
    log = claude_chat._log_path(a)
    assert log.parent == config.CHATS_DIR and log.exists()
    assert claude_chat.history(b) == []
    assert line_chat.chat_stats(a)["turns"] == 1
    assert line_named(NOTE_A)["chat"]["turns"] == 1


def test_turns_are_recounted_only_when_the_log_changes(line_vault, monkeypatch):
    key = key_a()
    claude_chat._append_log(key, {"role": "user", "text": "one"})
    calls = []
    real = line_chat._count_turns
    monkeypatch.setattr(line_chat, "_count_turns", lambda p: calls.append(p) or real(p))
    assert [line_chat.chat_stats(key)["turns"] for _ in range(3)] == [1, 1, 1]
    assert len(calls) == 1
    claude_chat._append_log(key, {"role": "user", "text": "two"})
    assert line_chat.chat_stats(key)["turns"] == 2 and len(calls) == 2


def test_reset_archives_only_that_lines_log(line_vault):
    a, b = key_a(), key_b()
    for k in (a, b):
        claude_chat._save_session(k, "s-" + k)
        claude_chat._append_log(k, {"role": "user", "text": k})
    asyncio.run(ChatRegistry().get(a).reset())
    assert claude_chat.history(a) == [] and claude_chat._load_sessions() == {b: "s-" + b}
    assert len(claude_chat.history(b)) == 1
    assert len(list(config.CHATS_DIR.glob("*.archived.jsonl"))) == 1


def test_line_prompt_is_built_off_the_event_loop(line_vault, monkeypatch):
    chat = ChatRegistry().get(key_a())
    seen = {}
    real = chat._line_prompt

    def spy():
        seen["thread"] = threading.get_ident()
        return real()

    async def connected(self):
        seen["prompt"] = self._prompt()
        return "client"

    monkeypatch.setattr(chat, "_line_prompt", spy)
    monkeypatch.setattr(claude_chat.PaperChat, "_ensure_client", connected)
    assert asyncio.run(chat._ensure_client()) == "client"
    assert seen["thread"] != threading.get_ident()
    assert "Project A Ranking Study" in seen["prompt"]


def test_line_chat_keeps_the_studio_tools(line_vault):
    chat = ChatRegistry().get(key_a())
    opts = chat._options()
    assert "studio" in opts.mcp_servers
    assert any(t.startswith("mcp__studio__") for t in opts.allowed_tools)
    assert not {"Edit", "Write"} & set(opts.allowed_tools)  # vault writes still need approval
    assert opts.cwd == str(config.VAULT_DIR)
    assert "Project A Ranking Study" in opts.system_prompt["append"]


# ---------------- context ----------------


def test_vault_folder_finds_numbered_folders_and_honors_settings(write_vault_file, monkeypatch):
    write_vault_file("08 Review Packets/x.md")
    write_vault_file("Evidence Archive/x.md")
    assert line_chat.packets_dir() == config.VAULT_DIR / "08 Review Packets"
    assert line_chat.evidence_dir() == config.VAULT_DIR / "Evidence"  # not "Evidence Archive"
    monkeypatch.setattr(config, "EVIDENCE", "Evidence Archive")
    assert line_chat.evidence_dir() == config.VAULT_DIR / "Evidence Archive"


def test_context_points_at_the_lines_material(line_vault):
    ctx = line_chat.context(line_chat.resolve(key_a()))
    assert ctx["note"] == "Ideas/Research Line - Project A Ranking Study.md"
    assert ctx["roadmap"] == "Atlas/PhD Roadmap.md"
    assert ctx["idea_radar"] == "Ideas/Idea Radar.md"
    assert ctx["evidence"] == "07 Evidence" and ctx["evidence_notes"] == 1
    assert ctx["packets"] == "08 Review Packets" and ctx["packet_notes"] == 1
    assert ctx["previous_packet"] is None
    assert ctx["template"] == {"source": "default", "path": None}
    assert ctx["plan"] is None  # no plan.yaml yet


def test_context_reports_missing_material_as_none(sandbox):
    ctx = line_chat.context(line_chat.resolve(key_b()))
    assert ctx["note"] is None and ctx["roadmap"] is None and ctx["evidence"] is None and ctx["packets"] is None


def test_system_prompt_names_paths_and_does_not_paste_notes(line_vault, write_vault_file):
    write_vault_file("AGENTS.md", "# Vault rules\nRULE-SENTINEL\n")
    prompt = line_chat.system_prompt(line_chat.resolve(key_a()), today=date(2026, 10, 25))
    for path in ("Ideas/Research Line - Project A Ranking Study.md", "Atlas/PhD Roadmap.md", "Ideas/Idea Radar.md",
                 "`07 Evidence/`", "`08 Review Packets/`"):
        assert path in prompt
    assert "SENTINEL-BODY-TEXT" not in prompt  # the note is read with tools, not pasted
    assert "Check whether a second-stage" not in prompt
    assert "RULE-SENTINEL" in prompt  # vault rules always apply
    assert "2026-10-25" in prompt
    assert "K1 未触发" in prompt and "K2 部分触发" in prompt
    assert "line 字段填「Project A」" in prompt
    assert "Edit / Write" in prompt


def test_system_prompt_for_a_line_without_a_note(sandbox):
    prompt = line_chat.system_prompt(line_chat.resolve(key_b()))
    assert "没有单独的研究线笔记" in prompt and "Project B" in prompt


def test_system_prompt_lists_targets_and_open_milestones(line_vault):
    from app import plan

    plan.add_milestone("Gate M1", "2099-01-15", kind="gate", line="Project A")
    plan.add_milestone("Old one", "2099-01-01", line="Project A", status="done")
    prompt = line_chat.system_prompt(line_chat.resolve(key_a()))
    assert "2099-01-15 Gate M1（Gate）" in prompt
    assert "Old one" not in prompt


# ---------------- previous packet ----------------


def _packet(write_vault_file, name: str, fm: str = "") -> None:
    write_vault_file(f"08 Review Packets/{name}.md", f"---\ntype: review-packet\n{fm}---\n\nbody\n")


def test_previous_packet_matches_by_frontmatter_or_file_name(line_vault, write_vault_file):
    line = line_chat.resolve(key_a())
    _packet(write_vault_file, "Old Packet", "created: 2026-08-01\nresearch_line: \"[[Research Line - Project A Ranking Study]]\"\n")
    assert line_chat.previous_packet(line)["name"] == "Old Packet"
    _packet(write_vault_file, "Project A 2026-09-15")  # file name is exactly the line's name (+ date)
    assert line_chat.previous_packet(line) == {"path": "08 Review Packets/Project A 2026-09-15.md",
                                                "name": "Project A 2026-09-15", "date": "2026-09-15"}
    _packet(write_vault_file, "Project B Advisor Packet 2026-10-20")  # another line's, newer
    _packet(write_vault_file, "Project A sync 2026-10-21")  # more than the name: not trusted without frontmatter
    assert line_chat.previous_packet(line)["name"] == "Project A 2026-09-15"


def test_previous_packet_ignores_a_line_whose_name_contains_this_one(line_vault, write_vault_file, monkeypatch):
    """Line "Project A" must not pick up the packets of line "Project AB"."""
    monkeypatch.setitem(config.PROFILE, "lines", ["Project A", "Project AB"])
    line = line_chat.resolve(key_a())
    _packet(write_vault_file, "Advisor Packet - Project A Ranking Study 2026-09-01")
    _packet(write_vault_file, "Advisor Packet - Project AB 2026-10-20")
    _packet(write_vault_file, "Advisor Packet - Project A Ranking Study Extended 2026-10-21")
    _packet(write_vault_file, "Advisor Packet - Project A Ranking Study 2026-10-22 (2)",
            "line: Project AB\n")  # its frontmatter says otherwise: trust that over the file name
    assert line_chat.previous_packet(line)["date"] == "2026-09-01"
    _packet(write_vault_file, "Advisor Packet - Project A Ranking Study 2026-10-23 (2)")
    assert line_chat.previous_packet(line)["name"] == "Advisor Packet - Project A Ranking Study 2026-10-23 (2)"


@pytest.mark.parametrize("stem, core", [
    ("Advisor Packet - Project A 2026-10-12", "Project A"), ("Advisor Packet - Project A 2026-10-12 (3)", "Project A"),
    ("导师汇报包 - 项目甲 2026-10-12", "项目甲"), ("Project A 2026-10-12", "Project A"), ("Project A sync", "Project A sync"),
])
def test_stem_core(stem, core):
    assert line_chat._stem_core(stem) == core


def test_previous_packet_prefers_advisor_packets(line_vault, write_vault_file):
    line = line_chat.resolve(key_a())
    _packet(write_vault_file, "Advisor Packet - Project A Ranking Study 2026-09-01")
    _packet(write_vault_file, "Project A literature scan", "created: 2026-10-01\nline: Project A\n")
    assert line_chat.previous_packet(line)["date"] == "2026-09-01"
    _packet(write_vault_file, "Weekly", "created: 2026-10-05\npacket: advisor\ntags: [project-a]\n")
    assert line_chat.previous_packet(line)["name"] == "Weekly"


def test_previous_packet_none_without_folder(sandbox):
    assert line_chat.previous_packet(line_chat.resolve(key_b())) is None


def test_direction_lines_carry_the_last_packet(line_vault, write_vault_file):
    _packet(write_vault_file, "Advisor Packet - Project A Ranking Study 2026-10-12")
    assert line_named("Research Line - Project A Ranking Study")["packet"]["date"] == "2026-10-12"
    assert line_named("Project B")["packet"] is None


# ---------------- template ----------------


def test_default_template_has_the_five_sections_and_nothing_personal():
    tpl = line_chat.template()
    assert tpl["source"] == "default" and tpl["path"] is None
    text = tpl["text"]
    for head in ("## Progress since last packet", "## Evidence", "## Risks against Kill criteria",
                 "## Asks for the advisor", "## Next 2 weeks"):
        assert head in text
    assert "type: review-packet" in text
    # generic: every name comes from a placeholder, nothing about a particular line or person is baked in
    filled = line_chat.fill(text, {"line": "LINE-X", "date": "D", "note_link": "", "previous_link": "", "previous_date": ""})
    assert "{{" not in filled and "LINE-X" in filled
    assert "Project" not in text and "Test student" not in text


def test_template_from_the_vault_templates_folder(write_vault_file):
    write_vault_file("Templates/Idea Note Template.md", "# idea\n")
    write_vault_file("Templates/Paper Note Template.md", "# paper\n")
    assert line_chat.template()["source"] == "default"
    write_vault_file("Templates/导师汇报包模板.md", "# 汇报 {{date}}\n")
    assert line_chat.template() == {"source": "vault", "path": "Templates/导师汇报包模板.md", "text": "# 汇报 {{date}}\n"}
    write_vault_file("Templates/Review Packet Template.md", "# Review\n")
    assert line_chat.template()["path"] == "Templates/Review Packet Template.md"  # the first listed name wins


def test_template_loose_name_and_numbered_folder(write_vault_file, monkeypatch):
    monkeypatch.setattr(config, "IDEA_TEMPLATE", "99 Templates/Idea Note Template.md")
    monkeypatch.setattr(line_chat, "IDEA_TEMPLATE", "99 Templates/Idea Note Template.md")
    write_vault_file("99 Templates/Idea Note Template.md", "# idea\n")
    write_vault_file("99 Templates/Lab Meeting Packet Template.md", "# lab\n")
    assert line_chat.template()["path"] == "99 Templates/Lab Meeting Packet Template.md"


def test_template_setting_wins_and_falls_back_when_missing(write_vault_file, monkeypatch):
    write_vault_file("Templates/Review Packet Template.md", "# found\n")
    write_vault_file("Somewhere/Mine.md", "# mine\n")
    monkeypatch.setattr(config, "REVIEW_TEMPLATE", "Somewhere/Mine.md")
    assert line_chat.template()["path"] == "Somewhere/Mine.md"
    monkeypatch.setattr(config, "REVIEW_TEMPLATE", "Somewhere/Missing.md")
    assert line_chat.template()["path"] == "Templates/Review Packet Template.md"


def test_fill_replaces_known_placeholders_only():
    out = line_chat.fill("{{line}} {{ date }} {{date:YYYY-MM-DD}} {{Title}} {{unknown}}",
                         {"line": "Project A", "date": "2026-10-25", "title": "T"})
    assert out == "Project A 2026-10-25 2026-10-25 T {{unknown}}"


# ---------------- packet prompt ----------------


def test_packet_path_is_new_and_safe(line_vault, write_vault_file):
    line = {"name": "Research Line - A/B: test?", "kind": "research-line"}
    p = line_chat.packet_path(line, "2026-10-25")
    assert p.parent == config.VAULT_DIR / "08 Review Packets"
    assert p.name == "Advisor Packet - A B test 2026-10-25.md"
    write_vault_file(f"08 Review Packets/{p.name}", "taken")
    assert line_chat.packet_path(line, "2026-10-25").name == "Advisor Packet - A B test 2026-10-25 (2).md"


def test_packet_prompt_first_packet(line_vault):
    line = line_chat.resolve(key_a())
    out = line_chat.packet_prompt(line, today=date(2026, 10, 25))
    assert out["path"] == "08 Review Packets/Advisor Packet - Project A Ranking Study 2026-10-25.md"
    assert out["template"] == {"source": "default", "path": None} and out["previous"] is None
    prompt = out["prompt"]
    assert out["path"] in prompt and "Write" in prompt
    assert "当作第一份" in prompt and "2026-09-01" in prompt  # progress counted from the line's start
    assert "Kill criteria" in prompt and "2 周" in prompt and "导师" in prompt
    assert 'line: "Project A Ranking Study"' in prompt
    assert 'research_line: "[[Research Line - Project A Ranking Study]]"' in prompt
    assert "{{" not in prompt.split("<template>")[1]


def test_packet_prompt_uses_the_previous_packet_and_vault_template(line_vault, write_vault_file):
    _packet(write_vault_file, "Advisor Packet - Project A Ranking Study 2026-10-11")
    write_vault_file("Templates/Review Packet Template.md", "---\ntype: template\n---\n# {{title}}\nSince {{previous_date}} ({{previous_link}})\n")
    out = line_chat.packet_prompt(line_chat.resolve(key_a()), today=date(2026, 10, 25))
    assert out["previous"]["date"] == "2026-10-11"
    assert out["template"] == {"source": "vault", "path": "Templates/Review Packet Template.md"}
    assert "Since 2026-10-11 ([[Advisor Packet - Project A Ranking Study 2026-10-11]])" in out["prompt"]
    assert "# Advisor Packet - Project A Ranking Study 2026-10-25" in out["prompt"]
    assert "上一份汇报包是 `08 Review Packets/Advisor Packet - Project A Ranking Study 2026-10-11.md`" in out["prompt"]


# ---------------- routes ----------------


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def test_routes(line_vault, client):
    key = key_a()
    r = client.get(f"/api/lines/{key}")
    assert r.status_code == 200 and r.json()["context"]["note"].endswith("Project A Ranking Study.md")
    r = client.get(f"/api/lines/{key}/packet")
    assert r.status_code == 200 and r.json()["label"] == "起草导师汇报包"
    assert client.get(f"/api/chat/{key}").json() == {"messages": []}
    nobody = line_chat.line_key("Nobody")
    assert client.get(f"/api/lines/{nobody}").status_code == 404
    assert client.get(f"/api/chat/{nobody}").status_code == 404
    assert client.delete(f"/api/chat/{nobody}").status_code == 404
    assert client.delete(f"/api/chat/{key}").json() == {"ok": True}
    keys = [x.get("chat_key") for x in client.get("/api/direction").json()["lines"]]
    assert key in keys
