"""app/zotero_push.py: pushing papers into Zotero, dedupe, the note link-back and auto mode.

Zotero is never the real one: a fake zotero.sqlite in the sandbox stands in for the local database,
and a small HTTP server on a random port plays both the desktop connector and the Web API. Every
other httpx call (including the real connector port 23119) raises.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

if "phd-studio-test-" not in os.environ.get("STUDIO_CONFIG", ""):  # never against the real vault or Zotero
    pytest.skip("needs the sandbox from tests/conftest.py", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from app import config, library, reading, zotero_push  # noqa: E402

from .conftest import ROOT  # noqa: E402

_REAL_GET, _REAL_POST = httpx.get, httpx.post  # before the sandbox fixture swaps them for "no network"

SCHEMA = """
CREATE TABLE libraries (libraryID INTEGER PRIMARY KEY, type TEXT NOT NULL);
CREATE TABLE items (itemID INTEGER PRIMARY KEY, libraryID INT NOT NULL, key TEXT NOT NULL);
CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value);
CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY);
CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INT, contentType TEXT);
CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INT);
CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT, parentCollectionID INT, libraryID INT, key TEXT);
CREATE TABLE deletedCollections (collectionID INTEGER PRIMARY KEY);
INSERT INTO libraries VALUES (1, 'user'), (7, 'group');
INSERT INTO fields (fieldName) VALUES ('title'), ('DOI'), ('url'), ('archiveID'), ('extra');
"""


class FakeZotero:
    """A minimal zotero.sqlite with just the tables the app reads."""

    def __init__(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.db = folder / "zotero.sqlite"
        self.lock = threading.Lock()
        with sqlite3.connect(self.db) as con:
            con.executescript(SCHEMA)
        self.stamp = time.time() - 1000
        self._touch()

    def _touch(self) -> None:
        self.stamp += 1  # strictly newer each write, whatever the file system's mtime resolution
        os.utime(self.db, (self.stamp, self.stamp))

    def add(self, key: str, library_id: int = 1, **fields: str | None) -> int:
        with self.lock, sqlite3.connect(self.db) as con:
            iid = con.execute("INSERT INTO items (libraryID, key) VALUES (?, ?)", (library_id, key)).lastrowid
            for name, value in fields.items():
                if value:
                    fid = con.execute("SELECT fieldID FROM fields WHERE fieldName = ?", (name,)).fetchone()[0]
                    vid = con.execute("INSERT INTO itemDataValues (value) VALUES (?)", (value,)).lastrowid
                    con.execute("INSERT INTO itemData VALUES (?, ?, ?)", (iid, fid, vid))
        self._touch()
        return iid

    def trash(self, key: str) -> None:
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO deletedItems SELECT itemID FROM items WHERE key = ?", (key,))
        self._touch()

    def collection(self, cid: int, name: str, key: str, parent: int | None = None, library_id: int = 1) -> None:
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO collections VALUES (?, ?, ?, ?, ?)", (cid, name, parent, library_id, key))
        self._touch()


class MockZotero:
    """The desktop connector and the Web API, recorded."""

    def __init__(self, fake: FakeZotero) -> None:
        self.fake = fake
        self.calls: list[dict[str, Any]] = []
        self.prefs = {"downloadAssociatedFiles": True}
        self.remote: list[dict[str, Any]] = []  # what a Web API search returns
        self.save_status = 201
        self.write_to_db = True  # the desktop app writes the saved item into zotero.sqlite
        self.n = 0
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, code: int, body: Any = None) -> None:
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self, method: str, body: Any) -> str:
                u = urlparse(self.path)
                mock.calls.append({"method": method, "path": u.path, "query": parse_qs(u.query),
                                   "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                return u.path

            def do_GET(self) -> None:
                path = self._record("GET", None)
                if path == "/keys/current":
                    return self._send(200, {"userID": 4242})
                if path == "/users/4242/items":
                    return self._send(200, mock.remote)
                self._send(404)

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                is_json = self.headers.get("Content-Type", "").startswith("application/json")
                path = self._record("POST", json.loads(raw) if is_json and raw else raw)
                body = mock.calls[-1]["body"]
                if path == "/connector/ping":
                    return self._send(200, {"prefs": mock.prefs})
                if path == "/connector/saveItems":
                    if mock.save_status != 201:
                        return self._send(mock.save_status)
                    it = body["items"][0]
                    if mock.write_to_db:
                        mock.fake.add(mock.next_key(), url=it.get("url"), archiveID=it.get("archiveID"), DOI=it.get("DOI"),
                                      extra=it.get("extra"))
                    return self._send(201)
                if path == "/connector/updateSession":
                    return self._send(200, {})
                if path == "/connector/saveAttachment":
                    return self._send(201)
                if path == "/users/4242/items":
                    key = "ATTACH22" if body[0].get("itemType") == "attachment" else mock.next_key()
                    return self._send(200, {"successful": {"0": {"key": key}}, "success": {"0": key}, "failed": {}})
                self._send(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def next_key(self) -> str:
        self.n += 1
        return f"NEWKEY{self.n:02d}"

    def paths(self) -> list[str]:
        return [c["path"] for c in self.calls if c["path"] != "/connector/ping"]

    def last(self, path: str) -> dict[str, Any]:
        return [c for c in self.calls if c["path"] == path][-1]


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def zot(sandbox, monkeypatch):
    """Fake Zotero database + mock server; the desktop app 'runs' and there is no Web API key."""
    for f in config.ZOTERO_DIR.glob("zotero.sqlite*"):
        f.unlink()
    fake = FakeZotero(config.ZOTERO_DIR)
    mock = MockZotero(fake)
    down = f"http://127.0.0.1:{_closed_port()}"

    def guard(real):
        def call(url, *args, **kwargs):
            if not str(url).startswith((mock.base, down)):
                raise RuntimeError(f"network access attempted in a test: {url}")
            return real(url, *args, **kwargs)
        return call

    monkeypatch.setattr("httpx.get", guard(_REAL_GET))
    monkeypatch.setattr("httpx.post", guard(_REAL_POST))
    monkeypatch.setattr(zotero_push, "CONNECTOR", mock.base)
    monkeypatch.setattr(zotero_push, "WEB_API", mock.base)
    monkeypatch.setattr(zotero_push, "_await_key", lambda ids, wait=0.0: _await(ids))
    for k in ("ZOTERO_API_KEY", "ZOTERO_USER_ID"):
        monkeypatch.delenv(k, raising=False)
    secrets = config.SECRETS_DIR / "zotero.env"
    secrets.unlink(missing_ok=True)
    zotero_push._index.cache_clear()
    zotero_push._ping_cache.update(at=0.0, up=False, prefs={})
    zotero_push._web_user.clear()
    mock.down = down

    def desktop(up: bool) -> None:
        monkeypatch.setattr(zotero_push, "CONNECTOR", mock.base if up else down)
        zotero_push._ping_cache["at"] = 0.0

    def web_key(user: str | None = "4242") -> None:
        secrets.parent.mkdir(parents=True, exist_ok=True)
        secrets.write_text("ZOTERO_API_KEY=testkey123\n" + (f"ZOTERO_USER_ID={user}\n" if user else ""), encoding="utf-8")

    mock.desktop, mock.web_key = desktop, web_key
    yield mock
    mock.server.shutdown()
    mock.server.server_close()
    secrets.unlink(missing_ok=True)
    for f in config.ZOTERO_DIR.glob("zotero.sqlite*"):
        f.unlink()
    library._zotero_pdf_urls.cache_clear()
    zotero_push._index.cache_clear()


_ORIG_AWAIT = zotero_push._await_key


def _await(ids):
    return _ORIG_AWAIT(ids, wait=0.0)


NOTE = """---
type: paper
year: 2026
arxiv: "2608.21939"
tags:
  - paper
  - memory
---

# Group Memory 2026

## Metadata

- Title: Enhancing Group Recommendation with Memory
- Authors: Ada Lovelace, Alan Turing, et al.
- Year: 2026
- Venue / status: arXiv preprint
"""


@pytest.fixture
def note() -> Path:
    p = config.PAPERS_DIR / "Group Memory 2026.md"
    p.write_text(NOTE, encoding="utf-8")
    library._arxiv_index.cache_clear()
    return p


# ---------- identities ----------


def test_ident_keys_normalise_doi_arxiv_and_url():
    assert zotero_push.ident_keys(doi="https://doi.org/10.1145/3637528.3671870.") == {"doi:10.1145/3637528.3671870"}
    assert zotero_push.ident_keys(arxiv="2608.21939") == {"arxiv:2608.21939"}
    assert zotero_push.ident_keys(url="http://www.arXiv.org/abs/2608.21939v2/") == {
        "arxiv:2608.21939", "url:arxiv.org/abs/2608.21939v2"}
    # What Zotero's arXiv translator writes: the arXiv DOI, an "arXiv:" archive id, and the id in Extra.
    assert "arxiv:2608.21939" in zotero_push.ident_keys(doi="10.48550/arXiv.2608.21939")
    assert zotero_push.ident_keys(extra="See 10.1000/other\narXiv:2608.21939\nDOI: 10.1145/AB.12") == {
        "arxiv:2608.21939", "doi:10.1145/ab.12"}
    assert zotero_push.ident_keys(extra="Notes about 10.1145/x") == set()  # a DOI in Extra counts only on a DOI: line
    assert zotero_push.ident_keys(url="not a url") == set()


def test_paper_meta_and_item_from_a_note(zot, note):
    meta = zotero_push.paper_meta("Group Memory 2026")
    assert meta["title"] == "Enhancing Group Recommendation with Memory"
    assert meta["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert meta["ids"] == {"arxiv:2608.21939", "url:arxiv.org/abs/2608.21939"}
    assert meta["pdf_url"] == "https://arxiv.org/pdf/2608.21939"
    item = zotero_push.build_item(meta)
    assert item["itemType"] == "preprint" and item["archiveID"] == "arXiv:2608.21939" and item["repository"] == "arXiv"
    assert item["creators"][0] == {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}
    assert item["extra"] == "arXiv: 2608.21939\nObsidian note: Group Memory 2026"
    assert item["tags"] == [{"tag": "phd-studio"}] and item["date"] == "2026"
    assert "DOI" not in item and "abstractNote" not in item


def test_author_names_lose_affiliations_and_footnote_marks():
    names = ("Subrat Panda (MindTwin)", "Alan Turing*", "Wei Li†", "Plato")
    assert [zotero_push._creator(n)["lastName"] for n in names] == ["Panda", "Turing", "Li", "Plato"]
    assert zotero_push._creator("Subrat Panda (MindTwin)")["firstName"] == "Subrat"


def test_item_types_for_doi_only_and_url_only_notes(zot, make_note):
    make_note("Doi Paper", title="A KDD Paper", source="https://dl.acm.org/doi/10.1145/3637528.3671870")
    meta = zotero_push.paper_meta("Doi Paper")
    item = zotero_push.build_item(meta)
    assert item["itemType"] == "journalArticle" and item["DOI"] == "10.1145/3637528.3671870"
    assert meta["ids"] == {"doi:10.1145/3637528.3671870", "url:dl.acm.org/doi/10.1145/3637528.3671870"}
    make_note("Blog", title="A Blog Post", source="https://example.org/post")
    assert zotero_push.build_item(zotero_push.paper_meta("Blog"))["itemType"] == "document"


def test_arxiv_only_item_uses_the_reading_list_title_and_arxiv_metadata(zot, monkeypatch):
    reading.push(arxiv="2610.11920", title="From the list", reason="r", source="self")
    monkeypatch.setattr(zotero_push, "_arxiv_meta", lambda aid: {"title": "API title", "authors": ["Grace Hopper"],
                                                                  "abstract": "Abs.", "date": "2026-10-01"})
    meta = zotero_push.paper_meta("arxiv:2610.11920")
    assert (meta["title"], meta["authors"], meta["date"], meta["note"]) == ("From the list", ["Grace Hopper"], "2026-10-01", None)
    assert zotero_push.build_item(meta)["abstractNote"] == "Abs."


# ---------- status and dedupe against the snapshot ----------


def test_status_linked_when_the_note_already_has_a_link(zot, make_note):
    make_note("Linked", arxiv="2601.00001", zotero="zotero://select/library/items/ABCD2345")
    s = zotero_push.status("Linked")
    assert (s["state"], s["key"], s["link"]) == ("linked", "ABCD2345", "zotero://select/library/items/ABCD2345")
    assert zot.paths() == []


@pytest.mark.parametrize("fields", [
    {"archiveID": "arXiv:2608.21939"},
    {"url": "http://arxiv.org/abs/2608.21939v1"},
    {"DOI": "10.48550/arXiv.2608.21939"},
    {"extra": "arXiv: 2608.21939"},
])
def test_status_finds_the_paper_in_the_snapshot(zot, note, fields):
    zot.fake.add("HAVE2345", **fields)
    s = zotero_push.status("Group Memory 2026")
    assert (s["state"], s["key"]) == ("in_library", "HAVE2345")
    assert s["linkback"]["line"] == "zotero: zotero://select/library/items/HAVE2345"
    assert s["linkback"]["replaces"] is None


def test_trashed_and_group_library_items_do_not_count(zot, note):
    zot.fake.add("TRASH234", archiveID="arXiv:2608.21939")
    zot.fake.trash("TRASH234")
    zot.fake.add("GROUP234", library_id=7, archiveID="arXiv:2608.21939")
    zot.fake.add("CHILD234", url="https://arxiv.org/abs/2608.21939")
    with sqlite3.connect(zot.fake.db) as con:  # an attachment, not a regular item
        con.execute("INSERT INTO itemAttachments SELECT itemID, NULL, 'application/pdf' FROM items WHERE key = 'CHILD234'")
    zot.fake._touch()
    assert zotero_push.status("Group Memory 2026")["state"] == "absent"


def test_collections_come_with_their_path_and_skip_deleted_ones(zot):
    zot.fake.collection(1, "Reading", "COLLAAAA")
    zot.fake.collection(2, "Agents", "COLLBBBB", parent=1)
    zot.fake.collection(3, "Old", "COLLCCCC")
    zot.fake.collection(4, "Group stuff", "COLLDDDD", library_id=7)
    with sqlite3.connect(zot.fake.db) as con:
        con.execute("INSERT INTO deletedCollections VALUES (3)")
    zot.fake._touch()
    assert zotero_push.collections() == [{"key": "COLLAAAA", "name": "Reading"}, {"key": "COLLBBBB", "name": "Reading / Agents"}]


# ---------- pushing through the desktop connector ----------


def test_push_through_the_desktop_app(zot, note):
    r = zotero_push.push("Group Memory 2026")
    assert (r["state"], r["key"], r["via"]) == ("added", "NEWKEY01", "desktop")
    assert r["link"] == "zotero://select/library/items/NEWKEY01"
    assert r["linkback"]["path"] == "Papers/Group Memory 2026.md"
    save = zot.last("/connector/saveItems")
    item = save["body"]["items"][0]
    assert item["id"] == "studio" and item["title"] == "Enhancing Group Recommendation with Memory"
    assert save["headers"]["x-zotero-connector-api-version"] == "3"
    assert not save["headers"]["user-agent"].startswith("Mozilla/")  # Zotero refuses browser-looking requests
    # No collection configured: the item goes to the library root, not whatever is selected in Zotero.
    assert zot.last("/connector/updateSession")["body"] == {"sessionID": save["body"]["sessionID"], "target": "L1", "tags": []}
    assert "/connector/saveAttachment" not in zot.paths()  # no local PDF, nothing downloaded for Zotero
    assert note.read_text(encoding="utf-8") == NOTE  # the note is never written by push

    again = zotero_push.push("Group Memory 2026")
    assert (again["state"], again["key"]) == ("exists", "NEWKEY01")
    assert zot.paths().count("/connector/saveItems") == 1


def test_push_into_the_configured_collection(zot, note):
    zot.fake.collection(12, "PhD", "COLLPHD2")
    config.save_settings({"zotero_collection": "COLLPHD2"})
    zotero_push.push("Group Memory 2026")
    assert zot.last("/connector/updateSession")["body"]["target"] == "C12"


def test_a_collection_that_is_gone_falls_back_to_the_library_root(zot, note):
    config.save_settings({"zotero_collection": "GONEGONE"})
    r = zotero_push.push("Group Memory 2026")
    assert zot.last("/connector/updateSession")["body"]["target"] == "L1"
    assert any("分类" in w for w in r["warnings"])


def test_local_pdf_is_attached_only_when_zotero_saves_files(zot, note):
    (config.VAULT_PDFS_DIR / "Group Memory 2026.pdf").write_bytes(b"%PDF-1.4 test")
    zotero_push.push("Group Memory 2026")
    att = zot.last("/connector/saveAttachment")
    assert att["body"] == b"%PDF-1.4 test" and att["headers"]["content-type"] == "application/pdf"
    meta = json.loads(att["headers"]["x-metadata"])
    assert meta["parentItemID"] == "studio" and meta["url"] == "https://arxiv.org/pdf/2608.21939"

    zot.calls.clear()
    zot.prefs["downloadAssociatedFiles"] = False
    zotero_push._ping_cache["at"] = 0.0
    zot.fake.trash("NEWKEY01")
    zotero_push.push("Group Memory 2026")
    assert "/connector/saveItems" in zot.paths() and "/connector/saveAttachment" not in zot.paths()


def test_saved_but_key_not_yet_visible(zot, note):
    zot.write_to_db = False
    r = zotero_push.push("Group Memory 2026")
    assert r["state"] == "added" and r["key"] is None and "link" not in r and r["warnings"]


def test_connector_refusal_is_an_error(zot, note):
    zot.save_status = 500
    with pytest.raises(zotero_push.ZoteroError, match="HTTP 500"):
        zotero_push.push("Group Memory 2026")


def test_nothing_to_dedupe_on_is_an_error(zot, make_note):
    make_note("Bare", title="No source at all")
    with pytest.raises(zotero_push.ZoteroError, match="查重"):
        zotero_push.push("Bare")
    assert zot.paths() == []


# ---------- the Web API ----------


def test_push_through_the_web_api_when_the_desktop_app_is_closed(zot, note):
    zot.desktop(False)
    zot.web_key()
    zot.fake.collection(12, "PhD", "COLLPHD2")
    config.save_settings({"zotero_collection": "COLLPHD2"})
    r = zotero_push.push("Group Memory 2026")
    assert (r["state"], r["key"], r["via"]) == ("added", "NEWKEY01", "web")
    search, create, attach = [c for c in zot.calls if c["path"] == "/users/4242/items"]
    assert search["method"] == "GET" and search["query"]["q"] == ["2608.21939"]
    assert search["headers"]["zotero-api-key"] == "testkey123"
    assert create["body"][0]["collections"] == ["COLLPHD2"] and create["body"][0]["itemType"] == "preprint"
    assert len(create["headers"]["zotero-write-token"]) == 32
    assert attach["body"][0] == {"itemType": "attachment", "linkMode": "linked_url", "parentItem": "NEWKEY01",
                                 "title": "PDF", "url": "https://arxiv.org/pdf/2608.21939", "contentType": "application/pdf"}
    # The snapshot doesn't have it yet (the desktop app hasn't synced), but this Mac remembers it.
    assert zotero_push.status("Group Memory 2026")["key"] == "NEWKEY01"
    assert zotero_push.push("Group Memory 2026")["state"] == "exists"


def test_web_api_user_id_is_looked_up_from_the_key(zot, note):
    zot.desktop(False)
    zot.web_key(user=None)
    assert zotero_push.web_creds() == ("testkey123", "4242")
    assert zot.paths() == ["/keys/current"]


def test_an_item_only_the_online_library_has_is_not_added_again(zot, note):
    zot.web_key()
    zot.remote = [{"key": "OTHER234", "data": {"key": "OTHER234", "archiveID": "arXiv:2608.21939"}},
                  {"key": "TRASHED2", "data": {"key": "TRASHED2", "deleted": 1, "url": "https://arxiv.org/abs/2608.21939"}}]
    r = zotero_push.push("Group Memory 2026")
    assert (r["state"], r["key"]) == ("exists", "OTHER234")
    assert "/connector/saveItems" not in zot.paths()


def test_a_trashed_online_match_does_not_block_adding(zot, note):
    zot.web_key()
    zot.remote = [{"key": "TRASHED2", "data": {"key": "TRASHED2", "deleted": 1, "archiveID": "arXiv:2608.21939"}}]
    assert zotero_push.push("Group Memory 2026")["state"] == "added"


def test_no_way_into_zotero(zot, note):
    zot.desktop(False)
    with pytest.raises(zotero_push.ZoteroError, match="没在运行"):
        zotero_push.push("Group Memory 2026")


def test_pushes_trusted_only_while_not_trashed_and_recent(zot, note):
    ids = {"arxiv:2608.21939"}
    zotero_push._record("Group Memory 2026", "PUSHED23", ids, "web")
    assert zotero_push.find_local(ids) == "PUSHED23"
    entries = json.loads(zotero_push.PUSHED_FILE.read_text())
    entries[0]["ts"] -= 8 * 86400
    zotero_push.PUSHED_FILE.write_text(json.dumps(entries))
    assert zotero_push.find_local(ids) is None  # old and never seen in the snapshot
    zot.fake.add("PUSHED23", title="whatever")
    assert zotero_push.find_local(ids) == "PUSHED23"  # the snapshot has the key: still valid
    zot.fake.trash("PUSHED23")
    assert zotero_push.find_local(ids) is None


# ---------- writing the link into the note ----------


def test_write_link_adds_one_front_matter_line(zot, note):
    before = note.stat()
    prev = zotero_push.link_preview("Group Memory 2026", "NEWKEY01")
    r = zotero_push.write_link("Group Memory 2026", "NEWKEY01", prev["base"])
    assert r == {"stem": "Group Memory 2026", "line": "zotero: zotero://select/library/items/NEWKEY01", "written": True}
    assert note.read_text(encoding="utf-8") == NOTE.replace("---\n", "---\nzotero: zotero://select/library/items/NEWKEY01\n", 1)
    assert note.stat().st_ino == before.st_ino  # rewritten in place, same file
    assert library.get_paper("Group Memory 2026")["zotero_key"] == "NEWKEY01"
    assert zotero_push.status("Group Memory 2026")["state"] == "linked"


def test_write_link_refuses_when_the_note_changed_since_the_preview(zot, note):
    prev = zotero_push.link_preview("Group Memory 2026", "NEWKEY01")
    note.write_text(NOTE + "\nedited\n", encoding="utf-8")
    with pytest.raises(LookupError):
        zotero_push.write_link("Group Memory 2026", "NEWKEY01", prev["base"])
    assert note.read_text(encoding="utf-8") == NOTE + "\nedited\n"


def test_write_link_never_replaces_another_link(zot, make_note):
    p = make_note("Other", arxiv="2601.00001", zotero="zotero://select/library/items/ABCD2345")
    text = p.read_text(encoding="utf-8")
    prev = zotero_push.link_preview("Other", "NEWKEY01")
    assert prev["replaces"] == "zotero: zotero://select/library/items/ABCD2345"
    with pytest.raises(LookupError):
        zotero_push.write_link("Other", "NEWKEY01", prev["base"])
    assert p.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("src, expected", [
    ("---\r\ntype: paper\r\n---\r\nBody\r\n", "---\r\nzotero: zotero://select/library/items/NEWKEY01\r\ntype: paper\r\n---\r\nBody\r\n"),
    ("---\n---\nBody\n", "---\nzotero: zotero://select/library/items/NEWKEY01\n---\nBody\n"),
    ("# No front matter\n", "---\nzotero: zotero://select/library/items/NEWKEY01\n---\n# No front matter\n"),
    ("---\ntype: paper\nzotero:\n---\n", "---\ntype: paper\nzotero: zotero://select/library/items/NEWKEY01\n---\n"),
])
def test_write_link_keeps_the_note_format(zot, src, expected):
    p = config.PAPERS_DIR / "Fmt.md"
    p.write_bytes(src.encode("utf-8"))
    zotero_push.write_link("Fmt", "NEWKEY01", zotero_push.link_preview("Fmt", "NEWKEY01")["base"])
    assert p.read_bytes().decode("utf-8") == expected


def test_write_link_checks_the_key(zot, note):
    with pytest.raises(ValueError):
        zotero_push.write_link("Group Memory 2026", "bad key\nx: y", "whatever")


def test_pending_links_list_pushed_notes_without_the_link(zot, note, make_note):
    zotero_push.push("Group Memory 2026")
    reading.push(arxiv="2610.11920", title="T", reason="r", source="self")
    zotero_push._record("arxiv:2610.11920", "ARXIV234", {"arxiv:2610.11920"}, "web")
    assert [p["stem"] for p in zotero_push.pending_links()] == ["Group Memory 2026"]  # no note for the arXiv one yet
    make_note("Later Note 2026", arxiv="2610.11920", title="T")
    assert [(p["stem"], p["key"]) for p in zotero_push.pending_links()] == [
        ("Later Note 2026", "ARXIV234"), ("Group Memory 2026", "NEWKEY01")]
    prev = zotero_push.pending_links()[1]
    zotero_push.write_link(prev["stem"], prev["key"], prev["base"])
    assert [p["stem"] for p in zotero_push.pending_links()] == ["Later Note 2026"]


# ---------- auto mode ----------


def _touch_list():
    t = time.time() + 5
    os.utime(config.READING_LIST, (t, t))


def test_auto_mode_first_pass_only_records(zot, note):
    config.save_settings({"zotero_auto": "all"})
    reading.push(paper="Group Memory 2026", reason="r", source="self", tier="T1")
    assert zotero_push.auto_tick() == []
    assert zot.paths() == []


def test_auto_triage_pushes_papers_given_a_tier_and_new_notes(zot, note, make_note, monkeypatch):
    monkeypatch.setattr(zotero_push, "_arxiv_meta", lambda aid: {})
    config.save_settings({"zotero_auto": "triage"})
    item, _ = reading.push(paper="Group Memory 2026", reason="r", source="tracker")
    zotero_push.auto_tick()  # seed
    reading.push(arxiv="2610.11920", title="Inbox only", reason="r", source="tracker")
    _touch_list()
    assert zotero_push.auto_tick() == []  # an inbox add is not enough in triage mode

    reading.update(item["id"], {"tier": "T1"})
    _touch_list()
    r = zotero_push.auto_tick()
    assert [(x["paper"], x["state"], x["key"]) for x in r] == [("Group Memory 2026", "added", "NEWKEY01")]
    assert note.read_text(encoding="utf-8") == NOTE  # the link is left for the user to confirm
    assert [p["stem"] for p in zotero_push.pending_links()] == ["Group Memory 2026"]

    make_note("Fresh Note", arxiv="2609.00002", title="Fresh")
    os.utime(config.PAPERS_DIR, (time.time() + 9, time.time() + 9))
    assert [x["paper"] for x in zotero_push.auto_tick()] == ["Fresh Note"]
    assert zotero_push.auto_tick() == []  # nothing changed since
    assert zotero_push.auto_status()["recent"][0]["paper"] == "Fresh Note"


def test_auto_all_pushes_inbox_additions(zot, monkeypatch):
    monkeypatch.setattr(zotero_push, "_arxiv_meta", lambda aid: {})
    config.save_settings({"zotero_auto": "all"})
    zotero_push.auto_tick()
    reading.push(arxiv="2610.11920", title="Inbox", reason="r", source="tracker")
    _touch_list()
    assert [(x["paper"], x["state"]) for x in zotero_push.auto_tick()] == [("arxiv:2610.11920", "added")]


def test_auto_off_keeps_track_without_pushing(zot, note):
    zotero_push.auto_tick()
    reading.push(paper="Group Memory 2026", reason="r", source="self", tier="T1")
    _touch_list()
    assert zotero_push.auto_tick() == []
    config.save_settings({"zotero_auto": "triage"})  # turning it on later doesn't back-fill
    assert zotero_push.auto_tick(force=True) == []
    assert zot.paths() == []


def test_auto_failures_are_retried_later(zot, note):
    config.save_settings({"zotero_auto": "triage"})
    zotero_push.auto_tick()
    zot.desktop(False)
    reading.push(paper="Group Memory 2026", reason="r", source="self", tier="T1")
    _touch_list()
    assert [x["state"] for x in zotero_push.auto_tick()] == ["error"]
    assert zotero_push.auto_status()["retry"] == 1
    zot.desktop(True)
    state = json.loads(zotero_push.AUTO_FILE.read_text())
    state["retried"] = 0
    zotero_push.AUTO_FILE.write_text(json.dumps(state))
    assert [x["state"] for x in zotero_push.auto_tick()] == ["added"]
    assert zotero_push.auto_status()["retry"] == 0


# ---------- routes ----------


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)  # not as a context manager: no lifespan, so no auto-mode thread


def test_routes(zot, note, client):
    assert client.get("/api/zotero/paper/Nope").status_code == 404
    assert client.get("/api/zotero/paper/..%2F..%2Fetc").status_code == 404
    assert client.get("/api/zotero/paper/Group Memory 2026").json()["state"] == "absent"
    r = client.post("/api/zotero/paper/Group Memory 2026").json()
    assert r["state"] == "added" and r["linkback"]["base"]
    setup = client.get("/api/zotero/setup").json()
    assert setup["desktop"] is True and setup["web"] is False
    assert setup["secrets_file"].endswith("zotero.env") and [p["stem"] for p in setup["pending"]] == ["Group Memory 2026"]
    bad = client.post("/api/zotero/link/Group Memory 2026", json={"key": "NEWKEY01", "base": "stale"})
    assert bad.status_code == 409 and note.read_text(encoding="utf-8") == NOTE
    ok = client.post("/api/zotero/link/Group Memory 2026", json={"key": "NEWKEY01", "base": r["linkback"]["base"]})
    assert ok.json()["written"] is True
    assert client.post("/api/zotero/link/Nope", json={"key": "NEWKEY01", "base": "x"}).status_code == 404


def test_route_reports_zotero_errors(zot, note, client):
    zot.desktop(False)
    r = client.post("/api/zotero/paper/Group Memory 2026")
    assert r.status_code == 502 and "没在运行" in r.json()["detail"]


def test_module_state_lives_in_the_sandbox():
    for p in (zotero_push.PUSHED_FILE, zotero_push.AUTO_FILE):
        assert p.is_relative_to(ROOT)
    assert zotero_push.CONNECTOR != "" and "4242" not in zotero_push.WEB_API
