"""app/store.py: add / update / tombstone delete / Syncthing conflict merge."""
from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

import pytest
import yaml

from app import config, store
from app.store import Store


@pytest.fixture
def st() -> Store:
    return Store(config.VAULT_DIR / "things.yaml", ("things", "other"), "# test header\n")


def write_yaml(path: Path, **collections: list[dict]) -> None:
    path.write_text(yaml.safe_dump(collections, allow_unicode=True, sort_keys=False), encoding="utf-8")


def read_yaml(st: Store) -> dict:
    return yaml.safe_load(st.path.read_text(encoding="utf-8"))


def conflict_copy(st: Store, name: str = "20261024-120000-ABCDEFG") -> Path:
    return st.path.parent / f"{st.path.stem}.sync-conflict-{name}{st.path.suffix}"


# ---------- add / update / delete ----------


def test_load_of_missing_file_is_empty_collections(st: Store):
    assert st.load() == {"things": [], "other": []}
    assert not st.path.exists()


def test_add_assigns_prefixed_id_and_timestamps_and_persists(st: Store):
    rec = st.add("things", {"name": "first", "note": None, "tags": []}, "t")
    assert rec["id"].startswith("t") and len(rec["id"]) == 7
    assert rec["name"] == "first"
    assert rec["created_at"] == rec["updated_at"]
    assert "note" not in rec and "tags" not in rec  # empty values are dropped
    text = st.path.read_text(encoding="utf-8")
    assert text.startswith("# test header\n")
    assert st.load()["things"] == [rec]


def test_add_keeps_unicode_readable_in_the_file(st: Store):
    st.add("things", {"name": "中文 idea"}, "t")
    assert "中文 idea" in st.path.read_text(encoding="utf-8")


def test_ids_are_unique(st: Store):
    ids = {st.add("things", {"n": i}, "t")["id"] for i in range(50)}
    assert len(ids) == 50


def test_update_patches_fields_and_bumps_updated_at(st: Store):
    rec = st.add("things", {"name": "a", "keep": "x"}, "t")
    old = read_yaml(st)
    old["things"][0]["updated_at"] = "2000-01-01T00:00:00"
    write_yaml(st.path, **old)
    out = st.update("things", rec["id"], {"name": "b"})
    assert out["name"] == "b" and out["keep"] == "x"
    assert out["updated_at"] > "2000-01-01T00:00:00"
    assert st.load()["things"][0]["name"] == "b"


def test_update_cannot_overwrite_identity_fields(st: Store):
    rec = st.add("things", {"name": "a"}, "t")
    out = st.update("things", rec["id"], {"id": "evil", "created_at": "1999", "updated_at": "1999", "name": "b"})
    assert out["id"] == rec["id"]
    assert out["created_at"] == rec["created_at"]
    assert out["updated_at"] != "1999"


def test_update_to_empty_value_removes_the_key(st: Store):
    rec = st.add("things", {"name": "a", "line": "X"}, "t")
    out = st.update("things", rec["id"], {"line": None})
    assert "line" not in out
    assert "line" not in st.load()["things"][0]


def test_update_unknown_id_raises_keyerror(st: Store):
    with pytest.raises(KeyError):
        st.update("things", "nope", {"name": "x"})


def test_dates_survive_a_roundtrip_as_strings(st: Store):
    st.add("things", {"when": "2027-01-14"}, "t")
    # an unquoted date in hand-edited yaml loads as datetime.date; it must come back as ISO text
    data = read_yaml(st)
    data["things"][0]["when"] = date(2027, 1, 14)
    st.path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert st.load()["things"][0]["when"] == "2027-01-14"


def test_delete_is_a_tombstone_not_a_removal(st: Store):
    a = st.add("things", {"name": "a"}, "t")
    b = st.add("things", {"name": "b"}, "t")
    st.delete("things", a["id"])
    assert [r["id"] for r in st.load()["things"]] == [b["id"]]
    raw = read_yaml(st)
    tomb = next(r for r in raw["things"] if r["id"] == a["id"])
    assert tomb["deleted"] is True and tomb["updated_at"]


def test_deleted_record_cannot_be_updated_or_deleted_again(st: Store):
    a = st.add("things", {"name": "a"}, "t")
    st.delete("things", a["id"])
    with pytest.raises(KeyError):
        st.update("things", a["id"], {"name": "zombie"})
    with pytest.raises(KeyError):
        st.delete("things", a["id"])


def test_load_can_include_tombstones(st: Store):
    a = st.add("things", {"name": "a"}, "t")
    st.add("things", {"name": "b"}, "t")
    st.delete("things", a["id"])
    assert [r["name"] for r in st.load()["things"]] == ["b"]
    everything = st.load(include_deleted=True)["things"]
    assert {r["name"]: r.get("deleted", False) for r in everything} == {"a": True, "b": False}


def test_collections_are_independent(st: Store):
    st.add("things", {"n": 1}, "t")
    st.add("other", {"n": 2}, "o")
    data = st.load()
    assert [r["n"] for r in data["things"]] == [1] and [r["n"] for r in data["other"]] == [2]


def test_rows_without_id_or_non_dicts_are_ignored(st: Store):
    st.path.write_text("things:\n  - name: no id\n  - just a string\n  - {id: ok1, name: fine}\n", encoding="utf-8")
    assert [r["id"] for r in st.load()["things"]] == ["ok1"]


def test_unparseable_file_reads_as_empty(st: Store):
    st.path.write_text("things: [unclosed\n  - : :\n", encoding="utf-8")
    assert st.load() == {"things": [], "other": []}


def test_write_goes_through_a_temp_file_outside_the_vault(st: Store):
    st.add("things", {"name": "a"}, "t")
    assert [p.name for p in config.VAULT_DIR.rglob("*") if p.is_file()] == ["things.yaml"]
    assert not st._tmp.exists()  # moved into place by os.replace
    assert st._tmp.parent == config.DATA_DIR


# ---------- sync-conflict merge ----------


def test_conflict_copy_newest_updated_at_wins_per_record(st: Store):
    write_yaml(st.path, things=[
        {"id": "t1", "name": "mine-newer", "updated_at": "2026-10-24T12:00:00"},
        {"id": "t2", "name": "mine-older", "updated_at": "2026-10-24T09:00:00"},
        {"id": "t3", "name": "same-time", "updated_at": "2026-10-24T10:00:00"},
    ])
    write_yaml(conflict_copy(st), things=[
        {"id": "t1", "name": "theirs-older", "updated_at": "2026-10-24T11:00:00"},
        {"id": "t2", "name": "theirs-newer", "updated_at": "2026-10-24T10:00:00"},
        {"id": "t3", "name": "theirs-same-time", "updated_at": "2026-10-24T10:00:00"},
    ])
    got = {r["id"]: r["name"] for r in st.load()["things"]}
    assert got == {"t1": "mine-newer", "t2": "theirs-newer", "t3": "same-time"}


def test_conflict_copy_adds_records_only_it_has(st: Store):
    write_yaml(st.path, things=[{"id": "t1", "name": "a", "updated_at": "2026-10-24T10:00:00"}])
    write_yaml(conflict_copy(st), things=[{"id": "t9", "name": "from the other mac", "updated_at": "2026-10-24T10:00:00"}],
               other=[{"id": "o1", "x": 1, "updated_at": "2026-10-24T10:00:00"}])
    data = st.load()
    assert {r["id"] for r in data["things"]} == {"t1", "t9"}
    assert [r["id"] for r in data["other"]] == ["o1"]


def test_conflict_copy_tombstone_newer_than_live_record_deletes_it(st: Store):
    write_yaml(st.path, things=[{"id": "t1", "name": "a", "updated_at": "2026-10-24T10:00:00"}])
    write_yaml(conflict_copy(st), things=[{"id": "t1", "name": "a", "deleted": True, "updated_at": "2026-10-24T11:00:00"}])
    assert st.load()["things"] == []


def test_conflict_copy_older_tombstone_does_not_resurrect_or_delete(st: Store):
    write_yaml(st.path, things=[{"id": "t1", "name": "edited after delete", "updated_at": "2026-10-24T12:00:00"}])
    write_yaml(conflict_copy(st), things=[{"id": "t1", "name": "a", "deleted": True, "updated_at": "2026-10-24T11:00:00"}])
    assert [r["name"] for r in st.load()["things"]] == ["edited after delete"]


def test_conflict_copy_is_moved_out_of_the_vault_and_the_merge_is_saved(st: Store):
    write_yaml(st.path, things=[{"id": "t1", "name": "a", "updated_at": "2026-10-24T10:00:00"}])
    copy = conflict_copy(st)
    write_yaml(copy, things=[{"id": "t2", "name": "b", "updated_at": "2026-10-24T10:00:00"}])
    st.load()
    assert not copy.exists()
    assert (store.CONFLICTS_DIR / copy.name).exists()
    # the merged result is on disk, not just returned
    on_disk = read_yaml(st)
    assert {r["id"] for r in on_disk["things"]} == {"t1", "t2"}
    # and a second load has nothing left to merge
    assert {r["id"] for r in st.load()["things"]} == {"t1", "t2"}


def test_several_conflict_copies_are_all_merged(st: Store):
    write_yaml(st.path, things=[{"id": "t1", "name": "v0", "updated_at": "2026-10-24T08:00:00"}])
    write_yaml(conflict_copy(st, "20261024-100000-AAAAAAA"), things=[{"id": "t1", "name": "v1", "updated_at": "2026-10-24T09:00:00"}])
    write_yaml(conflict_copy(st, "20261024-110000-BBBBBBB"), things=[{"id": "t1", "name": "v2", "updated_at": "2026-10-24T10:00:00"},
                                                                    {"id": "t2", "name": "extra", "updated_at": "2026-10-24T10:00:00"}])
    got = {r["id"]: r["name"] for r in st.load()["things"]}
    assert got == {"t1": "v2", "t2": "extra"}
    assert list(config.VAULT_DIR.glob("*sync-conflict*")) == []


def test_unrelated_files_are_not_mistaken_for_conflict_copies(st: Store):
    st.add("things", {"name": "a"}, "t")
    other = config.VAULT_DIR / "reading.sync-conflict-20261024-120000-ABCDEFG.yaml"
    other.write_text("items: []\n", encoding="utf-8")
    st.load()
    assert other.exists()


def test_editing_merges_conflicts_before_applying_the_change(st: Store):
    write_yaml(st.path, things=[{"id": "t1", "name": "a", "updated_at": "2026-10-24T10:00:00"}])
    copy = conflict_copy(st)
    write_yaml(copy, things=[{"id": "t2", "name": "b", "updated_at": "2026-10-24T10:00:00"}])
    st.update("things", "t2", {"name": "b2"})  # t2 exists only in the conflict copy
    assert {r["id"]: r["name"] for r in st.load()["things"]} == {"t1": "a", "t2": "b2"}
    assert not copy.exists()


# ---------- locking ----------


def test_concurrent_adds_do_not_lose_records(st: Store):
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(10):
                st.add("things", {"worker": n, "i": i}, "t")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    rows = st.load()["things"]
    assert len(rows) == 60
    assert len({r["id"] for r in rows}) == 60
