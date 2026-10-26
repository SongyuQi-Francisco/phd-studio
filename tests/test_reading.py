"""app/reading.py: push rules, status transitions, caps and stale handling, the Reading List.md view, conflict merge."""
from __future__ import annotations

import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from app import config, reading
from app.reading import ReadingError


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
