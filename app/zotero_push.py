"""Push papers into Zotero: the desktop app's connector when Zotero runs, else the Web API.

This only ever adds items; it never edits or deletes anything in Zotero. Before adding, the paper is
looked up by DOI, arXiv id and URL in the local snapshot of zotero.sqlite, in a per-device record of
earlier pushes and, when a Web API key exists, in the online library (which also sees what the other
Mac added). The resulting zotero://select link goes into the paper note's front matter only after
the user has seen the exact line and confirmed it (link_preview, then write_link).

The Web API key is created by the user at zotero.org/settings/keys and kept in <secrets>/zotero.env
(ZOTERO_API_KEY, ZOTERO_USER_ID). The app never asks for a Zotero password.

Auto mode (setting zotero_auto) watches the reading list and the Papers folder and pushes papers
that newly qualify: given a tier or a note ("triage"), or also on entering the inbox ("all").
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

from . import library, reading
from .config import DATA_DIR, PAPERS_DIR, READING_LIST, SECRETS_DIR, VAULT_DIR, load_settings, secret
from .excerpt import _locked as _note_locked, _read as _read_note, _write as _write_note

CONNECTOR = os.environ.get("STUDIO_ZOTERO_CONNECTOR", "http://127.0.0.1:23119")
WEB_API = os.environ.get("STUDIO_ZOTERO_API", "https://api.zotero.org")
PUSHED_FILE = DATA_DIR / "zotero-pushed.json"  # per device: what this Mac added, until the snapshot shows it
AUTO_FILE = DATA_DIR / "zotero-auto.json"  # per device: what auto mode has already seen
AUTO_MODES = ("off", "triage", "all")
TAG = "phd-studio"
TRUST_DAYS = 7  # an unsynced push still counts as "in Zotero" this long
_UA = "PhD-Studio/0.1"
_LOCK = threading.RLock()  # one push at a time, from clicks and from auto mode
_AUTO_LOCK = threading.Lock()


class ZoteroError(RuntimeError):
    pass


def select_link(key: str) -> str:
    return f"zotero://select/library/items/{key}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ---------- identity: DOI, arXiv id, URL ----------

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.I)
_ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv[:\s]\s*|10\.48550/arxiv\.)(\d{4}\.\d{4,5})", re.I)


def norm_doi(text: str) -> str:
    m = _DOI_RE.search(text or "")
    return m.group(1).rstrip(".,;)]").lower() if m else ""


def norm_url(url: str) -> str:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return ""
    return re.sub(r"^https?://(www\.)?", "", url.split("#")[0], flags=re.I).rstrip("/").lower()


def ident_keys(*, doi: str = "", arxiv: str = "", url: str = "", extra: str = "") -> set[str]:
    """Identity keys ("doi:…", "arxiv:…", "url:…") shared by a paper and its Zotero item."""
    out = set()
    doi_line = re.search(r"^DOI:(.*)$", extra or "", re.M | re.I)
    for d in (norm_doi(doi), norm_doi(doi_line.group(1)) if doi_line else ""):
        if d:
            out.add("doi:" + d)
    for text in (arxiv and f"arXiv:{arxiv}", doi, url, extra):
        for m in _ARXIV_RE.finditer(text or ""):
            out.add("arxiv:" + m.group(1))
    if u := norm_url(url):
        out.add("url:" + u)
    return out


# ---------- the local Zotero snapshot (read-only) ----------

_ITEM_FIELDS = """
SELECT i.key, f.fieldName, v.value
FROM items i
JOIN itemData d ON d.itemID = i.itemID
JOIN fields f ON f.fieldID = d.fieldID AND f.fieldName IN ('DOI', 'url', 'archiveID', 'extra')
JOIN itemDataValues v ON v.valueID = d.valueID
WHERE i.libraryID = ?
  AND i.itemID NOT IN (SELECT itemID FROM itemAttachments)
  AND i.itemID NOT IN (SELECT itemID FROM itemNotes)
"""


def _query(con: sqlite3.Connection, sql: str, *args: Any) -> list[tuple]:
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.Error:  # a table this Zotero version doesn't have
        return []


@lru_cache(maxsize=1)
def _index(_stamp: float) -> dict[str, Any]:
    out: dict[str, Any] = {"by_ident": {}, "live": set(), "deleted": set(), "library": None, "collections": []}
    con = library._zotero_db()
    if con is None:
        return out
    try:
        lib = _query(con, "SELECT libraryID FROM libraries WHERE type = 'user'")
        if not lib:
            return out
        lid = out["library"] = lib[0][0]
        deleted = {k for (k,) in _query(con, "SELECT i.key FROM items i JOIN deletedItems d ON d.itemID = i.itemID")}
        fields: dict[str, dict[str, str]] = {}
        for key, name, value in _query(con, _ITEM_FIELDS, lid):
            fields.setdefault(key, {})[name] = str(value)
        for key, f in fields.items():
            if key in deleted:
                continue
            for k in ident_keys(doi=f.get("DOI", ""), url=f.get("url", ""), extra=f"{f.get('archiveID', '')}\n{f.get('extra', '')}"):
                out["by_ident"].setdefault(k, key)
        out["live"] = {k for (k,) in _query(con, "SELECT key FROM items WHERE libraryID = ?", lid)} - deleted
        out["deleted"] = deleted
        gone = {cid for (cid,) in _query(con, "SELECT collectionID FROM deletedCollections")}
        rows = [r for r in _query(con, "SELECT collectionID, collectionName, parentCollectionID, key FROM collections "
                                       "WHERE libraryID = ?", lid) if r[0] not in gone]
        by_id = {r[0]: r for r in rows}

        def path(r: tuple) -> str:
            names, seen = [r[1]], {r[0]}
            while r[2] in by_id and r[2] not in seen:
                r = by_id[r[2]]
                seen.add(r[0])
                names.append(r[1])
            return " / ".join(reversed(names))
        out["collections"] = sorted(({"key": r[3], "id": r[0], "name": path(r)} for r in rows), key=lambda c: c["name"].lower())
    finally:
        con.close()
    return out


def snapshot() -> dict[str, Any]:
    return _index(library._zotero_stamp())


def collections() -> list[dict[str, Any]]:
    return [{"key": c["key"], "name": c["name"]} for c in snapshot()["collections"]]


def _pushed() -> list[dict[str, Any]]:
    return _load_json(PUSHED_FILE, [])


def find_local(ids: set[str]) -> str | None:
    """The Zotero key of a paper with any of these identities, from the snapshot or this Mac's pushes."""
    snap = snapshot()
    for k in sorted(ids):
        if key := snap["by_ident"].get(k):
            return key
    cutoff = time.time() - TRUST_DAYS * 86400
    for e in reversed(_pushed()):
        if ids & set(e.get("ids", [])) and e["key"] not in snap["deleted"]:
            if e["key"] in snap["live"] or e.get("ts", 0) >= cutoff:
                return e["key"]
    return None


def _record(paper: str, key: str, ids: set[str], via: str) -> None:
    entries = [e for e in _pushed() if e.get("key") != key]
    entries.append({"paper": paper, "key": key, "ids": sorted(ids), "via": via, "at": _now(), "ts": time.time()})
    _save_json(PUSHED_FILE, entries[-500:])


# ---------- the paper's metadata ----------

_FIELD = re.compile(r"^- (Title|Authors|Year|URL|DOI|Venue / status):\s*(.+?)\s*$", re.M)


def _arxiv_meta(aid: str) -> dict[str, Any]:
    """Title, authors, abstract and date from the arXiv API; {} when it can't be reached."""
    try:
        r = httpx.get("https://export.arxiv.org/api/query", params={"id_list": aid}, timeout=10)
        entry = re.search(r"<entry>(.*?)</entry>", r.text, re.S)
    except Exception:  # noqa: BLE001 - optional enrichment
        return {}
    if not entry:
        return {}
    e = entry.group(1)

    def tag(name: str) -> str:
        m = re.search(rf"<{name}>(.*?)</{name}>", e, re.S)
        return " ".join(m.group(1).split()) if m else ""
    return {"title": tag("title"), "abstract": tag("summary"), "date": tag("published")[:10],
            "authors": [" ".join(a.split()) for a in re.findall(r"<name>(.*?)</name>", e, re.S)]}


def paper_meta(key: str) -> dict[str, Any]:
    """What Zotero needs to know about a paper: a note stem, or arxiv:<id> before a note exists."""
    if aid := library.arxiv_key(key):
        item = reading.find(reading._load_raw(), key) or {}
        meta = {"key": key, "note": None, "title": item.get("title") or "", "authors": [], "date": "", "arxiv": aid,
                "doi": "", "url": f"https://arxiv.org/abs/{aid}", "venue": "", "zotero_key": "", "abstract": ""}
    else:
        path = library.note_path(key)
        text = path.read_text(encoding="utf-8", errors="replace")
        info = library._parse_note(path)
        m = library._FM_RE.match(text)
        try:
            fm = (yaml.safe_load(m.group(1)) if m else {}) or {}
        except yaml.YAMLError:
            fm = {}
        fields = dict(_FIELD.findall(text))
        url = fields.get("URL") or info["source"] or ""
        doi = norm_doi(str(fm.get("doi") or "")) or norm_doi(fields.get("DOI", "")) or norm_doi(url if "doi" in url.lower() else "")
        arxiv = info["arxiv"] or next((x.group(1) for x in _ARXIV_RE.finditer(url)), "")
        meta = {"key": key, "note": key, "title": info["title"] if info["title"] != key else "",
                "authors": [a.strip() for a in re.split(r",|;| and ", fields.get("Authors", "")) if a.strip()],
                "date": str(info["year"] or fields.get("Year", "") or ""), "arxiv": arxiv, "doi": doi,
                "url": url if url.startswith("http") else (f"https://arxiv.org/abs/{arxiv}" if arxiv else ""),
                "venue": fields.get("Venue / status", ""), "zotero_key": info["zotero_key"], "abstract": ""}
    meta["authors"] = [a for a in meta["authors"] if a.lower().rstrip(".") not in ("et al", "others")]
    if meta["arxiv"] and not (meta["title"] and meta["authors"]):
        extra = _arxiv_meta(meta["arxiv"])
        meta["title"] = meta["title"] or extra.get("title", "")
        meta["authors"] = meta["authors"] or extra.get("authors", [])
        meta["abstract"] = extra.get("abstract", "")
        meta["date"] = meta["date"] or extra.get("date", "")
    meta["title"] = meta["title"] or key
    meta["ids"] = ident_keys(doi=meta["doi"], arxiv=meta["arxiv"], url=meta["url"])
    meta["pdf_url"] = next((u for u in library.pdf_urls({"arxiv": meta["arxiv"], "source": meta["url"]})), "")
    return meta


def _creator(name: str) -> dict[str, str]:
    name = re.sub(r"\s*\([^)]*\)|[*†‡§¶\d]+$", "", name).strip()  # "Ada Lovelace (Lab)", "Alan Turing*"
    parts = name.split()
    return {"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1] if parts else name}


def build_item(meta: dict[str, Any]) -> dict[str, Any]:
    """A Zotero item in the shape both the connector and the Web API accept."""
    extra = [f"arXiv: {meta['arxiv']}"] if meta["arxiv"] else []
    if meta["note"]:
        extra.append(f"Obsidian note: {meta['note']}")
    item: dict[str, Any] = {"title": meta["title"], "creators": [_creator(a) for a in meta["authors"]],
                            "date": meta["date"], "url": meta["url"], "abstractNote": meta.get("abstract", ""),
                            "tags": [{"tag": TAG}]}
    if meta["arxiv"]:
        item.update(itemType="preprint", repository="arXiv", archiveID=f"arXiv:{meta['arxiv']}", DOI=meta["doi"])
    elif meta["doi"]:
        item.update(itemType="journalArticle", DOI=meta["doi"], publicationTitle=meta["venue"])
    else:
        item["itemType"] = "document"
    item["extra"] = "\n".join(extra)
    return {k: v for k, v in item.items() if v not in ("", None) or k in ("creators", "tags")}


# ---------- transports ----------

_ping_cache: dict[str, Any] = {"at": 0.0, "up": False, "prefs": {}}


def desktop() -> dict[str, Any]:
    """Whether the Zotero desktop app answers on its connector port (cached for a few seconds)."""
    if time.time() - _ping_cache["at"] > 5:
        try:
            r = httpx.post(f"{CONNECTOR}/connector/ping", json={}, timeout=1.5, trust_env=False,
                           headers={"X-Zotero-Connector-API-Version": "3", "User-Agent": _UA})
            up = r.status_code == 200
            prefs = (r.json().get("prefs") or {}) if up and "json" in r.headers.get("content-type", "") else {}
        except (httpx.HTTPError, ValueError):
            up, prefs = False, {}
        _ping_cache.update(at=time.time(), up=up, prefs=prefs)
    return {"up": _ping_cache["up"], "prefs": _ping_cache["prefs"]}


def _connector(path: str, **kw: Any) -> httpx.Response:
    headers = {"X-Zotero-Connector-API-Version": "3", "User-Agent": _UA, **kw.pop("headers", {})}
    try:
        return httpx.post(CONNECTOR + path, headers=headers, timeout=kw.pop("timeout", 20), trust_env=False, **kw)
    except httpx.HTTPError as e:
        _ping_cache["at"] = 0.0
        raise ZoteroError(f"连不上 Zotero 桌面端（{e.__class__.__name__}）") from None


_web_user: dict[str, str] = {}


def web_creds() -> tuple[str, str] | None:
    """(API key, user id) from <secrets>/zotero.env; the id is looked up once if only the key is there."""
    key = secret("ZOTERO_API_KEY", "zotero")
    if not key:
        return None
    uid = secret("ZOTERO_USER_ID", "zotero") or _web_user.get(key, "")
    if not uid:
        try:
            r = httpx.get(f"{WEB_API}/keys/current", headers={"Zotero-API-Key": key, "Zotero-API-Version": "3"}, timeout=15)
            uid = str(r.json().get("userID") or "") if r.status_code == 200 else ""
        except (httpx.HTTPError, ValueError):
            uid = ""
        if uid:
            _web_user[key] = uid
    return (key, uid) if uid else None


def _web(method: str, path: str, key: str, **kw: Any) -> httpx.Response:
    headers = {"Zotero-API-Key": key, "Zotero-API-Version": "3", "User-Agent": _UA, **kw.pop("headers", {})}
    fn = httpx.get if method == "GET" else httpx.post
    try:
        r = fn(WEB_API + path, headers=headers, timeout=30, **kw)
    except httpx.HTTPError as e:
        raise ZoteroError(f"连不上 Zotero 网络服务（{e.__class__.__name__}）") from None
    if r.status_code == 403:
        raise ZoteroError("Zotero 密钥没有这个权限：在 zotero.org/settings/keys 给它勾上 Allow library access 和 Allow write access")
    if r.status_code in (429, 503):
        raise ZoteroError("Zotero 服务器忙，稍后再试")
    if r.status_code >= 400:
        raise ZoteroError(f"Zotero 网络服务返回 HTTP {r.status_code}")
    return r


def _remote_find(ids: set[str], meta: dict[str, Any]) -> str | None:
    """The key of a matching item in the online library (catches the other Mac's unsynced additions)."""
    creds = web_creds()
    if not creds or not ids:
        return None
    key, uid = creds
    needle = meta["arxiv"] or meta["doi"] or meta["title"]
    try:
        r = _web("GET", f"/users/{uid}/items", key,
                 params={"q": needle, "qmode": "everything", "itemType": "-attachment", "limit": 25})
        rows = r.json()
    except (ZoteroError, ValueError):
        return None
    for row in rows if isinstance(rows, list) else []:
        d = row.get("data") or {}
        if d.get("deleted"):
            continue
        theirs = ident_keys(doi=d.get("DOI", ""), url=d.get("url", ""), extra=f"{d.get('archiveID', '')}\n{d.get('extra', '')}")
        if ids & theirs:
            return d.get("key") or row.get("key")
    return None


def _target_collection() -> tuple[dict[str, Any] | None, str | None]:
    """The configured collection from the snapshot, and a warning when it no longer exists."""
    want = load_settings().get("zotero_collection") or ""
    if not want:
        return None, None
    hit = next((c for c in snapshot()["collections"] if c["key"] == want), None)
    return hit, None if hit else "设置里选的 Zotero 分类已经不在了，这次放在了库的根目录"


def _await_key(ids: set[str], wait: float = 8.0) -> str | None:
    """After a connector save: the new item's key, once Zotero has written it to its database."""
    end = time.time() + wait
    while True:
        snap = snapshot()
        for k in sorted(ids):
            if key := snap["by_ident"].get(k):
                return key
        if time.time() >= end:
            return None
        time.sleep(0.5)


def _push_connector(meta: dict[str, Any], item: dict[str, Any]) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    session = uuid.uuid4().hex
    r = _connector("/connector/saveItems", json={"sessionID": session, "uri": item.get("url", ""), "items": [{**item, "id": "studio"}]})
    if r.status_code != 201:
        raise ZoteroError(f"Zotero 桌面端没有保存（HTTP {r.status_code}）")
    coll, warn = _target_collection()
    warnings += [warn] if warn else []
    lid = snapshot()["library"]
    target = f"C{coll['id']}" if coll else (f"L{lid}" if lid is not None else None)
    if target:
        r = _connector("/connector/updateSession", json={"sessionID": session, "target": target, "tags": []})
        if r.status_code != 200:
            warnings.append("没能放进所选分类，条目在 Zotero 当前选中的位置")
    # Like the browser connector: attach the PDF itself only when Zotero is set to save associated files,
    # and only a PDF that is already on this Mac (nothing is downloaded just for this).
    pdf = library.local_pdf(meta["key"])
    if pdf and desktop()["prefs"].get("downloadAssociatedFiles", True):
        r = _connector("/connector/saveAttachment", content=pdf.read_bytes(), timeout=120, headers={
            "Content-Type": "application/pdf",
            "X-Metadata": json.dumps({"sessionID": session, "parentItemID": "studio", "title": "Full Text PDF",
                                      "url": meta["pdf_url"] or meta["url"]})})
        if r.status_code not in (200, 201):
            warnings.append("PDF 没能附上")
    return _await_key(meta["ids"]), warnings


def _push_web(meta: dict[str, Any], item: dict[str, Any]) -> tuple[str, list[str]]:
    creds = web_creds()
    if not creds:
        raise ZoteroError("没有可用的 Zotero 网络密钥")
    key, uid = creds
    coll, warn = _target_collection()
    body = {**item, "collections": [coll["key"]] if coll else []}
    r = _web("POST", f"/users/{uid}/items", key, json=[body], headers={"Zotero-Write-Token": secrets.token_hex(16)})
    data = r.json()
    zkey = ((data.get("successful") or {}).get("0") or {}).get("key") or (data.get("success") or {}).get("0")
    if not zkey:
        failed = ((data.get("failed") or {}).get("0") or {}).get("message", "")
        raise ZoteroError(f"Zotero 没有接受这条{('：' + failed) if failed else ''}")
    warnings = [warn] if warn else []
    if meta["pdf_url"]:  # a link to the PDF, not a copy: uses no Zotero storage
        try:
            _web("POST", f"/users/{uid}/items", key, json=[{
                "itemType": "attachment", "linkMode": "linked_url", "parentItem": zkey, "title": "PDF",
                "url": meta["pdf_url"], "contentType": "application/pdf"}])
        except ZoteroError:
            warnings.append("PDF 链接没能附上")
    return zkey, warnings


# ---------- status, push ----------


def _with_links(out: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    key = out.get("key")
    if key:
        out["link"] = select_link(key)
    note = meta["note"] or (library.arxiv_index().get(meta["arxiv"]) if meta["arxiv"] else None)
    if key and note:
        try:
            if not library._parse_note(library.note_path(note))["zotero_key"]:
                out["linkback"] = link_preview(note, key)
        except FileNotFoundError:
            pass
    return out


def transports() -> dict[str, bool]:
    return {"desktop": desktop()["up"], "web": bool(secret("ZOTERO_API_KEY", "zotero"))}


def status(key: str) -> dict[str, Any]:
    """Whether a paper is in Zotero: linked (note has the link), in_library (found), or absent."""
    meta = paper_meta(key)
    base = {"title": meta["title"], "note": meta["note"], **transports()}
    if meta["zotero_key"]:
        return {**base, "state": "linked", "key": meta["zotero_key"], "link": select_link(meta["zotero_key"])}
    found = find_local(meta["ids"])
    return _with_links({**base, "state": "in_library" if found else "absent", "key": found}, meta)


def push(key: str) -> dict[str, Any]:
    """Add the paper to Zotero unless it is already there. Never writes the note (see write_link)."""
    with _LOCK:
        meta = paper_meta(key)
        if meta["zotero_key"]:
            return {"state": "linked", "key": meta["zotero_key"], "link": select_link(meta["zotero_key"]), "note": meta["note"]}
        if not meta["ids"]:
            raise ZoteroError("这篇没有 DOI、arXiv 号或链接，没法在 Zotero 里查重，先在笔记里补上来源")
        found = find_local(meta["ids"]) or _remote_find(meta["ids"], meta)
        if found:
            if not find_local(meta["ids"]):
                _record(key, found, meta["ids"], "found")
            return _with_links({"state": "exists", "key": found, "note": meta["note"]}, meta)
        item = build_item(meta)
        if desktop()["up"]:
            via = "desktop"
            zkey, warnings = _push_connector(meta, item)
        elif web_creds():
            via = "web"
            zkey, warnings = _push_web(meta, item)
        else:
            raise ZoteroError("Zotero 桌面端没在运行，也没有配置网络密钥")
        if zkey:
            _record(key, zkey, meta["ids"], via)
        else:
            warnings.append("已存进 Zotero，但还没读到条目编号；稍后再点一次可以写回链接")
        return _with_links({"state": "added", "key": zkey, "via": via, "warnings": warnings, "note": meta["note"]}, meta)


# ---------- writing the link into the note (only after an explicit confirm) ----------

_FM_OPEN = re.compile(r"---[ \t]*(\r?\n)")
_FM_CLOSE = re.compile(r"^---[ \t]*\r?$", re.M)
_ZOTERO_LINE = re.compile(r"^zotero:[^\r\n]*", re.M)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _linked_text(src: str, line: str) -> tuple[str, str | None]:
    """The note with the zotero line added as the first front-matter line, and any line it replaced."""
    m = _FM_OPEN.match(src)
    close = _FM_CLOSE.search(src, m.end()) if m else None
    if not (m and close):
        nl = "\r\n" if "\r\n" in src else "\n"
        return f"---{nl}{line}{nl}---{nl}" + src, None
    start, nl = m.end(), m.group(1)
    old = _ZOTERO_LINE.search(src, start, close.start())
    if old:
        return src[:old.start()] + line + src[old.end():], old.group(0).rstrip()
    return src[:start] + line + nl + src[start:], None


def link_preview(stem: str, zkey: str) -> dict[str, Any]:
    """The exact change write_link would make, for the user to confirm."""
    path = library.note_path(stem)
    src = _read_note(path)
    line = f"zotero: {select_link(zkey)}"
    _, old = _linked_text(src, line)
    return {"stem": stem, "path": str(path.relative_to(VAULT_DIR)), "line": line, "replaces": old,
            "key": zkey, "base": _digest(src)}


def write_link(stem: str, zkey: str, base: str) -> dict[str, Any]:
    """Write the confirmed line. `base` is the digest the preview was made from: if the note changed
    since, nothing is written and the caller has to show the change again."""
    if not re.fullmatch(r"[A-Z0-9]{8}", zkey or ""):
        raise ValueError("Zotero 条目编号不对")
    path = library.note_path(stem)
    line = f"zotero: {select_link(zkey)}"
    with _note_locked():
        src = _read_note(path)
        if _digest(src) != base:
            raise LookupError("笔记刚刚被改过，请重新确认要写入的那一行")
        new, old = _linked_text(src, line)
        if old and old.split(":", 1)[1].strip() and old != line:  # an empty "zotero:" placeholder gets filled
            raise LookupError("笔记里已经有另一个 Zotero 链接，没有改动")
        if new != src:
            _write_note(path, new)
    return {"stem": stem, "line": line, "written": new != src}


def pending_links() -> list[dict[str, Any]]:
    """Papers this Mac put into Zotero whose note doesn't carry the link yet."""
    out, seen = [], set()
    by_arxiv = library.arxiv_index()
    for e in reversed(_pushed()):
        paper = e.get("paper", "")
        aid = library.arxiv_key(paper)
        stem = by_arxiv.get(aid) if aid else paper
        if not stem or stem in seen:
            continue
        seen.add(stem)
        try:
            info = library._parse_note(library.note_path(stem))
        except FileNotFoundError:
            continue
        if not info["zotero_key"] and e["key"] not in snapshot()["deleted"]:
            out.append({**link_preview(stem, e["key"]), "title": info["title"]})
    return out


# ---------- auto mode ----------


def _qualifies(mode: str, status_: str | None) -> bool:
    if status_ is None:
        return False
    return status_ not in ("dismissed", "snoozed") if mode == "all" else status_ in ("queued", "reading", "done")


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def auto_status() -> dict[str, Any]:
    state = _load_json(AUTO_FILE, {})
    return {"mode": load_settings().get("zotero_auto", "off"), "recent": state.get("recent", [])[-10:][::-1],
            "retry": len(state.get("retry", []))}


def auto_tick(force: bool = False) -> list[dict[str, Any]]:
    """One pass of auto mode: push papers that newly qualify since the last pass.

    The first pass only records what is there, so turning auto mode on never back-fills the whole list.
    State is kept even while auto mode is off, for the same reason.
    """
    stamp = [_mtime(READING_LIST), _mtime(PAPERS_DIR)]
    mode = load_settings().get("zotero_auto", "off")
    with _AUTO_LOCK:
        state = _load_json(AUTO_FILE, {})
        due_retry = mode != "off" and state.get("retry") and time.time() - state.get("retried", 0) > 300
        if not force and state.get("stamp") == stamp and not due_retry:
            return []
        items = {i["id"]: [reading.key_of(i), i["status"]] for i in reading._load_raw()}
        notes = sorted(p.stem for p in PAPERS_DIR.glob("*.md"))
        todo: list[str] = []
        if "items" in state and mode in ("triage", "all"):
            prev = state["items"]
            todo = [k for iid, (k, st) in items.items() if _qualifies(mode, st) and not _qualifies(mode, (prev.get(iid) or [None, None])[1])]
            todo += sorted(set(notes) - set(state.get("notes", [])))
        if due_retry:
            todo += [r["paper"] for r in state["retry"]]
            state["retried"] = time.time()
        retry = {r["paper"]: r for r in state.get("retry", [])}
        state.update(stamp=stamp, items=items, notes=notes)
        _save_json(AUTO_FILE, state)
    results = []
    for key in dict.fromkeys(todo):
        try:
            r = push(key)
            results.append({"paper": key, "state": r["state"], "key": r.get("key"), "at": _now()})
            retry.pop(key, None)
        except (ZoteroError, FileNotFoundError) as e:
            n = retry.get(key, {}).get("tries", 0) + 1
            if n < 5:
                retry[key] = {"paper": key, "tries": n}
            else:
                retry.pop(key, None)
            results.append({"paper": key, "state": "error", "error": str(e), "at": _now()})
    if results:
        with _AUTO_LOCK:
            state = _load_json(AUTO_FILE, {})
            state["recent"] = (state.get("recent", []) + results)[-30:]
            state["retry"] = list(retry.values())
            if any(r["state"] == "error" for r in results):
                state["retried"] = time.time()  # next attempt in 5 minutes
            _save_json(AUTO_FILE, state)
    return results


_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    while not _stop.wait(5):
        try:
            auto_tick()
        except Exception as e:  # noqa: BLE001 - keep watching; the next change retries
            print(f"[zotero auto] {e.__class__.__name__}: {e}")


def start_auto() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="zotero-auto", daemon=True)
    _thread.start()


def stop_auto() -> None:
    _stop.set()


def secrets_hint() -> str:
    f = SECRETS_DIR / "zotero.env"
    home = str(Path.home())
    return str(f).replace(home, "~", 1) if str(f).startswith(home) else str(f)
