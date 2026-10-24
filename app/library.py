"""Paper library: vault paper notes + Zotero links + local PDF resolution.

A paper is identified by its note stem ("Attention 2017"), matching
<papers dir>/<stem>.md. PDFs are looked up in the vault first, then in
the local cache, and otherwise downloaded once from the Zotero attachment URL
or the note's arXiv / source link into the cache (never into the vault).
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import threading
from urllib.parse import quote
from functools import lru_cache
from pathlib import Path
from typing import Any

import pymupdf as fitz
import httpx
import yaml

from . import sources
from .config import (
    DATA_DIR,
    VAULT_NAME,
    PAPERS_DIR,
    PDF_CACHE_DIR,
    READING_QUEUE,
    TEXT_DIR,
    VAULT_DIR,
    VAULT_PDFS_DIR,
    ZOTERO_DIR,
)

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.S)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def _parse_note(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm: dict[str, Any] = {}
    m = _FM_RE.match(text)
    if m:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
    title_m = re.search(r"^- Title:\s*(.+)$", text, re.M)
    zkey = ""
    if isinstance(fm.get("zotero"), str):
        zkey = fm["zotero"].rstrip("/").rsplit("/", 1)[-1]
    tags = fm.get("tags") or []
    return {
        "stem": path.stem,
        "title": title_m.group(1).strip() if title_m else path.stem,
        "year": fm.get("year"),
        "status": fm.get("status") or "",
        "source": fm.get("source") or "",
        "arxiv": str(fm.get("arxiv") or ""),
        "doi": sources.norm_doi(str(fm["doi"])) if fm.get("doi") else "",
        "zotero_key": zkey,
        "tags": [t for t in tags if isinstance(t, str) and t != "paper"],
        "research_line": _WIKILINK_RE.findall(str(fm.get("research_line") or "")),
        "mtime": path.stat().st_mtime,
    }


def reading_queue() -> list[dict[str, str]]:
    """Wikilinked items of Reading Queue.md in order, with their section heading."""
    if not READING_QUEUE.exists():
        return []
    out, seen, section = [], set(), ""
    for line in READING_QUEUE.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        for target in _WIKILINK_RE.findall(line):
            target = target.strip()
            if target not in seen:
                seen.add(target)
                out.append({"stem": target, "section": section})
    return out


def list_papers() -> list[dict[str, Any]]:
    queue = {q["stem"]: i for i, q in enumerate(reading_queue())}
    papers = []
    for p in sorted(PAPERS_DIR.glob("*.md")):
        info = _parse_note(p)
        info["queue_rank"] = queue.get(info["stem"])
        info["pdf"] = "local" if local_pdf(info["stem"]) else ("remote" if pdf_urls(info) else "none")
        papers.append(info)
    return papers


_ARXIV_KEY = re.compile(r"^arxiv:(\d{4}\.\d{4,5})$")


def arxiv_key(key: str) -> str | None:
    """The arXiv id when the key is "arxiv:<id>" (a paper without a note yet)."""
    m = _ARXIV_KEY.match(key)
    return m.group(1) if m else None


def ext_key(key: str) -> tuple[str, str] | None:
    """("doi" | "openreview" | "acl", id) when the key is doi:<doi>, openreview:<id> or acl:<id>
    (a paper without a note or an arXiv id, e.g. one captured from a DOI or an OpenReview link)."""
    return sources.ext_parts(key)


def is_external(key: str) -> bool:
    """A paper that has no note yet: its key is arxiv:<id> or one of the external keys."""
    return bool(arxiv_key(key) or ext_key(key))


def safe_name(key: str) -> str:
    return key.replace(":", "_").replace("/", "_").replace("|", "_")


def validate(key: str) -> None:
    if not is_external(key):
        note_path(key)


@lru_cache(maxsize=1)
def _arxiv_index(_stamp: float) -> dict[str, str]:
    out = {}
    for p in PAPERS_DIR.glob("*.md"):
        info = _parse_note(p)
        ids = [info["arxiv"]] + re.findall(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", info["source"])
        for i in ids:
            if i:
                out.setdefault(re.sub(r"v\d+$", "", i), info["stem"])
    return out


def arxiv_index() -> dict[str, str]:
    """arXiv id -> paper note stem."""
    return _arxiv_index(PAPERS_DIR.stat().st_mtime)


@lru_cache(maxsize=1)
def _ext_index(_stamp: float) -> dict[str, str]:
    out = {}
    for p in PAPERS_DIR.glob("*.md"):
        info = _parse_note(p)
        keys = [sources.ext_key("doi", info["doi"])] if info["doi"] else []
        keys += [k for k in (sources.ext_from_url(info["source"]),) if k]
        for k in keys:
            if sources.ext_parts(k):
                out.setdefault(k, info["stem"])
    return out


def ext_index() -> dict[str, str]:
    """doi:/openreview:/acl: key -> paper note stem, from a note's `doi` and its `source` link."""
    return _ext_index(PAPERS_DIR.stat().st_mtime)


def get_paper(stem: str) -> dict[str, Any]:
    if is_external(stem):
        return {"stem": stem, "title": stem, "arxiv": arxiv_key(stem), "year": None, "status": "", "source": "",
                "zotero_key": "", "tags": [], "research_line": [], "note_rel": None, "note_markdown": "",
                "obsidian_url": None, "external": True, "pdf": "local" if local_pdf(stem) else "remote"}
    path = note_path(stem)
    info = _parse_note(path)
    info["note_rel"] = str(path.relative_to(VAULT_DIR))
    info["note_markdown"] = path.read_text(encoding="utf-8", errors="replace")
    info["obsidian_url"] = f"obsidian://open?vault={quote(VAULT_NAME)}&file={quote(info['note_rel'])}"
    pdf = local_pdf(stem)
    info["pdf"] = "local" if pdf else ("remote" if pdf_urls(info) else "none")
    return info


def note_path(stem: str) -> Path:
    """Resolve a stem to its note, refusing anything outside the Papers folder."""
    p = (PAPERS_DIR / f"{stem}.md").resolve()
    if p.parent != PAPERS_DIR.resolve() or not p.exists():
        raise FileNotFoundError(stem)
    return p


# ---------- Zotero (read-only snapshot) ----------

_ZOTERO_SNAPSHOT = DATA_DIR / "zotero_snapshot.sqlite"


_ZOTERO_LOCK = threading.Lock()


def _zotero_db() -> sqlite3.Connection | None:
    src = ZOTERO_DIR / "zotero.sqlite"
    if not src.exists():
        return None
    # Zotero keeps the DB locked while running; read a copy refreshed on change.
    # Copy under a lock into temp names, then swap in, so concurrent requests never
    # open a half-copied snapshot.
    with _ZOTERO_LOCK:
        newest = max(f.stat().st_mtime for f in ZOTERO_DIR.glob("zotero.sqlite*") if f.is_file())
        if not _ZOTERO_SNAPSHOT.exists() or _ZOTERO_SNAPSHOT.stat().st_mtime < newest:
            for suffix in ("", "-wal", "-shm"):
                s = ZOTERO_DIR / f"zotero.sqlite{suffix}"
                d = Path(f"{_ZOTERO_SNAPSHOT}{suffix}")
                if s.exists():
                    tmp = Path(f"{d}.tmp")
                    shutil.copy2(s, tmp)
                    os.replace(tmp, d)
                elif d.exists():
                    d.unlink()
        return sqlite3.connect(_ZOTERO_SNAPSHOT)


@lru_cache(maxsize=1)
def _zotero_pdf_urls(_stamp: float) -> dict[str, list[str]]:
    con = _zotero_db()
    if con is None:
        return {}
    rows = con.execute(
        """
        SELECT parent.key, v.value
        FROM itemAttachments a
        JOIN items parent ON parent.itemID = a.parentItemID
        JOIN itemData d ON d.itemID = a.itemID
        JOIN fields f ON f.fieldID = d.fieldID AND f.fieldName = 'url'
        JOIN itemDataValues v ON v.valueID = d.valueID
        WHERE a.contentType = 'application/pdf'
        """
    ).fetchall()
    con.close()
    out: dict[str, list[str]] = {}
    for key, url in rows:
        out.setdefault(key, []).append(url)
    return out


def _zotero_stamp() -> float:
    files = list(ZOTERO_DIR.glob("zotero.sqlite*"))
    return max((f.stat().st_mtime for f in files), default=0.0)


def _doi_pdf_urls(doi: str) -> list[str]:
    """Links a DOI gives away without a lookup: the arXiv and ACL Anthology copies, the ACM page."""
    if m := re.match(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", doi, re.I):
        return [f"https://arxiv.org/pdf/{m.group(1)}"]
    if m := re.match(r"10\.18653/v1/(.+)$", doi):
        return [f"https://aclanthology.org/{m.group(1)}.pdf"]
    if doi.startswith("10.1145/"):
        return [f"https://dl.acm.org/doi/pdf/{doi}"]
    return []


def pdf_urls(info: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    if info.get("zotero_key"):
        urls += _zotero_pdf_urls(_zotero_stamp()).get(info["zotero_key"], [])
    if info.get("arxiv"):
        urls.append(f"https://arxiv.org/pdf/{info['arxiv']}")
    if info.get("openreview"):
        urls.append(f"https://openreview.net/pdf?id={info['openreview']}")
    if info.get("acl"):
        urls.append(f"https://aclanthology.org/{info['acl']}.pdf")
    if info.get("doi"):
        urls += _doi_pdf_urls(info["doi"])
    src = info.get("source") or ""
    if m := re.search(r"arxiv\.org/(?:abs|pdf)/([\w.]+?)(?:v\d+)?(?:\.pdf)?$", src):
        urls.append(f"https://arxiv.org/pdf/{m.group(1)}")
    elif m := re.search(r"openreview\.net/(?:forum|pdf)\?id=([\w-]+)", src):
        urls.append(f"https://openreview.net/pdf?id={m.group(1)}")
    elif m := re.search(r"aclanthology\.org/([\w.-]+?)/?$", src):
        urls.append(f"https://aclanthology.org/{m.group(1).removesuffix('.pdf')}.pdf")
    elif m := re.search(r"dl\.acm\.org/doi/(?:abs/|pdf/)?(10\.[^?#]+)", src):
        urls.append(f"https://dl.acm.org/doi/pdf/{m.group(1)}")
    return list(dict.fromkeys(urls))


# ---------- PDFs ----------

# PyMuPDF is not thread-safe: every open / render / text extraction, from any module, holds this lock.
PDF_LOCK = threading.RLock()


def local_pdf(stem: str) -> Path | None:
    for p in (VAULT_PDFS_DIR / f"{stem}.pdf", PDF_CACHE_DIR / f"{safe_name(stem)}.pdf"):
        if p.exists():
            return p
    return None


def ensure_pdf(stem: str) -> Path:
    """Return a local PDF for the paper, downloading it into the cache if needed."""
    if p := local_pdf(stem):
        return p
    aid, ext = arxiv_key(stem), ext_key(stem)
    info = {"arxiv": aid} if aid else {ext[0]: ext[1]} if ext else _parse_note(note_path(stem))
    errors = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh) PhD-Studio/0.1"}
    urls = pdf_urls(info)
    if info.get("doi"):  # last resort, a lookup: an open-access copy OpenAlex knows about
        urls += [u for u in sources.doi_pdf_urls(info["doi"]) if u not in urls]
    for url in urls:
        try:
            r = httpx.get(url, headers=headers, follow_redirects=True, timeout=60)
            if r.status_code == 200 and r.content[:5] == b"%PDF-":
                dest = PDF_CACHE_DIR / f"{safe_name(stem)}.pdf"
                dest.write_bytes(r.content)
                return dest
            errors.append(f"{url}: HTTP {r.status_code}")
        except httpx.HTTPError as e:
            errors.append(f"{url}: {e.__class__.__name__}")
    raise FileNotFoundError("; ".join(errors) or "no PDF link in note or Zotero")


def text_file(stem: str) -> Path:
    """Plain-text dump of the PDF with page markers, for Claude to Read on demand."""
    pdf = ensure_pdf(stem)
    out = TEXT_DIR / f"{safe_name(stem)}.md"
    if out.exists() and out.stat().st_mtime >= pdf.stat().st_mtime:
        return out
    with PDF_LOCK, fitz.open(pdf) as doc:
        parts = [f"# {stem} (full text, {len(doc)} pages)\n"]
        for i, page in enumerate(doc, 1):
            parts.append(f"\n<!-- page {i} -->\n## Page {i}\n\n{page.get_text().strip()}\n")
    out.write_text("".join(parts), encoding="utf-8")
    return out


def page_text(stem: str, page: int) -> str:
    pdf = ensure_pdf(stem)  # may download; not under the lock
    with PDF_LOCK, fitz.open(pdf) as doc:
        if not 1 <= page <= len(doc):
            return ""
        return doc[page - 1].get_text().strip()
