"""app/reading.py: push rules, status transitions, caps and stale handling, the Reading List.md view, conflict merge,
vault-note items and pinning (and web/reading-groups.js, the page's groups)."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from app import config, reading
from app.reading import ReadingError

PROJECT = Path(__file__).resolve().parent.parent
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def push(**kw: Any) -> tuple[dict, str]:
    kw.setdefault("reason", "worth a look")
    return reading.push(**kw)


def raw_items() -> list[dict]:
    return yaml.safe_load(config.READING_LIST.read_text(encoding="utf-8"))["items"]


def write_list(path: Path, items: list[dict]) -> None:
    path.write_text(yaml.safe_dump({"items": items}, allow_unicode=True, sort_keys=False), encoding="utf-8")


def conflict_path(name: str = "20261024-120000-ABCDEFG", stem: str = "reading", suffix: str = ".yaml") -> Path:
    return config.READING_LIST.parent / f"{stem}.sync-conflict-{name}{suffix}"


@pytest.fixture
def notes(make_note):
    for stem in ("Alpha", "Beta", "Gamma"):
        make_note(stem)


# ---------- push: validation ----------


@pytest.mark.parametrize("reason", ["", "   ", "\n"])
def test_push_requires_a_reason(notes, reason):
    with pytest.raises(ReadingError, match="reason is required"):
        push(paper="Alpha", reason=reason)
    assert not config.READING_LIST.exists()


def test_push_rejects_unknown_source_and_tier(notes):
    with pytest.raises(ReadingError, match="source"):
        push(paper="Alpha", source="a friend")
    with pytest.raises(ReadingError, match="tier"):
        push(paper="Alpha", tier="T9")


def test_push_needs_a_paper_or_an_arxiv_id():
    with pytest.raises(ReadingError, match="--paper"):
        push()


def test_push_unknown_note_points_to_arxiv():
    with pytest.raises(ReadingError, match="no paper note named 'Nope'"):
        push(paper="Nope")


def test_push_paper_name_cannot_point_outside_the_papers_folder(write_vault_file):
    write_vault_file("secret.md", "# not a paper")
    for name in ("../secret", "/etc/hosts", "Papers/../secret"):
        with pytest.raises(ReadingError, match="no paper note"):
            push(paper=name)


# ---------- push: adding ----------


def test_push_paper_lands_in_the_inbox(notes):
    item, what = push(paper="Alpha", reason="  look at their metric  ", source="claude", pushed_by="planning session",
                      lines=["Project B", "Project A", "Project A"])
    assert what == "added"
    assert item["id"].startswith("r")
    assert item["status"] == "inbox"
    assert item["paper"] == "Alpha"
    assert item["reason"] == "look at their metric"
    assert item["pushed_by"] == "planning session"
    assert item["pushed_at"] == date.today().isoformat()
    assert item["lines"] == ["Project A", "Project B"]
    assert reading.key_of(item) == "Alpha"
    assert [i["id"] for i in raw_items()] == [item["id"]]


def test_tier_queues_only_self_pushes(notes):
    mine, _ = push(paper="Alpha", tier="T1", source="self")
    agent, _ = push(paper="Beta", tier="T1", source="claude")
    assert (mine["status"], mine["tier"]) == ("queued", "T1")
    assert (agent["status"], agent["tier"]) == ("inbox", "T1")  # an agent's tier is a suggestion; the user triages


def test_push_arxiv_only_normalizes_the_id_and_keeps_the_title():
    item, what = push(arxiv=" arXiv:2606.17328v2 ", title="A Paper")
    assert what == "added"
    assert item["arxiv"] == "2606.17328"
    assert item["title"] == "A Paper"
    assert "paper" not in item
    assert reading.key_of(item) == "arxiv:2606.17328"


def test_push_arxiv_without_title_asks_arxiv(monkeypatch):
    monkeypatch.setattr(reading, "fetch_arxiv_title", lambda aid: f"Title of {aid}")
    item, _ = push(arxiv="2606.17328")
    assert item["title"] == "Title of 2606.17328"


def test_push_arxiv_of_a_known_note_uses_the_note(make_note):
    make_note("Alpha", arxiv="2606.17328")
    item, _ = push(arxiv="arxiv:2606.17328v3")
    assert item["paper"] == "Alpha" and item["arxiv"] == "2606.17328"
    assert "title" not in item  # no network lookup needed when the note exists


def test_arxiv_only_item_is_linked_when_the_note_appears_later(make_note):
    push(arxiv="2606.17328", title="A Paper")
    make_note("Alpha", source="https://arxiv.org/abs/2606.17328v1")
    [item] = reading.load()
    assert item["paper"] == "Alpha"
    assert [i["paper"] for i in raw_items()] == ["Alpha"]  # persisted


# ---------- push: dedupe, done/dismissed ----------


def test_push_twice_dedupes_and_merges_reasons_and_lines(notes):
    first, _ = push(paper="Alpha", reason="first reason", lines=["Project A"])
    again, what = push(paper="Alpha", reason="second reason", lines=["Project B"], due="2026-12-01")
    assert what == "already listed (inbox); merged"
    assert again["id"] == first["id"]
    [item] = raw_items()
    assert item["reason"] == "first reason | second reason"
    assert item["lines"] == ["Project A", "Project B"]
    assert item["due"] == "2026-12-01"


def test_same_paper_by_note_and_by_arxiv_is_one_item(make_note):
    make_note("Alpha", arxiv="2606.17328")
    push(paper="Alpha")
    push(arxiv="2606.17328")
    assert len(raw_items()) == 1


def test_arxiv_only_pushes_dedupe_by_id():
    push(arxiv="2606.00001", title="T")
    _, what = push(arxiv="arXiv:2606.00001v2", title="T")
    assert what.startswith("already listed")
    assert len(raw_items()) == 1


def test_repush_raises_the_tier_but_never_lowers_it(notes):
    push(paper="Alpha", tier="T2", source="self")
    push(paper="Alpha", tier="T1")
    assert raw_items()[0]["tier"] == "T1"
    push(paper="Alpha", tier="T3")
    assert raw_items()[0]["tier"] == "T1"


def test_done_paper_is_not_re_added_unless_forced(notes):
    item, _ = push(paper="Alpha")
    reading.update(item["id"], {"status": "done", "depth": "deep"})
    again, what = push(paper="Alpha", reason="again?")
    assert what == f"already read on {date.today().isoformat()}"
    assert again["status"] == "done"
    [stored] = raw_items()
    assert stored["status"] == "done" and stored["reason"] == "worth a look"  # untouched

    forced, what = push(paper="Alpha", reason="new angle", force=True)
    assert what.endswith("merged")
    assert forced["status"] == "inbox"
    assert raw_items()[0]["reason"] == "worth a look | new angle"
    assert len(raw_items()) == 1


def test_dismissed_paper_is_not_re_added_unless_forced(notes):
    item, _ = push(paper="Alpha")
    reading.update(item["id"], {"status": "dismissed"})
    kept, what = push(paper="Alpha", reason="please?")
    assert what.startswith("dismissed earlier; not re-added")
    assert kept["status"] == "dismissed"
    assert raw_items()[0]["status"] == "dismissed"
    assert len(raw_items()) == 1

    revived, _ = push(paper="Alpha", reason="really", force=True)
    assert revived["status"] == "inbox"


def test_force_does_not_duplicate_arxiv_only_items():
    item, _ = push(arxiv="2606.00001", title="T")
    reading.update(item["id"], {"status": "dismissed"})
    push(arxiv="2606.00001", title="T", force=True)
    assert [i["status"] for i in raw_items()] == ["inbox"]


# ---------- update: status transitions ----------


def test_update_unknown_item_and_bad_values_are_errors(notes):
    item, _ = push(paper="Alpha")
    with pytest.raises(ReadingError, match="no reading-list item"):
        reading.update("r000000", {"status": "done"})
    for patch in ({"status": "finished"}, {"tier": "T0"}, {"depth": "medium"}, {"verdict": "meh"}):
        with pytest.raises(ReadingError):
            reading.update(item["id"], patch)
    assert raw_items()[0]["status"] == "inbox"


def test_setting_a_tier_on_an_inbox_item_queues_it(notes):
    item, _ = push(paper="Alpha", source="claude")
    out = reading.update(item["id"], {"tier": "T2"})
    assert (out["status"], out["tier"]) == ("queued", "T2")


def test_done_stamps_read_at_once_and_keeps_the_notes(notes):
    item, _ = push(paper="Alpha")
    out = reading.update(item["id"], {"status": "done", "depth": "skim", "verdict": "key", "takeaway": "uses X"})
    assert out["read_at"] == date.today().isoformat()
    assert (out["depth"], out["verdict"], out["takeaway"]) == ("skim", "key", "uses X")
    with reading.editing() as items:
        items[0]["read_at"] = "2026-01-01"
    out = reading.update(item["id"], {"takeaway": "edited"})
    assert out["read_at"] == "2026-01-01"


def test_update_finds_items_by_note_name_and_arxiv_key(notes):
    push(paper="Alpha")
    push(arxiv="2606.00001", title="T")
    assert reading.update("Alpha", {"tier": "T3"})["tier"] == "T3"
    assert reading.update("arxiv:2606.00001", {"tier": "T1"})["tier"] == "T1"


def test_snooze_state_machine(notes):
    item, _ = push(paper="Alpha")
    out = reading.update(item["id"], {"status": "snoozed", "snooze_until": "2999-01-01"})
    assert out["snooze_until"] == "2999-01-01"
    out = reading.update(item["id"], {"status": "queued", "tier": "T2"})
    assert "snooze_until" not in out


def test_snoozed_items_wake_up_into_the_inbox_when_due(notes):
    a, _ = push(paper="Alpha")
    b, _ = push(paper="Beta")
    reading.update(a["id"], {"status": "snoozed", "snooze_until": days_ago(1)})
    reading.update(b["id"], {"status": "snoozed", "snooze_until": (date.today() + timedelta(days=3)).isoformat()})
    by_paper = {i["paper"]: i for i in reading.load()}
    assert by_paper["Alpha"]["status"] == "inbox" and "snooze_until" not in by_paper["Alpha"]
    assert by_paper["Beta"]["status"] == "snoozed"
    assert {i["paper"]: i["status"] for i in raw_items()} == {"Alpha": "inbox", "Beta": "snoozed"}  # persisted


def test_mark_opened_moves_inbox_and_queued_to_reading_but_not_done(notes):
    a, _ = push(paper="Alpha")
    b, _ = push(paper="Beta", tier="T1", source="self")
    c, _ = push(paper="Gamma")
    reading.update(c["id"], {"status": "done"})
    assert reading.mark_opened("Alpha")["status"] == "reading"
    assert reading.mark_opened("Beta")["status"] == "reading"
    out = reading.mark_opened("Gamma")
    assert out["status"] == "done" and out["opened_at"] == date.today().isoformat()
    assert reading.mark_opened("Nobody") is None


def test_changes_are_stamped_with_updated_at(notes):
    item, _ = push(paper="Alpha")
    with reading.editing() as items:
        items[0]["updated_at"] = "2000-01-01T00:00:00"
    assert raw_items()[0]["updated_at"] == "2000-01-01T00:00:00"
    reading.update(item["id"], {"tier": "T3"})
    assert raw_items()[0]["updated_at"] > "2000-01-01T00:00:00"
    reading.load()
    reading.load()  # reads alone leave stamps alone
    stamp = raw_items()[0]["updated_at"]
    reading.load()
    assert raw_items()[0]["updated_at"] == stamp


# ---------- summary: caps and stale ----------


def test_summary_counts_and_caps(notes, make_note):
    for i in range(7):
        make_note(f"P{i}")
        push(paper=f"P{i}", tier="T1", source="self")
    push(paper="Alpha")
    reading.mark_opened("Alpha")
    s = reading.summary(reading.load())
    assert s["T1"] == 7 and s["queued"] == 7
    assert s["caps"] == {"T1": 5, "queued": 12}
    assert s["T1"] > s["caps"]["T1"]  # the UI warns when over the cap
    assert s["reading"] == 1


def test_summary_stale_inbox_items_do_not_count_as_inbox(notes):
    push(paper="Alpha")
    push(paper="Beta")
    push(paper="Gamma")
    with reading.editing() as items:
        for i in items:
            i["pushed_at"] = {"Alpha": days_ago(1), "Beta": days_ago(reading.STALE_DAYS), "Gamma": days_ago(reading.STALE_DAYS + 1)}[i["paper"]]
    items = reading.load()
    s = reading.summary(items)
    by = {i["paper"]: i for i in items}
    assert not by["Alpha"]["stale"] and not by["Beta"]["stale"] and by["Gamma"]["stale"]
    assert by["Gamma"]["age_days"] == reading.STALE_DAYS + 1
    assert s["inbox"] == 2


def test_stale_only_applies_to_the_inbox(notes):
    item, _ = push(paper="Alpha", tier="T2", source="self")
    with reading.editing() as items:
        items[0]["pushed_at"] = days_ago(90)
    [i] = reading.load()
    reading.summary([i])
    assert i["status"] == "queued" and not i["stale"]


def test_summary_decorates_items_and_counts_done_this_week(notes):
    push(paper="Alpha")
    push(arxiv="2606.00001", title="X")
    a, b = reading.load()
    reading.update(a["id"], {"status": "done"})
    items = reading.load()
    s = reading.summary(items)
    assert s["done_week"] == 1
    assert s["lines"] == []
    by = {i["key"]: i for i in items}
    assert set(by) == {"Alpha", "arxiv:2606.00001"}
    assert by["Alpha"]["title"] == "Alpha"  # falls back to the note name
    assert by["arxiv:2606.00001"]["title"] == "X"


def test_summary_old_reads_are_not_this_weeks(notes):
    item, _ = push(paper="Alpha")
    reading.update(item["id"], {"status": "done"})
    with reading.editing() as items:
        items[0]["read_at"] = days_ago(10)
    assert reading.summary(reading.load())["done_week"] == 0


# ---------- Reading List.md ----------


def view() -> str:
    return reading.READING_VIEW.read_text(encoding="utf-8")


@pytest.fixture
def english(monkeypatch: pytest.MonkeyPatch) -> None:
    """The view is written in the vault language ([profile] vault_language); these tests read the English one."""
    monkeypatch.setitem(config.PROFILE, "vault_language", "en")


def test_view_is_a_generated_sibling_of_the_yaml(notes, english):
    push(paper="Alpha")
    assert reading.READING_VIEW == config.READING_LIST.parent / "Reading List.md"
    text = view()
    assert text.startswith("---\ntype: reading-list\ngenerated: true\n---\n")
    assert "# Reading List" in text
    assert "edits here are overwritten" in text


def test_view_groups_by_tier_with_links_lines_and_reasons(notes, english):
    push(paper="Alpha", tier="T1", source="self", lines=["Project A"], reason="their metric")
    push(paper="Beta", tier="T2", source="self", reason="baseline")
    push(arxiv="2606.00001", title="Some Paper", reason="cited by Alpha")
    push(paper="Gamma", reason="triage me")
    text = view()
    assert "## T1\n- [[Alpha]] · Project A — their metric\n" in text
    assert "## T2\n- [[Beta]] — baseline\n" in text
    assert "## T3" not in text
    assert "## Inbox (2 to triage)" in text
    assert "- [Some Paper](https://arxiv.org/abs/2606.00001) — cited by Alpha" in text
    assert text.index("## T1") < text.index("## T2") < text.index("## Inbox")


def test_view_is_in_chinese_with_tiers_kept(notes):
    push(paper="Alpha", tier="T1", source="self")
    push(paper="Gamma", reason="triage me")
    reading.mark_opened("Alpha")
    push(paper="Beta", tier="T2", source="self")
    text = view()
    assert "# 阅读清单" in text and "在这里的改动会被覆盖" in text
    assert "## 在读\n- [[Alpha]]" in text
    assert "## T2\n- [[Beta]]" in text
    assert "## 收件箱（1 篇待分拣）" in text


def test_view_keeps_the_vault_language_whatever_this_macs_interface(notes):
    config.save_settings({"ui_language": "en"})
    push(paper="Alpha")
    assert "# 阅读清单" in view()  # both Macs write the same headings, whatever their interface language


def test_view_orders_a_tier_by_due_date_then_age(notes, make_note):
    for stem in ("D1", "D2", "D3"):
        make_note(stem)
    push(paper="D1", tier="T1", source="self")
    push(paper="D2", tier="T1", source="self", due="2026-12-02")
    push(paper="D3", tier="T1", source="self", due="2026-12-01")
    text = view()
    assert text.index("[[D3]]") < text.index("[[D2]]") < text.index("[[D1]]")


def test_view_lists_reading_first_and_hides_done_dismissed_snoozed_and_stale(notes, make_note, english):
    for stem in ("R", "Done", "Dis", "Snz", "Old"):
        make_note(stem)
        push(paper=stem)
    reading.mark_opened("R")
    reading.update("Done", {"status": "done"})
    reading.update("Dis", {"status": "dismissed"})
    reading.update("Snz", {"status": "snoozed", "snooze_until": "2999-01-01"})
    with reading.editing() as items:
        next(i for i in items if i["paper"] == "Old")["pushed_at"] = days_ago(reading.STALE_DAYS + 5)
    text = view()
    assert "## Reading\n- [[R]]" in text
    for gone in ("[[Done]]", "[[Dis]]", "[[Snz]]", "[[Old]]"):
        assert gone not in text
    assert "## Inbox" not in text


def test_view_is_not_rewritten_when_nothing_changed(notes):
    push(paper="Alpha")
    before = reading.READING_VIEW.stat().st_mtime_ns
    reading.load()
    reading.load()
    assert reading.READING_VIEW.stat().st_mtime_ns == before


def test_view_is_regenerated_when_missing_or_overwritten_by_hand(notes):
    push(paper="Alpha")
    reading.READING_VIEW.unlink()
    reading.load()
    assert "[[Alpha]]" in view()
    reading.READING_VIEW.write_text("my own edits", encoding="utf-8")
    push(paper="Beta")
    assert "my own edits" not in view() and "[[Beta]]" in view()


def test_conflict_copy_of_the_view_is_parked_and_regenerated(notes):
    push(paper="Alpha")
    copy = conflict_path(stem="Reading List", suffix=".md")
    copy.write_text("old view from the other mac", encoding="utf-8")
    reading.load()
    assert not copy.exists()
    assert (reading.CONFLICTS_DIR / copy.name).exists()
    assert "[[Alpha]]" in view()


def test_legacy_block_in_the_reading_queue_note_is_removed(notes):
    push(paper="Alpha")
    config.READING_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    config.READING_QUEUE.write_text(
        f"# Queue\n\n{reading.VIEW_BEGIN}\n- [[Alpha]] generated\n{reading.VIEW_END}\n\n## Mine\n- [[Beta]]\n", encoding="utf-8")
    reading.load()
    text = config.READING_QUEUE.read_text(encoding="utf-8")
    assert reading.VIEW_BEGIN not in text and "generated" not in text
    assert "# Queue" in text and "## Mine\n- [[Beta]]" in text


# ---------- sync-conflict merge ----------


def item_dict(iid: str, paper: str, **kw: Any) -> dict:
    return {"id": iid, "paper": paper, "status": "inbox", "pushed_at": "2026-10-20", "reason": "r",
            "updated_at": "2026-10-24T10:00:00", **kw}


def test_conflict_copy_newest_edit_per_item_wins(notes):
    write_list(config.READING_LIST, [
        item_dict("r00001", "Alpha", status="reading", updated_at="2026-10-24T12:00:00"),
        item_dict("r00002", "Beta", status="inbox", updated_at="2026-10-24T09:00:00"),
    ])
    write_list(conflict_path(), [
        item_dict("r00001", "Alpha", status="done", updated_at="2026-10-24T11:00:00"),
        item_dict("r00002", "Beta", status="dismissed", updated_at="2026-10-24T10:00:00"),
    ])
    got = {i["paper"]: i["status"] for i in reading.load()}
    assert got == {"Alpha": "reading", "Beta": "dismissed"}


def test_conflict_copy_adds_items_and_unions_lines_of_the_winner(notes):
    write_list(config.READING_LIST, [item_dict("r00001", "Alpha", lines=["Project A"], updated_at="2026-10-24T09:00:00")])
    write_list(conflict_path(), [
        item_dict("r00001", "Alpha", lines=["Project B"], status="queued", tier="T1", updated_at="2026-10-24T10:00:00"),
        item_dict("r00003", "Gamma"),
    ])
    items = {i["paper"]: i for i in reading.load()}
    assert set(items) == {"Alpha", "Gamma"}
    assert items["Alpha"]["status"] == "queued"
    assert items["Alpha"]["lines"] == ["Project A", "Project B"]


def test_same_paper_pushed_on_both_macs_is_deduplicated(notes):
    write_list(config.READING_LIST, [item_dict("r0000a", "Alpha", reason="mac one", updated_at="2026-10-24T09:00:00")])
    write_list(conflict_path(), [item_dict("r0000b", "Alpha", reason="mac two", updated_at="2026-10-24T10:00:00")])
    items = reading.load()
    assert len(items) == 1
    assert items[0]["reason"] == "mac two"


def test_conflict_copy_is_parked_outside_the_vault_and_the_merge_persisted(notes):
    write_list(config.READING_LIST, [item_dict("r00001", "Alpha")])
    copy = conflict_path()
    write_list(copy, [item_dict("r00003", "Gamma")])
    reading.load()
    assert not copy.exists()
    assert (reading.CONFLICTS_DIR / copy.name).exists()
    assert {i["paper"] for i in raw_items()} == {"Alpha", "Gamma"}
    assert list(config.VAULT_DIR.rglob("*sync-conflict*")) == []


def test_unreadable_conflict_copy_is_parked_and_ignored(notes):
    write_list(config.READING_LIST, [item_dict("r00001", "Alpha")])
    copy = conflict_path()
    copy.write_text("items: [broken\n  - : :", encoding="utf-8")
    assert [i["paper"] for i in reading.load()] == ["Alpha"]
    assert not copy.exists()


def test_push_merges_a_pending_conflict_copy_first(notes):
    write_list(config.READING_LIST, [item_dict("r00001", "Alpha")])
    write_list(conflict_path(), [item_dict("r00003", "Gamma")])
    push(paper="Beta")
    assert {i["paper"] for i in raw_items()} == {"Alpha", "Beta", "Gamma"}


# ---------- first run: importing the hand-written queue ----------


def test_load_without_a_list_or_queue_starts_empty_and_creates_the_files():
    assert reading.load() == []
    assert config.READING_LIST.exists() and reading.READING_VIEW.exists()


def test_load_imports_the_reading_queue_note_once(make_note):
    for stem in ("Alpha", "Beta", "Gamma"):
        make_note(stem)
    config.READING_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    config.READING_QUEUE.write_text(
        "# Reading Queue\n\n## 2026-10-20 Project A\n- [[Alpha]]: check their metric\n- [[Beta]]\n\n"
        "## 2026-10-21 Project B\n- Full text read: [[Gamma]]\n- Loose: https://arxiv.org/abs/2606.00002 — maybe\n",
        encoding="utf-8")
    first = {reading.key_of(i): i for i in reading.load()}
    assert first["Alpha"]["status"] == "inbox" and first["Alpha"]["pushed_at"] == "2026-10-20"
    assert first["Alpha"]["lines"] == ["Project A"]
    assert first["Gamma"]["status"] == "done" and first["Gamma"]["depth"] == "deep"
    assert first["arxiv:2606.00002"]["status"] == "inbox"
    second = {reading.key_of(i): i for i in reading.load()}
    assert {k: v["id"] for k, v in second.items()} == {k: v["id"] for k, v in first.items()}


# ---------- file format and locking ----------


def test_file_has_a_header_and_unquoted_dates_roundtrip(notes):
    push(paper="Alpha", due="2026-12-01")
    text = config.READING_LIST.read_text(encoding="utf-8")
    assert text.startswith("# Reading list, managed by PhD Studio")
    assert "studio push" in text.splitlines()[1]
    # hand-edited yaml with a bare date must still load as text
    config.READING_LIST.write_text(text.replace("due: '2026-12-01'", "due: 2026-12-01"), encoding="utf-8")
    assert reading.load()[0]["due"] == "2026-12-01"


def test_no_temp_file_is_left_in_the_vault(notes):
    push(paper="Alpha")
    names = {p.name for p in config.VAULT_DIR.rglob("*") if p.is_file()}
    assert names == {"Alpha.md", "Beta.md", "Gamma.md", "Reading List.md", "reading.yaml"}
    assert not reading._TMP.exists() and reading._TMP.parent == config.DATA_DIR


def test_concurrent_pushes_do_not_lose_items():
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(5):
                push(arxiv=f"2606.{n}{i:04d}", title=f"T{n}{i}")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(1, 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    items = reading.load()
    assert len(items) == 20
    assert len({i["id"] for i in items}) == 20


# ---------- vault-note items ----------


@pytest.fixture
def report(write_vault_file) -> str:
    write_vault_file("Notes/Report A.md", "# Report A\n\nFindings.")
    return "Notes/Report A.md"


def test_push_note_lists_a_vault_note_under_its_path(report):
    item, what = push(note=report, reason="read before the meeting", source="claude", pushed_by="Ada Example")
    assert what == "added"
    assert item["note"] == "Notes/Report A.md" and item["status"] == "inbox"
    assert "paper" not in item and "arxiv" not in item and "title" not in item  # the title is the note's name, not stored
    assert reading.key_of(item) == "note:Notes/Report A.md"
    assert reading.is_note(item)
    [stored] = raw_items()
    assert stored["note"] == "Notes/Report A.md" and stored["id"] == item["id"]
    [shown] = reading.load()
    reading.summary([shown])
    assert (shown["key"], shown["title"], shown["stale"]) == ("note:Notes/Report A.md", "Report A", False)


def test_note_path_is_normalized_and_may_leave_out_md(report):
    for path in ("Notes/Report A", " ./Notes/../Notes/Report A.md ", str(config.VAULT_DIR / report)):
        item, _ = push(note=path)
        assert item["note"] == "Notes/Report A.md", path
    assert len(raw_items()) == 1  # one item, found again by its normalized path


def test_note_must_be_a_markdown_note_inside_the_vault(write_vault_file, report):
    write_vault_file("Notes/data.txt", "x")
    (config.VAULT_DIR.parent / "outside.md").write_text("# not in the vault", encoding="utf-8")
    for bad in ("Notes/Missing.md", "Notes/data.txt", "../outside.md", "/etc/hosts", "Notes/../../outside"):
        with pytest.raises(ReadingError, match="no Markdown note"):
            push(note=bad)
    assert not config.READING_LIST.exists()


def test_note_and_a_paper_cannot_be_given_together(notes, report):
    for other in ({"paper": "Alpha"}, {"arxiv": "2606.00001"}, {"ext": "doi:10.5555|abc.1"}):
        with pytest.raises(ReadingError, match="either --note or a paper"):
            push(note=report, **other)


def test_a_note_in_the_papers_folder_is_listed_as_that_paper(notes):
    item, _ = push(note=str(config.PAPERS_DIR.relative_to(config.VAULT_DIR) / "Alpha.md"))
    assert item["paper"] == "Alpha" and "note" not in item
    _, what = push(paper="Alpha")
    assert what.startswith("already listed") and len(raw_items()) == 1


def test_note_items_dedupe_by_path_and_keep_their_title(report):
    first, _ = push(note=report, reason="r1", title="Report A, final", lines=["Project A"])
    again, what = push(note="Notes/Report A", reason="r2", lines=["Project B"])
    assert what == "already listed (inbox); merged" and again["id"] == first["id"]
    [item] = raw_items()
    assert item["reason"] == "r1 | r2" and item["lines"] == ["Project A", "Project B"]
    assert item["title"] == "Report A, final"


def test_a_paper_with_the_same_title_as_a_note_item_is_its_own_item(report):
    push(note=report, title="Retrieval Augmented Ranking Survey")
    item, what = push(arxiv="2606.00001", title="Retrieval Augmented Ranking Survey")
    assert what == "added" and item["arxiv"] == "2606.00001"
    assert len(raw_items()) == 2


def test_note_items_are_found_updated_opened_and_marked_done(report):
    item, _ = push(note=report)
    assert reading.find(reading.load(), "note:Notes/Report A.md")["id"] == item["id"]
    assert reading.find(reading.load(), "Notes/Report A.md")["id"] == item["id"]
    assert reading.update("note:Notes/Report A.md", {"tier": "T1"})["status"] == "queued"
    assert reading.mark_opened("note:Notes/Report A.md")["status"] == "reading"
    out = reading.update(item["id"], {"status": "done", "depth": "deep", "verdict": "key", "takeaway": "one idea"})
    assert (out["status"], out["read_at"], out["note"]) == ("done", date.today().isoformat(), "Notes/Report A.md")
    _, what = push(note=report)
    assert what.startswith("already read on")


def test_note_items_are_never_attached_to_a_paper_note(make_note, report):
    with reading.editing() as items:
        items.append({"id": "r00009", "note": report, "arxiv": "2606.00001", "status": "inbox", "reason": "hand-edited"})
    make_note("Alpha", arxiv="2606.00001")
    item = reading.find(reading.load(), "r00009")
    assert "paper" not in item and reading.key_of(item) == "note:Notes/Report A.md"


def test_view_links_note_items_as_wikilinks(write_vault_file, report, english):
    write_vault_file("Report B.md", "# B")
    push(note=report, title="The final report", tier="T1", source="self", reason="before the meeting")
    push(note="Report B", reason="top level")
    text = view()
    assert "## T1\n- [[Notes/Report A|The final report]] — before the meeting\n" in text
    assert "## Inbox (1 to triage)\n- [[Report B]] — top level" in text
    with reading.editing() as items:
        items[0].pop("title")
    assert "- [[Notes/Report A|Report A]]" in view()


def test_the_same_note_pushed_on_both_macs_is_deduplicated(report):
    write_list(config.READING_LIST, [{"id": "r0000a", "note": report, "status": "inbox", "pushed_at": "2026-10-20",
                                      "reason": "mac one", "updated_at": "2026-10-24T09:00:00"}])
    write_list(conflict_path(), [{"id": "r0000b", "note": report, "status": "queued", "tier": "T1", "pushed_at": "2026-10-20",
                                  "reason": "mac two", "updated_at": "2026-10-24T10:00:00"}])
    [item] = reading.load()
    assert (item["note"], item["status"], item["reason"]) == (report, "queued", "mac two")


def test_a_note_item_whose_note_is_gone_still_loads(report):
    push(note=report)
    (config.VAULT_DIR / report).unlink()
    [item] = reading.load()
    assert reading.summary([item])["inbox"] == 1 and item["title"] == "Report A"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def test_api_lists_patches_and_pushes_note_items(client, notes, report):
    r = client.post("/api/reading", json={"note": report, "reason": "r", "pinned": True}).json()
    assert r["result"] == "added" and r["item"]["note"] == report and r["item"]["pinned"] is True
    push(paper="Alpha")
    got = client.get("/api/reading").json()
    item = next(i for i in got["items"] if i.get("note"))
    assert (item["key"], item["title"]) == ("note:Notes/Report A.md", "Report A")
    assert got["summary"]["inbox"] == 2
    assert client.patch(f"/api/reading/{item['id']}", json={"status": "queued", "tier": "T2"}).json()["item"]["tier"] == "T2"
    assert client.post("/api/reading/opened", json={"key": item["key"]}).json()["item"]["status"] == "reading"
    assert client.post("/api/reading", json={"note": "Notes/Missing.md"}).status_code == 400


def test_other_pages_take_note_items_in_their_stride(client, notes, report):
    push(note=report, tier="T1", source="self")
    push(paper="Alpha", tier="T1", source="self")
    assert client.get("/api/overview").json()["reading"]["T1"] == 2
    assert [p["stem"] for p in client.get("/api/papers").json()["papers"] if p["queue_rank"] is not None] == ["Alpha"]
    assert client.post("/api/thumbs", json={"keys": ["note:Notes/Report A.md"]}).json() == {"have": []}
    assert client.get("/api/thumb/note:Notes/Report A.md").status_code == 404
    assert client.get("/api/papers/note:Notes/Report A.md").status_code == 404


def test_the_assistant_can_push_and_pin_a_note(monkeypatch, report):
    from app import studio_tools

    async def emit(_e: dict[str, Any]) -> None:
        pass

    monkeypatch.setattr(studio_tools, "create_sdk_mcp_server", lambda name, tools: tools)
    tools = {t.name: t for t in studio_tools.build(emit)[0]}
    out = asyncio.run(tools["reading_push"].handler({"note": report, "reason": "r", "pinned": True}))
    assert not out.get("is_error")
    [item] = raw_items()
    assert item["note"] == report and item["pinned"] is True
    asyncio.run(tools["reading_update"].handler({"id": item["id"], "pinned": False}))
    listed = json.loads(asyncio.run(tools["reading_list"].handler({}))["content"][0]["text"])
    assert listed[0]["key"] == "note:Notes/Report A.md" and listed[0]["pinned"] is None


# ---------- pinning ----------


def test_push_can_pin_and_stamps_when(notes):
    item, _ = push(paper="Alpha", pinned=True)
    assert item["pinned"] is True and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", item["pinned_at"])
    plain, _ = push(paper="Beta")
    assert "pinned" not in plain and "pinned_at" not in plain
    assert "pinned" not in raw_items()[1]  # nothing written for an unpinned item


def test_pin_and_unpin_through_update(notes):
    item, _ = push(paper="Alpha")
    out = reading.update(item["id"], {"pinned": True})
    assert out["pinned"] is True and out["pinned_at"]
    with reading.editing() as items:
        items[0]["pinned_at"] = "2026-01-01T09:00:00"
    assert reading.update(item["id"], {"pinned": True})["pinned_at"] == "2026-01-01T09:00:00"  # already pinned: kept
    out = reading.update(item["id"], {"pinned": False})
    assert "pinned" not in out and "pinned_at" not in out
    assert "pinned" not in raw_items()[0] and "pinned_at" not in raw_items()[0]
    with pytest.raises(ReadingError, match="pinned"):
        reading.update(item["id"], {"pinned": "yes"})


def test_pinning_is_an_edit_that_syncs(notes):
    item, _ = push(paper="Alpha")
    with reading.editing() as items:
        items[0]["updated_at"] = "2000-01-01T00:00:00"
    reading.update(item["id"], {"pinned": True})
    assert raw_items()[0]["updated_at"] > "2000-01-01T00:00:00"


def test_done_and_dismissed_clear_the_pin_but_snooze_keeps_it(notes):
    a, _ = push(paper="Alpha", pinned=True)
    b, _ = push(paper="Beta", pinned=True)
    c, _ = push(paper="Gamma", pinned=True)
    assert "pinned" not in reading.update(a["id"], {"status": "done"})
    assert "pinned" not in reading.update(b["id"], {"status": "dismissed"})
    assert reading.update(c["id"], {"status": "snoozed", "snooze_until": "2999-01-01"})["pinned"] is True
    assert not reading.is_pinned(reading.find(reading.load(), c["id"]))  # snoozed: not shown as pinned until it wakes
    assert reading.is_pinned(reading.update(c["id"], {"status": "inbox"}))


def test_repush_with_pin_pins_an_existing_item(notes):
    push(paper="Alpha")
    item, what = push(paper="Alpha", reason="again", pinned=True)
    assert what.endswith("merged") and item["pinned"] is True
    item, what = push(paper="Alpha", reason="once more")
    assert item["pinned"] is True  # a plain push leaves the pin alone


def test_a_pinned_inbox_item_never_goes_stale(notes):
    push(paper="Alpha", pinned=True)
    push(paper="Beta")
    with reading.editing() as items:
        for i in items:
            i["pushed_at"] = days_ago(reading.STALE_DAYS + 10)
    items = reading.load()
    s = reading.summary(items)
    assert {i["paper"]: i["stale"] for i in items} == {"Alpha": False, "Beta": True}
    assert s["inbox"] == 1


def test_view_lists_pinned_items_first_newest_pin_on_top_and_once(notes, make_note, english):
    make_note("Delta")
    push(paper="Alpha", tier="T1", source="self")
    push(paper="Beta")
    push(paper="Gamma")
    push(paper="Delta")
    with reading.editing() as items:
        for i in items:
            pin = {"Alpha": "2026-10-20T09:00:00", "Beta": "2026-10-21T09:00:00", "Delta": "2026-10-22T09:00:00"}.get(i["paper"])
            if pin:
                i.update(pinned=True, pinned_at=pin)
        next(i for i in items if i["paper"] == "Delta")["status"] = "done"  # hand-edited: done never shows as pinned
    text = view()
    assert text.index("## Pinned\n- [[Beta]] — worth a look\n- [[Alpha]] — worth a look\n") < text.index("## Inbox")
    assert text.count("[[Alpha]]") == 1 and text.count("[[Beta]]") == 1
    assert "## T1" not in text and "## Inbox (1 to triage)\n- [[Gamma]]" in text
    assert "[[Delta]]" not in text


def test_view_heading_for_pinned_items_is_in_chinese(notes):
    push(paper="Alpha", pinned=True)
    assert "## 置顶\n- [[Alpha]]" in view()


def test_hand_written_pin_with_a_bare_timestamp_loads_as_text(notes):
    push(paper="Alpha")
    text = config.READING_LIST.read_text(encoding="utf-8")
    config.READING_LIST.write_text(text.replace("  status: inbox\n", "  status: inbox\n  pinned: true\n  pinned_at: 2026-10-29T08:30:00\n"),
                                   encoding="utf-8")
    [item] = reading.load()
    assert item["pinned"] is True and item["pinned_at"] == "2026-10-29T08:30:00"
    assert reading.is_pinned(item)


def test_a_false_pin_is_not_written_back(notes):
    push(paper="Alpha")
    with reading.editing() as items:
        items[0]["pinned"] = False
    assert "pinned" not in config.READING_LIST.read_text(encoding="utf-8").split("items:", 1)[1]


# ---------- the page's groups (web/reading-groups.js) ----------


@pytest.mark.parametrize("engine", ["node", "jsc"])
def test_reading_groups_in_a_javascript_engine(engine, tmp_path):
    exe = shutil.which("node") if engine == "node" else (str(JSC) if JSC.exists() else None)
    if not exe:
        pytest.skip(f"{engine} is not installed")
    shutil.copy(PROJECT / "web" / "reading-groups.js", tmp_path / "reading-groups.mjs")
    shutil.copy(PROJECT / "tests" / "js" / "reading_groups.test.mjs", tmp_path / "test.mjs")
    args = [exe, "-m", "test.mjs"] if engine == "jsc" else [exe, "test.mjs"]
    r = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


def test_the_page_uses_the_groups_and_opens_notes_in_the_note_sheet():
    page = (PROJECT / "web" / "reading.js").read_text(encoding="utf-8")
    assert 'import { readingGroups, ACTIVE } from "/reading-groups.js";' in page
    assert "readingGroups(filtered)" in page and "onOpenNote(i.note)" in page
    assert "!item.note && html`<button" in page  # no AI draft in the done dialog for a note item
    main = (PROJECT / "web" / "main.js").read_text(encoding="utf-8")
    assert "onOpenNote=${setNotePath}" in main
