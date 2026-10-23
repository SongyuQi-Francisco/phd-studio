"""Paper library: vault paper notes + Zotero links + local PDF resolution.

A paper is identified by its note stem ("Attention 2017"), matching
<papers dir>/<stem>.md. PDFs are looked up in the vault first, then in
the local cache, and otherwise downloaded once from the Zotero attachment URL
or the note's arXiv / source link into the cache (never into the vault).
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from urllib.parse import quote
from functools import lru_cache
from pathlib import Path
from typing import Any

import pymupdf as fitz
import httpx
import yaml

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


def get_paper(stem: str) -> dict[str, Any]:
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


def _zotero_db() -> sqlite3.Connection | None:
    src = ZOTERO_DIR / "zotero.sqlite"
    if not src.exists():
        return None
    # Zotero keeps the DB locked while running; read a copy refreshed on change.
    newest = max(f.stat().st_mtime for f in ZOTERO_DIR.glob("zotero.sqlite*") if f.is_file())
    if not _ZOTERO_SNAPSHOT.exists() or _ZOTERO_SNAPSHOT.stat().st_mtime < newest:
        for suffix in ("", "-wal", "-shm"):
            s = ZOTERO_DIR / f"zotero.sqlite{suffix}"
            d = Path(f"{_ZOTERO_SNAPSHOT}{suffix}")
            if s.exists():
                shutil.copy2(s, d)
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


def pdf_urls(info: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    if info.get("zotero_key"):
        urls += _zotero_pdf_urls(_zotero_stamp()).get(info["zotero_key"], [])
    if info.get("arxiv"):
        urls.append(f"https://arxiv.org/pdf/{info['arxiv']}")
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


def local_pdf(stem: str) -> Path | None:
    for p in (VAULT_PDFS_DIR / f"{stem}.pdf", PDF_CACHE_DIR / f"{stem}.pdf"):
        if p.exists():
            return p
    return None


def ensure_pdf(stem: str) -> Path:
    """Return a local PDF for the paper, downloading it into the cache if needed."""
    if p := local_pdf(stem):
        return p
    info = _parse_note(note_path(stem))
    errors = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh) PhD-Studio/0.1"}
    for url in pdf_urls(info):
        try:
            r = httpx.get(url, headers=headers, follow_redirects=True, timeout=60)
            if r.status_code == 200 and r.content[:5] == b"%PDF-":
                dest = PDF_CACHE_DIR / f"{stem}.pdf"
                dest.write_bytes(r.content)
                return dest
            errors.append(f"{url}: HTTP {r.status_code}")
        except httpx.HTTPError as e:
            errors.append(f"{url}: {e.__class__.__name__}")
    raise FileNotFoundError("; ".join(errors) or "no PDF link in note or Zotero")


def text_file(stem: str) -> Path:
    """Plain-text dump of the PDF with page markers, for Claude to Read on demand."""
    pdf = ensure_pdf(stem)
    out = TEXT_DIR / f"{stem}.md"
    if out.exists() and out.stat().st_mtime >= pdf.stat().st_mtime:
        return out
    with fitz.open(pdf) as doc:
        parts = [f"# {stem} (full text, {len(doc)} pages)\n"]
        for i, page in enumerate(doc, 1):
            parts.append(f"\n<!-- page {i} -->\n## Page {i}\n\n{page.get_text().strip()}\n")
    out.write_text("".join(parts), encoding="utf-8")
    return out


def page_text(stem: str, page: int) -> str:
    with fitz.open(ensure_pdf(stem)) as doc:
        if not 1 <= page <= len(doc):
            return ""
        return doc[page - 1].get_text().strip()
