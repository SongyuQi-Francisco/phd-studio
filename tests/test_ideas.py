"""app/idea_ops.py: free tags, merging ideas, provenance, restore, Syncthing conflicts; plus the
assistant tools, the `studio idea` CLI and the router that expose them."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import cli, config, idea_api, idea_ops, plan, studio_tools


def ids(rows) -> list[str]:
    return [r["id"] for r in rows]


def texts(rows) -> list[str]:
    return [r["text"] for r in rows]


def raw(rid: str) -> dict:
    """The stored record, whatever its state (tombstones too)."""
    return next(r for r in plan.idea_store.load(include_deleted=True)["ideas"] if r["id"] == rid)


def set_created(rec: dict, ts: str) -> None:
    with plan.idea_store.editing() as data:
        next(r for r in data["ideas"] if r["id"] == rec["id"])["created_at"] = ts


def conflict_copy(name: str = "20261024-120000-ABCDEFG") -> Path:
    p = plan.idea_store.path
    return p.parent / f"{p.stem}.sync-conflict-{name}{p.suffix}"


def write_copy(ideas: list[dict]) -> None:
    conflict_copy().write_text(yaml.safe_dump({"ideas": ideas}, allow_unicode=True, sort_keys=False), encoding="utf-8")


def file_ideas() -> dict[str, dict]:
    return {r["id"]: r for r in yaml.safe_load(plan.idea_store.path.read_text(encoding="utf-8"))["ideas"]}


# ---------- tag cleaning ----------


def test_norm_tags_splits_strips_and_dedupes_ignoring_case():
    assert plan.norm_tags(["#Verifier, eval ", "VERIFIER", "  two   words ", "", "x，y、z", "＃全角"]) == \
        ["Verifier", "eval", "two words", "x", "y", "z", "全角"]
    assert plan.norm_tags("solo") == ["solo"]
    assert plan.norm_tags(None) == [] and plan.norm_tags(["#", " , "]) == []


def test_norm_tags_refuses_a_tag_that_is_really_a_sentence():
    with pytest.raises(ValueError, match="标签太长"):
        plan.norm_tags(["x" * (plan.TAG_MAX + 1)])
    assert plan.norm_tags(["x" * plan.TAG_MAX]) == ["x" * plan.TAG_MAX]


def test_add_idea_cleans_its_tags():
    assert plan.add_idea("a", tags=["#Eval", "eval", "bug,perf"])["tags"] == ["Eval", "bug", "perf"]


# ---------- adding and removing tags ----------


def test_tag_adds_and_removes_in_one_step_keeping_order():
    i = plan.add_idea("a", tags=["one", "two", "three"])
    out = idea_ops.tag(i["id"], add=["#four", "TWO"], remove=["One"])
    assert out["tags"] == ["two", "three", "four"]
    assert file_ideas()[i["id"]]["tags"] == ["two", "three", "four"]


def test_removing_the_last_tag_drops_the_key():
    i = plan.add_idea("a", tags=["x"])
    out = idea_ops.tag(i["id"], remove=["x"])
    assert "tags" not in out and "tags" not in file_ideas()[i["id"]]
    assert idea_ops.tag(i["id"], add=["y"])["tags"] == ["y"]


def test_tag_that_changes_nothing_leaves_the_record_alone():
    i = plan.add_idea("a", tags=["x"])
    before = raw(i["id"])
    idea_ops.tag(i["id"], add=["X"], remove=["nope"])
    assert raw(i["id"]) == before


def test_tag_edits_apply_to_what_is_stored_not_to_a_stale_copy():
    i = plan.add_idea("a")
    idea_ops.tag(i["id"], add=["from-the-app"])
    idea_ops.tag(i["id"], add=["from-the-cli"])  # a second writer that never saw the first
    assert raw(i["id"])["tags"] == ["from-the-app", "from-the-cli"]


def test_tag_on_unknown_or_deleted_idea_is_a_key_error():
    i = plan.add_idea("a")
    plan.idea_store.delete("ideas", i["id"])
    for rid in ("nope", i["id"]):
        with pytest.raises(KeyError):
            idea_ops.tag(rid, add=["x"])


# ---------- ideas saved before tags were cleaned ----------


def legacy(text: str, tags: list[str]) -> dict:
    """An idea as the assistant used to store it: tags exactly as given."""
    i = plan.add_idea(text)
    with plan.idea_store.editing() as data:
        next(r for r in data["ideas"] if r["id"] == i["id"])["tags"] = tags
    return i


def test_listing_shows_stored_tags_cleaned_and_filters_on_them():
    i = legacy("a", ["#foo", "RL, reward", "Foo"])
    assert idea_ops.listing()[0]["tags"] == ["foo", "RL", "reward"]
    assert ids(idea_ops.listing(tags=["foo", "reward"])) == [i["id"]]
    assert idea_ops.tag_counts(idea_ops.listing()) == [("foo", 1), ("reward", 1), ("RL", 1)]


def test_removing_a_tag_works_on_a_legacy_spelling_and_cleans_the_rest():
    i = legacy("a", ["#foo", "RL, reward", "keep"])
    assert idea_ops.tag(i["id"], remove=["foo"])["tags"] == ["RL", "reward", "keep"]
    assert raw(i["id"])["tags"] == ["RL", "reward", "keep"]  # rewritten clean
    j = legacy("b", ["#foo", "RL, reward"])
    assert idea_ops.tag(j["id"], remove=["#FOO"])["tags"] == ["RL", "reward"]
    k = legacy("c", ["RL", "reward", "other"])
    assert idea_ops.tag(k["id"], remove=["RL, reward"])["tags"] == ["other"]


def test_an_over_long_legacy_tag_can_be_removed_and_never_blocks_a_merge():
    long = "x" * (plan.TAG_MAX + 15)
    a, b = legacy("a", [long, "short"]), plan.add_idea("b")
    m = idea_ops.merge([a["id"], b["id"]])
    assert m["tags"] == [long, "short"]  # kept as it was
    assert idea_ops.tag(a["id"], remove=[long])["tags"] == ["short"]
    with pytest.raises(ValueError, match="标签太长"):  # a new tag is still held to the limit
        idea_ops.tag(b["id"], add=[long])


# ---------- listing, filtering, counts ----------


def test_listing_filters_by_tags_status_and_line():
    a = plan.add_idea("a", tags=["Eval", "bug"], line="Project A")
    b = plan.add_idea("b", tags=["eval"], line="Project B")
    c = plan.add_idea("c", tags=["bug"], line="Project A")
    plan.add_idea("d")
    for k, rec in enumerate((a, b, c)):
        set_created(rec, f"2026-10-0{k + 1}T10:00:00")
    plan.idea_store.update("ideas", c["id"], {"status": "kept"})
    assert texts(idea_ops.listing(tags=["EVAL"])) == ["b", "a"]
    assert texts(idea_ops.listing(tags=["eval", "bug"])) == ["a"]  # all of them
    assert texts(idea_ops.listing(tags=["nope"])) == []
    assert texts(idea_ops.listing(tags=["bug"], status="kept")) == ["c"]
    assert texts(idea_ops.listing(tags=["bug"], line="Project A")) == ["c", "a"]
    assert texts(idea_ops.listing()) == ["d", "c", "b", "a"]


def test_tag_counts_group_spellings_and_sort_by_use():
    rows = [{"tags": ["Eval", "bug"]}, {"tags": ["eval"]}, {"tags": ["bug", "zeta"]}, {"tags": ["eval"]}, {}]
    assert idea_ops.tag_counts(rows) == [("Eval", 3), ("bug", 2), ("zeta", 1)]


# ---------- update ----------


def test_update_edits_text_line_status_note_and_tags():
    i = plan.add_idea("a", tags=["old"])
    out = idea_ops.update(i["id"], {"text": "  b  ", "line": "Project A", "status": "kept", "note": "Ideas/x.md",
                                    "tags": ["#new", "New", "other"]})
    assert (out["text"], out["line"], out["status"], out["note"], out["tags"]) == \
        ("b", "Project A", "kept", "Ideas/x.md", ["new", "other"])
    assert idea_ops.update(i["id"], {"line": None}).get("line") is None


@pytest.mark.parametrize("patch", [{"merged_from": ["i1"]}, {"merged_into": "i1"}, {"deleted": True},
                                   {"id": "other"}, {"updated_at": "2020-01-01T00:00:00"}, {"premerge_status": "new"}])
def test_update_refuses_to_touch_bookkeeping_fields(patch):
    i = plan.add_idea("a")
    with pytest.raises(ValueError, match="cannot edit"):
        idea_ops.update(i["id"], patch)
    assert raw(i["id"]) == plan.ideas()[0] and not raw(i["id"]).get("deleted")


def test_update_refuses_merged_status_empty_text_and_unknown_status():
    i = plan.add_idea("a")
    for patch, msg in (({"status": "merged"}, "merging"), ({"text": "  "}, "empty"), ({"status": "maybe"}, "must be one of")):
        with pytest.raises(ValueError, match=msg):
            idea_ops.update(i["id"], patch)
    with pytest.raises(KeyError):
        idea_ops.update("nope", {"text": "x"})


def test_update_with_restore_is_all_or_nothing(trio):
    a, b, _ = trio
    idea_ops.merge([a["id"], b["id"]])
    before = plan.idea_store.path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        idea_ops.update(a["id"], {"text": "  "}, restore=True)
    with pytest.raises(ValueError, match="must be one of"):
        idea_ops.update(a["id"], {"status": "maybe"}, restore=True, tags_add=["x"])
    assert plan.idea_store.path.read_text(encoding="utf-8") == before and raw(a["id"])["status"] == "merged"
    out = idea_ops.update(a["id"], {"line": "Project B"}, restore=True, tags_add=["x"], tags_remove=["verifier"])
    assert (out["status"], out["line"], out["tags"]) == ("kept", "Project B", ["Memory", "x"])


# ---------- merging ----------


@pytest.fixture
def trio():
    """Three ideas: an old kept one with a paper, a newer one on another line, and a dropped one."""
    a = plan.add_idea("verifier for memory writes", line="Project A", paper="Alpha", page=4, tags=["verifier", "Memory"], source="self")
    b = plan.add_idea("check writes with a judge", line="Project A", paper="Beta", page=9, tags=["memory", "eval"], source="assistant")
    c = plan.add_idea("unrelated direction", line="Project B", tags=["eval"], source="claude")
    for rec, ts in ((a, "2026-10-01T09:00:00"), (b, "2026-10-02T09:00:00"), (c, "2026-10-03T09:00:00")):
        set_created(rec, ts)
    plan.idea_store.update("ideas", a["id"], {"status": "kept"})
    plan.idea_store.update("ideas", c["id"], {"status": "dropped"})
    return a, b, c


def test_merge_creates_a_survivor_and_hides_the_originals(trio):
    a, b, c = trio
    m = idea_ops.merge([a["id"], b["id"]], text="  verify memory writes with a judge model  ", source="claude")
    assert m["id"].startswith("i") and m["id"] not in (a["id"], b["id"])
    assert (m["text"], m["source"], m["status"]) == ("verify memory writes with a judge model", "claude", "kept")
    assert m["merged_from"] == [a["id"], b["id"]]
    assert m["tags"] == ["verifier", "Memory", "eval"]  # union, first spelling wins
    assert (m["paper"], m["page"], m["line"]) == ("Alpha", 4, "Project A")
    assert m["created_at"] == "2026-10-01T09:00:00"  # the earliest, so history and the weekly chart stay put
    assert texts(idea_ops.listing()) == ["unrelated direction", m["text"]]  # a and b are gone from the default list
    for o in (a, b):
        r = raw(o["id"])
        assert (r["status"], r["merged_into"]) == ("merged", m["id"])
    assert raw(a["id"])["premerge_status"] == "kept" and raw(b["id"])["premerge_status"] == "new"


def test_merge_shows_the_paper_of_the_text_that_was_kept(trio):
    a, b, _ = trio
    assert (lambda m: (m["paper"], m["page"]))(idea_ops.merge([a["id"], b["id"]], text=b["text"])) == ("Beta", 9)
    c, d = plan.add_idea("no paper"), plan.add_idea("has paper", paper="Gamma", page=2)
    assert idea_ops.merge([c["id"], d["id"]], text=c["text"])["paper"] == "Gamma"  # kept text has none: take the first paper


def test_merge_keeps_every_original_text_paper_page_and_source(trio):
    a, b, _ = trio
    m = idea_ops.merge([a["id"], b["id"]], text="merged")
    snaps = {s["id"]: s for s in m["merged_sources"]}
    assert list(snaps) == [a["id"], b["id"]]
    assert snaps[a["id"]] == {"id": a["id"], "text": "verifier for memory writes", "line": "Project A", "paper": "Alpha", "page": 4,
                              "tags": ["verifier", "Memory"], "status": "kept", "source": "self",
                              "created_at": "2026-10-01T09:00:00"}
    assert (snaps[b["id"]]["text"], snaps[b["id"]]["paper"], snaps[b["id"]]["page"], snaps[b["id"]]["source"]) == \
        ("check writes with a judge", "Beta", 9, "assistant")
    assert "merged_sources" not in snaps[a["id"]] and "updated_at" not in snaps[a["id"]]
    stored = file_ideas()[m["id"]]
    assert stored["merged_from"] == m["merged_from"] and len(stored["merged_sources"]) == 2


def test_merge_snapshots_fields_other_features_add_later():
    a = plan.add_idea("a")
    b = plan.add_idea("b")
    idea_ops.update(a["id"], {"url": "https://example.org/x"})
    m = idea_ops.merge([a["id"], b["id"]])
    assert m["merged_sources"][0]["url"] == "https://example.org/x"


def test_merge_defaults_to_the_first_ideas_text_and_the_commonest_line(trio):
    a, b, c = trio
    m = idea_ops.merge([b["id"], c["id"], a["id"]])
    assert m["text"] == b["text"]
    assert m["line"] == "Project A"  # two of three
    assert idea_ops.merge([plan.add_idea("x", line="Project B")["id"], plan.add_idea("y")["id"]], line="Project A")["line"] == "Project A"


def test_merge_with_an_empty_line_means_no_line_but_none_means_pick_for_me(trio):
    a, b, _ = trio  # both on Project A
    assert "line" not in idea_ops.merge([a["id"], b["id"]], line="")  # 不分组 chosen on purpose
    x, y = plan.add_idea("x", line="Project B"), plan.add_idea("y", line="Project B")
    assert idea_ops.merge([x["id"], y["id"]], line=None)["line"] == "Project B"


def test_merge_status_is_kept_only_if_an_original_was_kept(trio):
    a, b, c = trio
    assert idea_ops.merge([b["id"], c["id"]])["status"] == "new"  # a dropped one does not count
    d, e = plan.add_idea("d"), plan.add_idea("e")
    plan.idea_store.update("ideas", e["id"], {"status": "kept"})
    assert idea_ops.merge([d["id"], e["id"]])["status"] == "kept"


def test_merged_ideas_are_hidden_by_default_and_listed_on_request(trio):
    a, b, c = trio
    m = idea_ops.merge([a["id"], b["id"]])
    assert set(ids(idea_ops.listing())) == {m["id"], c["id"]}
    assert set(ids(idea_ops.listing(include_merged=True))) == {m["id"], a["id"], b["id"], c["id"]}
    assert set(ids(idea_ops.listing(status="merged"))) == {a["id"], b["id"]}
    assert set(ids(plan.ideas())) == {m["id"], c["id"]} and a["id"] in ids(plan.ideas(include_merged=True))


def test_merge_is_all_or_nothing(trio):
    a, b, _ = trio
    before = plan.idea_store.path.read_text(encoding="utf-8")
    with pytest.raises(KeyError):
        idea_ops.merge([a["id"], b["id"], "nope"])
    assert plan.idea_store.path.read_text(encoding="utf-8") == before


def test_merge_needs_two_distinct_live_unmerged_unpromoted_ideas(trio):
    a, b, c = trio
    for given in ([a["id"]], [], [a["id"], a["id"]]):
        with pytest.raises(ValueError, match="at least two"):
            idea_ops.merge(given)
    plan.idea_store.delete("ideas", b["id"])
    with pytest.raises(KeyError):
        idea_ops.merge([a["id"], b["id"]])
    d = plan.add_idea("d")
    idea_ops.merge([a["id"], d["id"]])
    with pytest.raises(ValueError, match="already merged"):
        idea_ops.merge([a["id"], c["id"]])
    e, f = plan.add_idea("e"), plan.add_idea("f")
    plan.idea_store.update("ideas", e["id"], {"status": "promoted", "note": "Ideas/e.md"})
    with pytest.raises(ValueError, match="already a note"):
        idea_ops.merge([e["id"], f["id"]])
    assert raw(f["id"])["status"] == "new"


def test_merging_survivors_chains_and_keeps_each_level():
    a, b, c = (plan.add_idea(t) for t in "abc")
    m1 = idea_ops.merge([a["id"], b["id"]], text="ab")
    m2 = idea_ops.merge([m1["id"], c["id"]], text="abc")
    assert m2["merged_from"] == [m1["id"], c["id"]] and m2["merged_sources"][0]["text"] == "ab"
    assert raw(m1["id"])["status"] == "merged" and raw(a["id"])["merged_into"] == m1["id"]
    assert texts(idea_ops.listing()) == ["abc"]


# ---------- restoring ----------


def test_restore_puts_the_original_back_with_its_old_status(trio):
    a, b, c = trio
    m = idea_ops.merge([a["id"], b["id"], c["id"]], text="all three")
    out = idea_ops.restore(a["id"])
    assert out["status"] == "kept" and "merged_into" not in out and "premerge_status" not in out
    assert idea_ops.restore(c["id"])["status"] == "dropped"  # a dropped idea comes back dropped
    survivor = next(i for i in idea_ops.listing() if i["id"] == m["id"])
    assert survivor["merged_from"] == [b["id"]]
    assert [s["id"] for s in survivor["merged_sources"]] == [b["id"]]
    assert set(ids(idea_ops.listing())) == {m["id"], a["id"], c["id"]}  # b is still merged away


def test_restoring_every_original_leaves_a_plain_survivor(trio):
    a, b, c = trio
    m = idea_ops.merge([a["id"], b["id"]], text="x")
    idea_ops.restore(a["id"])
    idea_ops.restore(b["id"])
    s = next(i for i in idea_ops.listing() if i["id"] == m["id"])
    assert "merged_from" not in s and "merged_sources" not in s
    assert set(ids(idea_ops.listing())) == {m["id"], a["id"], b["id"], c["id"]}


def test_restore_only_works_on_merged_ideas(trio):
    a, _, _ = trio
    with pytest.raises(ValueError, match="not a merged idea"):
        idea_ops.restore(a["id"])
    with pytest.raises(KeyError):
        idea_ops.restore("nope")


def test_restored_idea_can_be_merged_again(trio):
    a, b, _ = trio
    idea_ops.merge([a["id"], b["id"]], text="first")
    idea_ops.restore(a["id"])
    idea_ops.restore(b["id"])
    again = idea_ops.merge([a["id"], b["id"]], text="second")
    assert raw(a["id"])["merged_into"] == again["id"]


def test_unmerge_restores_all_originals_and_deletes_the_survivor(trio):
    a, b, _ = trio
    m = idea_ops.merge([a["id"], b["id"]])
    out = idea_ops.unmerge(m["id"])
    assert set(out["restored"]) == {a["id"], b["id"]}
    assert raw(m["id"])["deleted"] is True
    assert {i["id"]: i["status"] for i in idea_ops.listing() if i["id"] in (a["id"], b["id"])} == {a["id"]: "kept", b["id"]: "new"}
    with pytest.raises(KeyError):
        idea_ops.unmerge(m["id"])  # already gone


def test_unmerge_deletes_an_untouched_survivor_and_reports_it(trio):
    a, b, _ = trio
    m = idea_ops.merge([a["id"], b["id"]])
    assert idea_ops.unmerge(m["id"])["survivor"] == "deleted" and raw(m["id"])["deleted"] is True


@pytest.mark.parametrize("edit", [{"text": "rewritten after the merge"}, {"tags_add": ["later"]}, {"line": "Project B"}])
def test_unmerge_keeps_an_edited_survivor_as_dropped(trio, edit):
    a, b, _ = trio
    m = idea_ops.merge([a["id"], b["id"]], text="merged")
    patch = {k: v for k, v in edit.items() if k != "tags_add"}
    idea_ops.update(m["id"], patch, tags_add=edit.get("tags_add", []))
    out = idea_ops.unmerge(m["id"])
    assert out["survivor"] == "dropped" and set(out["restored"]) == {a["id"], b["id"]}
    kept = raw(m["id"])
    assert kept["status"] == "dropped" and not kept.get("deleted")
    assert not {"merged_from", "merged_sources", "merge_untouched"} & set(kept)
    assert kept["text"] == edit.get("text", "merged") and ("later" in kept["tags"]) == ("tags_add" in edit)
    assert {i["id"] for i in idea_ops.listing()} >= {a["id"], b["id"], m["id"]}  # nothing is hidden or lost


def test_a_survivor_edited_on_the_other_mac_is_not_deleted_by_unmerge(trio):
    a, b, _ = trio
    m = idea_ops.merge([a["id"], b["id"]], text="merged")
    edited = {k: v for k, v in raw(m["id"]).items() if k != "merge_untouched"}  # what Mac B's app writes on an edit
    write_copy([{**edited, "text": "edited on mac b", "updated_at": "2099-01-01T00:00:00"}])
    assert idea_ops.unmerge(m["id"])["survivor"] == "dropped"
    assert raw(m["id"])["text"] == "edited on mac b"


def test_unmerge_refuses_plain_notes_and_merged_ideas():
    a, b, c = (plan.add_idea(t) for t in "abc")
    with pytest.raises(ValueError, match="no merged ideas"):
        idea_ops.unmerge(a["id"])
    m = idea_ops.merge([a["id"], b["id"]])
    plan.idea_store.update("ideas", m["id"], {"status": "promoted", "note": "Ideas/m.md"})
    with pytest.raises(ValueError, match="promoted"):
        idea_ops.unmerge(m["id"])
    assert raw(m["id"]).get("deleted") is None and raw(a["id"])["status"] == "merged"


def test_a_merged_idea_cannot_change_status_until_restored(trio):
    a, b, _ = trio
    idea_ops.merge([a["id"], b["id"]])
    with pytest.raises(ValueError, match="restore"):
        idea_ops.update(a["id"], {"status": "kept"})
    idea_ops.update(a["id"], {"text": "reworded"})  # editing the text of an original is harmless
    assert raw(a["id"])["status"] == "merged"


def test_restore_never_writes_the_survivor(trio):
    a, b, _ = trio
    m = idea_ops.merge([a["id"], b["id"]], text="merged")
    before = raw(m["id"])
    idea_ops.restore(a["id"])
    assert raw(m["id"]) == before  # the file still lists a; listing() derives who belongs on read
    assert [s["id"] for s in next(i for i in idea_ops.listing() if i["id"] == m["id"])["merged_sources"]] == [b["id"]]


def test_restore_on_one_mac_does_not_discard_an_edit_of_the_survivor_on_the_other(trio, monkeypatch):
    a, b, _ = trio
    clock = iter(["2026-10-25T10:00:00", "2026-10-25T12:00:00"])
    monkeypatch.setattr(idea_ops, "now", lambda: next(clock))
    m = idea_ops.merge([a["id"], b["id"]], text="merged")  # 10:00, both Macs have this
    idea_ops.restore(a["id"])  # Mac A, 12:00
    # Mac B edited the merged idea at 11:00 (after the merge, before A's restore) and never saw the restore.
    write_copy([{**raw(m["id"]), "text": "merged, reworded on mac b", "tags": ["late"], "updated_at": "2026-10-25T11:00:00"}])
    got = {i["id"]: i for i in idea_ops.listing()}
    assert (got[m["id"]]["text"], got[m["id"]]["tags"]) == ("merged, reworded on mac b", ["late"])
    assert got[a["id"]]["status"] == "kept"  # and A's restore stands
    assert [s["id"] for s in got[m["id"]]["merged_sources"]] == [b["id"]]


# ---------- two Macs: Syncthing conflict copies ----------


def test_conflict_copy_brings_in_tags_and_ideas_from_the_other_mac_and_keeps_a_merge(trio):
    a, b, c = trio
    plan.idea_store.update("ideas", c["id"], {"status": "new"})
    base = {r["id"]: r for r in plan.idea_store.load()["ideas"]}
    m = idea_ops.merge([a["id"], b["id"]], text="merged here")  # Mac A
    other_c = {**base[c["id"]], "tags": ["eval", "from-mac-b"], "updated_at": "2099-01-01T00:00:00"}  # Mac B tagged c ...
    new_d = {"id": "iBBBBBB", "text": "typed on mac b", "status": "new", "created_at": "2026-10-24T11:00:00",
             "updated_at": "2026-10-24T11:00:00"}
    write_copy([base[a["id"]], base[b["id"]], other_c, new_d])  # ... and never saw the merge
    got = {i["id"]: i for i in idea_ops.listing(include_merged=True)}
    assert got[c["id"]]["tags"] == ["eval", "from-mac-b"] and "iBBBBBB" in got
    assert got[a["id"]]["status"] == "merged" and got[b["id"]]["status"] == "merged"  # the older copies lose
    assert got[m["id"]]["merged_from"] == [a["id"], b["id"]]
    assert list(config.VAULT_DIR.rglob("*sync-conflict*")) == []


def test_original_edited_on_the_other_mac_after_the_merge_comes_back_as_a_plain_idea(trio):
    a, b, _ = trio
    base = {r["id"]: r for r in plan.idea_store.load()["ideas"]}
    m = idea_ops.merge([a["id"], b["id"]], text="merged here")
    write_copy([{**base[a["id"]], "text": "a, reworded on mac b", "tags": ["verifier", "late"], "updated_at": "2099-01-01T00:00:00"}])
    got = {i["id"]: i for i in idea_ops.listing()}
    assert got[a["id"]]["text"] == "a, reworded on mac b" and got[a["id"]]["status"] == "kept"  # the edit wins
    assert got[m["id"]]["merged_from"] == [b["id"]]  # the survivor stops claiming it
    assert [s["id"] for s in got[m["id"]]["merged_sources"]] == [b["id"]]
    assert b["id"] not in got  # b is still merged away


def test_both_macs_merging_overlapping_ideas_leaves_each_original_with_one_survivor():
    x, y, z = (plan.add_idea(t) for t in "xyz")
    base = {r["id"]: dict(r) for r in plan.idea_store.load()["ideas"]}
    m1 = idea_ops.merge([x["id"], y["id"]], text="x+y")  # Mac A
    # Mac B, from the same starting point: x+z -> m2, written later than A's merge.
    stamp = "2099-01-01T00:00:00"
    m2 = {"id": "iM2M2M2", "text": "x+z", "status": "new", "merged_from": [x["id"], z["id"]],
          "merged_sources": [{k: v for k, v in base[o].items() if k != "updated_at"} for o in (x["id"], z["id"])],
          "source": "self", "created_at": base[x["id"]]["created_at"], "updated_at": stamp}
    bx = {**base[x["id"]], "status": "merged", "merged_into": "iM2M2M2", "premerge_status": "new", "updated_at": stamp}
    bz = {**base[z["id"]], "status": "merged", "merged_into": "iM2M2M2", "premerge_status": "new", "updated_at": stamp}
    write_copy([bx, base[y["id"]], bz, m2])
    live = {i["id"]: i for i in idea_ops.listing()}
    assert set(live) == {m1["id"], "iM2M2M2"}
    assert live[m1["id"]]["merged_from"] == [y["id"]]  # x moved to the newer merge
    assert live["iM2M2M2"]["merged_from"] == [x["id"], z["id"]]
    assert {i["id"]: i["merged_into"] for i in idea_ops.listing(status="merged")} == \
        {x["id"]: "iM2M2M2", y["id"]: m1["id"], z["id"]: "iM2M2M2"}


def test_survivor_deleted_on_the_other_mac_leaves_originals_restorable(trio):
    a, b, _ = trio
    m = idea_ops.merge([a["id"], b["id"]])
    write_copy([{**raw(m["id"]), "deleted": True, "updated_at": "2099-01-01T00:00:00"}])
    assert m["id"] not in ids(idea_ops.listing(include_merged=True))
    assert set(ids(idea_ops.listing(status="merged"))) == {a["id"], b["id"]}  # still reachable
    assert idea_ops.restore(a["id"])["status"] == "kept"
    assert idea_ops.restore(b["id"])["status"] == "new"
    assert {a["id"], b["id"]} <= set(ids(idea_ops.listing()))


def test_same_idea_tagged_on_both_macs_newest_record_wins():
    i = plan.add_idea("a", tags=["base"])
    mine = idea_ops.tag(i["id"], add=["mac-a"])
    write_copy([{**raw(i["id"]), "tags": ["base", "mac-b"], "updated_at": "2099-01-01T00:00:00"}])
    assert idea_ops.listing()[0]["tags"] == ["base", "mac-b"]  # one record, last writer wins, as for every store
    assert mine["tags"] == ["base", "mac-a"]


# ---------- assistant tools ----------


@pytest.fixture
def call(monkeypatch):
    """call(tool, args) -> (result, emitted events): runs a studio tool handler the way the SDK would."""
    tools: dict = {}
    events: list[dict] = []

    async def emit(e):
        events.append(e)

    real = studio_tools.create_sdk_mcp_server
    monkeypatch.setattr(studio_tools, "create_sdk_mcp_server",
                        lambda name, tools=None, **kw: (tools_by_name.update({t.name: t for t in tools}), real(name, tools=tools, **kw))[1])
    tools_by_name = tools
    _, names = studio_tools.build(emit)

    def run_tool(name: str, args: dict):
        assert f"mcp__studio__{name}" in names
        events.clear()
        return asyncio.run(tools[name].handler(args)), list(events)

    return run_tool


def payload(res: dict):
    return json.loads(res["content"][0]["text"])


def test_tool_idea_add_takes_tags_and_idea_list_filters_by_them(call):
    res, ev = call("idea_add", {"text": "tagged by the assistant", "tags": ["#Eval", "bug"]})
    assert payload(res)["tags"] == ["Eval", "bug"] and ev[0]["area"] == "ideas"
    plan.add_idea("other", tags=["bug"])
    assert texts(payload(call("idea_list", {"tags": ["eval"]})[0])) == ["tagged by the assistant"]
    assert len(payload(call("idea_list", {"tags": ["bug"]})[0])) == 2
    assert len(payload(call("idea_list", {})[0])) == 2


def test_tool_idea_update_adds_and_removes_tags_and_reports_a_change(call):
    i = plan.add_idea("a", tags=["old"])
    res, ev = call("idea_update", {"id": i["id"], "tags_add": ["new"], "tags_remove": ["old"], "status": "kept"})
    assert (payload(res)["tags"], payload(res)["status"]) == (["new"], "kept") and ev[0]["area"] == "ideas"
    assert call("idea_update", {"id": i["id"]})[0]["is_error"]
    assert call("idea_update", {"id": "nope", "tags_add": ["x"]})[0]["is_error"]
    assert call("idea_update", {"id": i["id"], "status": "merged"})[0]["is_error"]


def test_tool_idea_merge_then_list_hides_and_restore_brings_back(call):
    a, b = plan.add_idea("alpha", tags=["x"]), plan.add_idea("beta")
    res, ev = call("idea_merge", {"ids": [a["id"], b["id"]], "text": "alpha and beta"})
    m = payload(res)
    assert (m["source"], m["tags"], m["merged_from"]) == ("assistant", ["x"], [a["id"], b["id"]]) and ev[0]["area"] == "ideas"
    assert texts(payload(call("idea_list", {})[0])) == ["alpha and beta"]
    assert set(ids(payload(call("idea_list", {"status": "merged"})[0]))) == {a["id"], b["id"]}
    assert len(payload(call("idea_list", {"include_merged": True})[0])) == 3
    assert payload(call("idea_update", {"id": a["id"], "restore": True})[0])["status"] == "new"
    assert call("idea_merge", {"ids": [a["id"]]})[0]["is_error"]
    assert call("idea_list", {"status": "maybe"})[0]["is_error"]


def test_tool_idea_update_restore_with_a_rejected_change_changes_nothing(call):
    a, b = plan.add_idea("alpha"), plan.add_idea("beta")
    idea_ops.merge([a["id"], b["id"]])
    res, ev = call("idea_update", {"id": a["id"], "restore": True, "text": "   "})
    assert res["is_error"] and ev == []  # not restored, and nothing announced
    assert raw(a["id"])["status"] == "merged"
    res, ev = call("idea_update", {"id": a["id"], "restore": True, "tags_add": ["back"]})
    assert payload(res)["status"] == "new" and payload(res)["tags"] == ["back"] and ev[0]["area"] == "ideas"


# ---------- CLI ----------


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_idea_add_with_tags_and_list_filters_by_tag(capsys):
    assert run(capsys, "idea", "tagged one", "--tag", "Eval", "--tag", "bug")[0] == 0
    plan.add_idea("plain one", tags=["bug"])
    code, out, _ = run(capsys, "idea", "list", "--tag", "eval")
    assert code == 0 and "#Eval #bug" in out and "tagged one" in out and "plain one" not in out
    assert len(run(capsys, "idea", "list", "--tag", "bug")[1].splitlines()) == 2
    assert run(capsys, "idea", "list", "--tag", "eval", "--tag", "nope")[1] == "(empty)\n"
    rows = json.loads(run(capsys, "idea", "list", "--tag", "bug", "--json")[1])
    assert sorted(texts(rows)) == ["plain one", "tagged one"]


def test_cli_idea_tag_plus_adds_minus_removes(capsys):
    i = plan.add_idea("a", tags=["todo"])
    code, out, _ = run(capsys, "idea", "tag", i["id"], "+verifier", "-todo", "+Memory")
    assert code == 0 and out.strip() == f"{i['id']}  #verifier #Memory"
    assert raw(i["id"])["tags"] == ["verifier", "Memory"]
    assert run(capsys, "idea", "tag", i["id"], "bare")[1].strip().endswith("#verifier #Memory #bare")
    assert run(capsys, "idea", "tag", i["id"], "-verifier", "-Memory", "-bare")[1].strip() == f"{i['id']}  (no tags)"


def test_cli_idea_tag_errors(capsys):
    i = plan.add_idea("a")
    code, _, err = run(capsys, "idea", "tag", "inope", "+x")
    assert code == 2 and "no idea inope" in err
    code, _, err = run(capsys, "idea", "tag", i["id"])
    assert code == 2 and "nothing to do" in err
    assert run(capsys, "idea", "tag", i["id"], "+" + "x" * 40)[0] == 2


def test_cli_idea_merge_list_all_and_restore(capsys):
    a, b = plan.add_idea("alpha", tags=["x"]), plan.add_idea("beta", tags=["y"])
    code, out, _ = run(capsys, "idea", "merge", a["id"], b["id"], "--text", "alpha with beta")
    assert code == 0 and out.startswith("merged: i") and a["id"] in out and b["id"] in out
    code, out, _ = run(capsys, "idea", "list")
    assert len(out.splitlines()) == 1 and "#x #y" in out and "alpha with beta" in out
    assert len(run(capsys, "idea", "list", "--all")[1].splitlines()) == 3
    assert len(run(capsys, "idea", "list", "--status", "merged")[1].splitlines()) == 2
    code, out, _ = run(capsys, "idea", "restore", a["id"])
    assert code == 0 and "restored:" in out and "[new]" in out
    assert len(run(capsys, "idea", "list")[1].splitlines()) == 2
    assert run(capsys, "idea", "restore", a["id"])[0] == 2  # not merged any more


def test_cli_idea_merge_errors(capsys):
    a = plan.add_idea("alpha")
    assert run(capsys, "idea", "merge", a["id"])[0] == 2
    code, _, err = run(capsys, "idea", "merge", a["id"], "inope")
    assert code == 2 and "no idea inope" in err


# ---------- HTTP routes ----------


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(idea_api.router)
    return TestClient(app)


def test_routes_tag_merge_restore_unmerge(client):
    a, b = plan.add_idea("alpha", tags=["x"]), plan.add_idea("beta")
    r = client.post(f"/api/ideas/{a['id']}/tags", json={"add": ["#y"], "remove": ["x"]})
    assert r.status_code == 200 and r.json()["tags"] == ["y"]
    r = client.post("/api/ideas/merge", json={"ids": [a["id"], b["id"]], "text": "both"})
    m = r.json()
    assert r.status_code == 200 and m["merged_from"] == [a["id"], b["id"]] and m["tags"] == ["y"]
    r = client.post(f"/api/ideas/{a['id']}/restore")
    assert r.status_code == 200 and r.json()["status"] == "new"
    r = client.post(f"/api/ideas/{m['id']}/unmerge")
    assert r.status_code == 200 and r.json()["restored"] == [b["id"]]
    assert set(texts(idea_ops.listing())) == {"alpha", "beta"}


def test_route_merge_line_empty_string_means_no_line_and_unmerge_reports_the_survivor(client):
    a, b = plan.add_idea("alpha", line="Project A"), plan.add_idea("beta", line="Project A")
    m = client.post("/api/ideas/merge", json={"ids": [a["id"], b["id"]], "text": "both", "line": ""}).json()
    assert "line" not in m and m["merge_untouched"] is True
    client.post(f"/api/ideas/{m['id']}/tags", json={"add": ["touched"]})
    r = client.post(f"/api/ideas/{m['id']}/unmerge")
    assert r.status_code == 200 and r.json()["survivor"] == "dropped"
    m2 = client.post("/api/ideas/merge", json={"ids": [a["id"], b["id"]]}).json()
    assert m2["line"] == "Project A"  # not given: the most common one
    assert client.post(f"/api/ideas/{m2['id']}/unmerge").json()["survivor"] == "deleted"


def test_routes_report_bad_requests_as_4xx(client):
    a = plan.add_idea("alpha")
    assert client.post("/api/ideas/merge", json={"ids": [a["id"]]}).status_code == 400
    assert client.post("/api/ideas/merge", json={"ids": [a["id"], "nope"]}).status_code == 404
    assert client.post("/api/ideas/nope/tags", json={"add": ["x"]}).status_code == 404
    assert client.post(f"/api/ideas/{a['id']}/tags", json={"add": ["x" * 99]}).status_code == 400
    assert client.post(f"/api/ideas/{a['id']}/restore").status_code == 400
    assert client.post("/api/ideas/nope/unmerge").status_code == 404
