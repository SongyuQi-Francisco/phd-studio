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
from urllib.parse import parse_qs, unquote, urlparse

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
        self.crossref: dict[str, dict[str, Any]] = {}  # DOI -> Crossref "message"
        self.arxiv: dict[str, str] = {}  # arXiv id -> Atom <entry> body
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
                if path.startswith("/works/"):
                    doi = unquote(path[len("/works/"):])
                    return self._send(200, {"message": mock.crossref[doi]}) if doi in mock.crossref else self._send(404)
                if path == "/arxiv":
                    aid = parse_qs(urlparse(self.path).query)["id_list"][0]
                    data = f"<feed><entry>{mock.arxiv[aid]}</entry></feed>".encode() if aid in mock.arxiv else b"<feed></feed>"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    return self.wfile.write(data)
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
    monkeypatch.setattr(zotero_push, "CROSSREF", mock.base)
    monkeypatch.setattr(zotero_push, "ARXIV_API", mock.base + "/arxiv")
    zotero_push._meta_failed.clear()
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


SIGIR = {"type": "proceedings-article", "title": ["A SIGIR Paper"],
         "container-title": ["Proceedings of the 48th International ACM SIGIR Conference on Research and Development in Information Retrieval"],
         "event": {"name": "SIGIR '25: The 48th International ACM SIGIR Conference"},
         "author": [{"given": "Ada", "family": "Lovelace"}, {"given": "Alan M.", "family": "Turing"}],
         "issued": {"date-parts": [[2025, 7, 13]]}, "page": "100-110", "publisher": "ACM"}


def test_a_doi_conference_paper_becomes_a_conference_paper(zot, make_note):
    make_note("Doi Paper", title="A SIGIR Paper", source="https://dl.acm.org/doi/10.1145/3726302.3730238",
              body="- Venue / status: SIGIR 2025; ACM DOI verified\n")
    zot.crossref["10.1145/3726302.3730238"] = SIGIR
    meta = zotero_push.paper_meta("Doi Paper")
    assert meta["ids"] == {"doi:10.1145/3726302.3730238", "url:dl.acm.org/doi/10.1145/3726302.3730238"}
    assert zotero_push.enrich(meta) == []
    item = zotero_push.build_item(meta)
    assert item["itemType"] == "conferencePaper" and item["DOI"] == "10.1145/3726302.3730238"
    assert item["proceedingsTitle"].startswith("Proceedings of the 48th International ACM SIGIR")
    assert item["conferenceName"].startswith("SIGIR '25") and item["pages"] == "100-110" and item["date"] == "2025-07-13"
    assert item["creators"][1] == {"creatorType": "author", "firstName": "Alan M.", "lastName": "Turing"}
    assert "publicationTitle" not in item and "archiveID" not in item
    # Cached: a second lookup needs no network.
    zot.crossref.clear()
    meta2 = zotero_push.paper_meta("Doi Paper")
    assert zotero_push.enrich(meta2) == [] and meta2["crossref"]["type"] == "proceedings-article"


def test_without_crossref_the_venue_text_is_cleaned_and_reported(zot, make_note):
    make_note("Offline", title="Offline Paper", source="https://dl.acm.org/doi/10.1145/3626772.3657669",
              body="- Venue / status: SIGIR 2024 demo; ACM DOI verified\n")
    meta = zotero_push.paper_meta("Offline")
    warnings = zotero_push.enrich(meta)  # the mock has no record: Crossref answers 404
    assert len(warnings) == 1 and "DOI" in warnings[0]
    item = zotero_push.build_item(meta)
    assert (item["itemType"], item["proceedingsTitle"]) == ("conferencePaper", "SIGIR 2024")


@pytest.mark.parametrize("venue, kind, name", [
    ("SIGIR 2025; ACM DOI verified", "conferencePaper", "SIGIR 2025"),
    ("WWW Companion 2025; ACM DOI verified", "conferencePaper", "WWW Companion 2025"),
    ("AAAI 2026 Technical Track, Proceedings of AAAI 40(37), 31636-31644", "conferencePaper", "AAAI 2026"),
    ("IAAI / AAAI 2026 proceedings, Proceedings of AAAI 40(47)", "conferencePaper", "IAAI / AAAI 2026"),
    ("ACM TOIS 2025", "journalArticle", "ACM TOIS 2025"),
    ("IEEE Transactions on Knowledge and Data Engineering", "journalArticle", "IEEE Transactions on Knowledge and Data Engineering"),
])
def test_venue_text_without_crossref(venue, kind, name):
    assert zotero_push._guess_type(venue) == kind and zotero_push.venue_name(venue) == name


def test_url_only_notes_are_documents(zot, make_note):
    make_note("Blog", title="A Blog Post", source="https://example.org/post")
    assert zotero_push.build_item(zotero_push.paper_meta("Blog"))["itemType"] == "document"


ATOM = ("<title>API title</title><summary>Abs.</summary><published>2026-10-01T00:00:00Z</published>"
        "<author><name>Grace Hopper</name></author>")


def test_arxiv_only_item_uses_the_reading_list_title_and_arxiv_metadata(zot):
    reading.push(arxiv="2610.11920", title="From the list", reason="r", source="self")
    zot.arxiv["2610.11920"] = ATOM
    meta = zotero_push.paper_meta("arxiv:2610.11920")
    assert (meta["title"], meta["authors"], meta["note"]) == ("From the list", [], None)  # no network yet
    assert zot.paths() == []
    assert zotero_push.enrich(meta) == []
    assert (meta["title"], meta["authors"], meta["date"]) == ("From the list", ["Grace Hopper"], "2026-10-01")
    assert zotero_push.build_item(meta)["abstractNote"] == "Abs."


def test_status_never_fetches_metadata(zot):
    reading.push(arxiv="2610.11920", title="T", reason="r", source="self")
    zot.arxiv["2610.11920"] = ATOM
    assert zotero_push.status("arxiv:2610.11920")["state"] == "absent"
    assert [p for p in zot.paths() if p in ("/arxiv",) or p.startswith("/works/")] == []


def test_arxiv_failures_are_reported_and_not_retried_at_once(zot):
    reading.push(arxiv="2610.99999", title="Unknown", reason="r", source="self")
    r = zotero_push.push("arxiv:2610.99999")  # the mock arXiv has no such id
    assert r["state"] == "added" and any("arXiv" in w for w in r["warnings"])
    zot.calls.clear()
    meta = zotero_push.paper_meta("arxiv:2610.99999")
    assert zotero_push.enrich(meta) and "/arxiv" not in zot.paths()  # remembered for 10 minutes


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


def test_saved_but_key_not_yet_visible_is_not_added_twice(zot, note):
    zot.write_to_db = False
    r = zotero_push.push("Group Memory 2026")
    assert r["state"] == "added" and r["key"] is None and "link" not in r and r["warnings"]
    s = zotero_push.status("Group Memory 2026")
    assert (s["state"], s["key"], s["pending"]) == ("in_library", None, True)
    again = zotero_push.push("Group Memory 2026")  # the second click the UI used to invite
    assert again["state"] == "pending" and again["warnings"]
    assert zot.paths().count("/connector/saveItems") == 1
    zot.fake.add("LATEKEY2", archiveID="arXiv:2608.21939")  # Zotero finally wrote it
    assert zotero_push.status("Group Memory 2026")["key"] == "LATEKEY2"
    assert [p["key"] for p in zotero_push.pending_links()] == ["LATEKEY2"]
    assert zotero_push.push("Group Memory 2026")["state"] == "exists"


def test_a_pending_save_expires(zot, note):
    zot.write_to_db = False
    zotero_push.push("Group Memory 2026")
    entries = json.loads(zotero_push.PUSHED_FILE.read_text())
    entries[-1]["ts"] -= (zotero_push.PENDING_HOURS + 1) * 3600
    zotero_push.PUSHED_FILE.write_text(json.dumps(entries))
    assert zotero_push.push("Group Memory 2026")["state"] == "added"
    assert zot.paths().count("/connector/saveItems") == 2
    assert len(json.loads(zotero_push.PUSHED_FILE.read_text())) == 1  # the new pending record replaced the old


def test_an_unreadable_snapshot_fails_closed(zot, note, client):
    zot.fake.db.write_bytes(b"this is not a database at all" * 100)
    zot.fake._touch()
    s = zotero_push.status("Group Memory 2026")
    assert s["state"] == "absent" and "快照" in s["error"]
    with pytest.raises(zotero_push.SnapshotError):
        zotero_push.push("Group Memory 2026")
    assert "/connector/saveItems" not in zot.paths()
    setup = client.get("/api/zotero/setup").json()
    assert "快照" in setup["snapshot_error"] and setup["collections"] == [] and setup["pending"] == []
    r = client.post("/api/zotero/paper/Group Memory 2026")
    assert r.status_code == 502 and "快照" in r.json()["detail"]


def test_a_bad_copy_of_a_good_database_heals(zot, note):
    zot.fake.add("HAVE2345", archiveID="arXiv:2608.21939")
    assert zotero_push.status("Group Memory 2026")["key"] == "HAVE2345"  # the app's copy now exists
    copy = library._ZOTERO_SNAPSHOT
    copy.write_bytes(b"not a database" * 100)
    t = zot.fake.stamp + 50  # newer than Zotero's file, so it would never be copied again on its own
    os.utime(copy, (t, t))
    zotero_push._index.cache_clear()
    assert zotero_push.status("Group Memory 2026")["key"] == "HAVE2345"
    assert copy.read_bytes()[:15] == b"SQLite format 3"


def test_a_missing_optional_table_is_fine_but_a_missing_core_table_is_not(zot, note):
    import sqlite3
    with sqlite3.connect(zot.fake.db) as con:
        con.execute("DROP TABLE deletedCollections")
    zot.fake._touch()
    assert zotero_push.status("Group Memory 2026")["state"] == "absent"
    with sqlite3.connect(zot.fake.db) as con:
        con.execute("DROP TABLE deletedItems")
    zot.fake._touch()
    assert "error" in zotero_push.status("Group Memory 2026")


def test_desktop_app_running_but_its_database_not_found(zot, note):
    for f in config.ZOTERO_DIR.glob("zotero.sqlite*"):
        f.unlink()
    with pytest.raises(zotero_push.ZoteroError, match="找不到它的数据库"):
        zotero_push.push("Group Memory 2026")
    assert "/connector/saveItems" not in zot.paths()


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


def test_auto_triage_pushes_papers_given_a_tier_and_new_notes(zot, note, make_note):
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


def test_auto_all_pushes_inbox_additions(zot):
    config.save_settings({"zotero_auto": "all"})
    reading.load()  # creates the (empty) list
    zotero_push.auto_tick()
    reading.push(arxiv="2610.11920", title="Inbox", reason="r", source="tracker")
    _touch_list()
    assert [(x["paper"], x["state"]) for x in zotero_push.auto_tick()] == [("arxiv:2610.11920", "added")]


def test_auto_off_keeps_track_without_pushing(zot, note):
    reading.load()
    zotero_push.auto_tick()
    reading.push(paper="Group Memory 2026", reason="r", source="self", tier="T1")
    _touch_list()
    assert zotero_push.auto_tick() == []
    config.save_settings({"zotero_auto": "triage"})  # turning it on later doesn't back-fill
    assert zotero_push.auto_tick(force=True) == []
    assert zot.paths() == []


def test_auto_mode_does_not_take_its_first_look_at_a_missing_list(zot, note):
    config.save_settings({"zotero_auto": "all"})
    assert not config.READING_LIST.exists()
    assert zotero_push.auto_tick() == [] and not zotero_push.AUTO_FILE.exists()  # no empty first look recorded
    reading.push(paper="Group Memory 2026", reason="r", source="tracker")
    assert zotero_push.auto_tick() == []  # the first real look only records
    assert zot.paths() == []


@pytest.mark.parametrize("gap", ["list", "papers"])
def test_auto_mode_treats_a_vanished_list_or_folder_as_a_gap(zot, note, make_note, gap):
    config.save_settings({"zotero_auto": "all"})
    make_note("Other 2026", arxiv="2601.00042", title="Other")
    reading.push(paper="Group Memory 2026", reason="r", source="tracker")
    zotero_push.auto_tick()  # seed: one item, two notes
    if gap == "list":
        saved = config.READING_LIST.read_bytes()
        config.READING_LIST.unlink()
    else:
        moved = config.DATA_DIR / "papers-away"
        config.PAPERS_DIR.rename(moved)
    assert zotero_push.auto_tick(force=True) == []
    if gap == "list":
        config.READING_LIST.write_bytes(saved)
        _touch_list()
    else:
        moved.rename(config.PAPERS_DIR)
        os.utime(config.PAPERS_DIR, (time.time() + 9, time.time() + 9))
    assert zotero_push.auto_tick() == []  # nothing came back as "new"
    assert zot.paths() == []


def test_auto_failures_are_retried_later(zot, note):
    config.save_settings({"zotero_auto": "triage"})
    reading.load()
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


# ---------- papers known by doi: / openreview: / acl: keys (captured without a note or arXiv id) ----------

from app import sources  # noqa: E402


def test_a_doi_key_is_pushed_as_the_published_paper(zot):
    reading.push(ext="doi:10.1145|3726302.3730238", title="A SIGIR Paper", reason="r", source="self")
    zot.crossref["10.1145/3726302.3730238"] = SIGIR
    key = "doi:10.1145|3726302.3730238"
    meta = zotero_push.paper_meta(key)
    assert (meta["doi"], meta["url"]) == ("10.1145/3726302.3730238", "https://doi.org/10.1145/3726302.3730238")
    assert "doi:10.1145/3726302.3730238" in meta["ids"]
    assert zotero_push.status(key)["state"] == "absent"
    r = zotero_push.push(key)
    assert r["state"] == "added" and r["warnings"] == []
    item = zot.last("/connector/saveItems")["body"]["items"][0]
    assert item["itemType"] == "conferencePaper" and item["DOI"] == "10.1145/3726302.3730238"
    assert zotero_push.push(key)["state"] == "exists"  # the saved item's DOI dedupes it


def test_an_arxiv_item_that_also_has_a_doi_uses_the_published_version(zot):
    reading.push(arxiv="2610.11920", ext="doi:10.1145|3726302.3730238", title="Both", reason="r", source="self")
    zot.crossref["10.1145/3726302.3730238"] = SIGIR
    meta = zotero_push.paper_meta("arxiv:2610.11920")
    assert meta["doi"] == "10.1145/3726302.3730238" and {"arxiv:2610.11920", "doi:10.1145/3726302.3730238"} <= meta["ids"]
    zotero_push.enrich(meta)
    item = zotero_push.build_item(meta)
    assert item["itemType"] == "conferencePaper" and item["extra"].startswith("arXiv: 2610.11920")


def test_an_acl_key(zot, monkeypatch):
    reading.push(ext="acl:2024.acl-long.15", title="An ACL Paper", reason="r", source="self")
    key = "acl:2024.acl-long.15"
    meta = zotero_push.paper_meta(key)
    assert meta["url"] == "https://aclanthology.org/2024.acl-long.15/"
    assert {"acl:2024.acl-long.15", "url:aclanthology.org/2024.acl-long.15", "doi:10.18653/v1/2024.acl-long.15"} <= meta["ids"]
    assert meta["pdf_url"] == "https://aclanthology.org/2024.acl-long.15.pdf"
    monkeypatch.setattr(sources, "acl_meta", lambda acl_id: sources.candidate(
        title="An ACL Paper", authors=["Ada Lovelace"], year=2024, acl=acl_id,
        venue="Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics"))
    assert zotero_push.enrich(meta) == []
    item = zotero_push.build_item(meta)
    assert item["itemType"] == "conferencePaper" and item["proceedingsTitle"].startswith("Proceedings of the 62nd")
    assert "DOI" not in item  # the derived Anthology DOI only matches, it isn't written


def test_an_acl_paper_already_in_zotero_is_found_by_its_doi_or_link(zot):
    reading.push(ext="acl:2024.acl-long.15", title="T", reason="r", source="self")
    zot.fake.add("ACLKEY23", DOI="10.18653/v1/2024.acl-long.15")
    assert zotero_push.status("acl:2024.acl-long.15")["key"] == "ACLKEY23"
    reading.push(ext="acl:P19-1001", title="Old", reason="r", source="self")
    zot.fake.add("ACLOLD23", url="https://www.aclanthology.org/P19-1001.pdf")
    assert zotero_push.status("acl:P19-1001")["key"] == "ACLOLD23"


@pytest.mark.parametrize("venue, kind, proceedings", [
    ("ICLR 2025 Poster", "conferencePaper", "ICLR 2025"),
    ("Submitted to ICLR 2026", "preprint", None),
])
def test_an_openreview_key(zot, monkeypatch, venue, kind, proceedings):
    reading.push(ext="openreview:AbCdEf123", title="An OpenReview Paper", reason="r", source="self")
    meta = zotero_push.paper_meta("openreview:AbCdEf123")
    assert meta["url"] == "https://openreview.net/forum?id=AbCdEf123" and "openreview:AbCdEf123" in meta["ids"]
    monkeypatch.setattr(sources, "openreview_meta", lambda forum: sources.candidate(
        title="An OpenReview Paper", authors=["Grace Hopper"], year=2025, venue=venue, openreview=forum))
    zotero_push.enrich(meta)
    item = zotero_push.build_item(meta)
    assert item["itemType"] == kind and item.get("proceedingsTitle") == proceedings
    if kind == "preprint":
        assert item["repository"] == "OpenReview"


def test_an_openreview_paper_saved_from_its_pdf_link_is_found(zot):
    reading.push(ext="openreview:AbCdEf123", title="T", reason="r", source="self")
    zot.fake.add("ORKEY234", url="https://openreview.net/pdf?id=AbCdEf123")
    assert zotero_push.status("openreview:AbCdEf123")["key"] == "ORKEY234"


def test_external_metadata_failures_are_warnings(zot, monkeypatch):
    reading.push(ext="openreview:AbCdEf123", title="T", reason="r", source="self")

    def human_check(forum):
        raise sources.SourceError("OpenReview 要求人机验证，暂时查不了标题")
    monkeypatch.setattr(sources, "openreview_meta", human_check)
    r = zotero_push.push("openreview:AbCdEf123")
    assert r["state"] == "added" and any("OpenReview" in w and "人机验证" in w for w in r["warnings"])


def test_a_note_written_later_for_a_doi_key_gets_the_link(zot, make_note):
    reading.push(ext="doi:10.1145|3726302.3730238", title="A SIGIR Paper", reason="r", source="self")
    zot.crossref["10.1145/3726302.3730238"] = SIGIR
    zotero_push.push("doi:10.1145|3726302.3730238")
    assert zotero_push.pending_links() == []  # no note yet
    make_note("Sigir Note 2025", title="A SIGIR Paper", source="https://dl.acm.org/doi/10.1145/3726302.3730238")
    assert [p["stem"] for p in zotero_push.pending_links()] == ["Sigir Note 2025"]
    s = zotero_push.status("doi:10.1145|3726302.3730238")
    assert s["linkback"]["stem"] == "Sigir Note 2025"


def test_routes_accept_external_keys(zot, client):
    reading.push(ext="doi:10.1145|3726302.3730238", title="T", reason="r", source="self")
    assert client.get("/api/zotero/paper/doi:10.1145|3726302.3730238").json()["state"] == "absent"
    assert client.get("/api/zotero/paper/doi:not-a-doi").status_code == 404


def test_auto_mode_pushes_external_keys(zot, monkeypatch):
    config.save_settings({"zotero_auto": "all"})
    monkeypatch.setattr(sources, "acl_meta", lambda acl_id: sources.candidate(title="ACL", acl=acl_id))
    reading.load()
    zotero_push.auto_tick()
    reading.push(ext="acl:2024.acl-long.15", title="An ACL Paper", reason="r", source="tracker")
    _touch_list()
    assert [(x["paper"], x["state"]) for x in zotero_push.auto_tick()] == [("acl:2024.acl-long.15", "added")]
