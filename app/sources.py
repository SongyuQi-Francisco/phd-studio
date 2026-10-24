"""Paper sources beyond arXiv, for the 追踪 page's capture box and its followed feed.

- Capture: what the box is given is classified (OpenReview forum link, DOI link or bare DOI, ACL
  Anthology link or id, arXiv link or id) and read into one normalized candidate. Anything else
  that is not a URL is a title: it is searched on arXiv, Crossref and OpenAlex and the best
  matches are listed to pick from.
- Feed: OpenReview submissions (full-text search) and ACL Anthology papers (the proceedings
  volumes of the recent *CL events, cached on disk) that match a followed keyword or person.

Every source is its own try: one that is down, rate limited or asks for a human check never
takes the others with it; the caller shows a small status per source. Requests are paced per
host and cached under DATA_DIR/sources; with the network gone the last cached copy is served.

This module imports neither reading, library nor tracking: they import it.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote

import httpx

from .config import DATA_DIR, secret

UA = {"User-Agent": "PhD-Studio/0.1 (local research workbench)"}
ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF = "https://api.crossref.org"
OPENALEX = "https://api.openalex.org"
OPENREVIEW = "https://api2.openreview.net"
ANTHOLOGY = "https://aclanthology.org"
CACHE = DATA_DIR / "sources"
DAY = 86400


class SourceError(Exception):
    """A source could not answer; the message is shown to the user."""


class NotFound(SourceError):
    """The source answered, and the paper is not there."""


class Pace:
    """Request starts at least `gap` seconds apart; `with` holds the slot for the whole request."""

    def __init__(self, gap: float):
        self.gap, self.lock, self.last = gap, threading.Lock(), 0.0

    def __enter__(self) -> None:
        self.lock.acquire()
        wait = self.gap - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)

    def __exit__(self, *exc: object) -> None:
        self.last = time.time()
        self.lock.release()


ARXIV_PACE = Pace(3.0)  # arXiv asks for one request every 3 s, from everything in this app
CROSSREF_PACE = Pace(0.5)
OPENALEX_PACE = Pace(0.2)
OPENREVIEW_PACE = Pace(1.0)
ANTHOLOGY_PACE = Pace(2.0)


def _get(url: str, what: str, *, params: dict[str, Any] | None = None, pace: Pace | None = None,
         headers: dict[str, str] | None = None, timeout: float = 30) -> httpx.Response:
    """One GET. Connection problems and rate limits become SourceError (no URL in the message:
    it can carry an API key)."""
    try:
        if pace:
            with pace:
                r = httpx.get(url, params=params, headers={**UA, **(headers or {})}, timeout=timeout, follow_redirects=True)
        else:
            r = httpx.get(url, params=params, headers={**UA, **(headers or {})}, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as e:
        raise SourceError(f"{what} 连接失败（{e.__class__.__name__}）") from None
    if r.status_code == 429:
        raise SourceError(f"{what} 请求太频繁或今日额度用完，稍后再试")
    return r


def _check(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code == 404:
        raise NotFound(f"{what} 里没有这条")
    if r.status_code >= 400:
        raise SourceError(f"{what} 返回 HTTP {r.status_code}")
    return r


def _json(r: httpx.Response, what: str) -> Any:
    try:
        return r.json()
    except ValueError:
        raise SourceError(f"{what} 返回的不是 JSON") from None


# ---------- cache ----------

def _cache_file(name: str) -> Path:
    return CACHE / (hashlib.sha1(name.encode()).hexdigest()[:24] + ".json")


def cached(name: str, ttl: float, load: Callable[[], Any]) -> tuple[Any, bool]:
    """`load()` once per `ttl` seconds. When it fails and an older copy exists, that copy is
    served and marked stale (second value); without one the error goes up."""
    f = _cache_file(name)
    old = None
    try:
        old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except (OSError, ValueError):
        old = None
    if old and time.time() - old.get("t", 0) < ttl:
        return old["data"], False
    try:
        data = load()
    except Exception:
        if old:
            return old["data"], True
        raise
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"t": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    return data, False


# ---------- identifiers ----------

_ARXIV_ID = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)")
_OPENREVIEW_URL = re.compile(r"openreview\.net/(?:forum|pdf|revisions)\?(?:[^\s#]*&)?id=([\w-]+)", re.I)
_ACL_ID = r"(?:\d{4}\.(?=[a-z0-9-]*[a-z])[a-z0-9]+(?:-[a-z0-9]+)*\.\d+|[A-Z]\d{2}-\d{4})"
_ACL_URL = re.compile(rf"aclanthology\.org/({_ACL_ID})(?=/|\.pdf|\.bib|\.xml|[?#\s]|$)", re.I)
_ACL_BARE = re.compile(rf"^(?:acl[: ]\s*)?({_ACL_ID})$", re.I)
_DOI = re.compile(r"(?<![\w.])(10\.\d{4,9}/[^\s\"'?#]+)", re.I)

# Key of a reading item without a paper note or arXiv id: <scheme>:<id>. Keys travel in URL paths
# (/api/papers/<key>), where a "/" would split the route, so the "/" of a DOI is written "|".
_EXT = {
    "doi": re.compile(r"^doi:(10\.\d{4,9}\|[\w.\-;()|:+]+)$"),
    "openreview": re.compile(r"^openreview:([\w-]{4,40})$"),
    "acl": re.compile(rf"^acl:({_ACL_ID})$"),
}


def ext_parts(key: str | None) -> tuple[str, str] | None:
    """("doi" | "openreview" | "acl", id) for a valid external key, else None."""
    for scheme, rx in _EXT.items():
        if m := rx.fullmatch(key or ""):
            return scheme, m.group(1).replace("|", "/") if scheme == "doi" else m.group(1)
    return None


def norm_doi(doi: str) -> str:
    doi = unquote(doi).strip().lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    doi = doi.rstrip(".,;:>")
    while doi.endswith(")") and doi.count("(") < doi.count(")"):
        doi = doi[:-1]
    return doi


def ext_key(scheme: str, ident: str) -> str:
    return f"doi:{norm_doi(ident).replace('/', '|')}" if scheme == "doi" else f"{scheme}:{ident}"


def doi_ok(doi: str) -> bool:
    return "|" not in doi and ext_parts(ext_key("doi", doi)) is not None


def ext_from_url(url: str) -> str | None:
    """The external key of a link found in a paper note's `source` (None when it is not one)."""
    url = url or ""
    if m := _OPENREVIEW_URL.search(url):
        return ext_key("openreview", m.group(1))
    if m := _ACL_URL.search(url):
        return ext_key("acl", m.group(1))
    if m := _DOI.search(unquote(url)):
        doi = norm_doi(m.group(1))
        return ext_key("doi", doi) if doi_ok(doi) else None
    return None


def _acl_case(acl_id: str) -> str:
    """Old ids are upper case (P19-1001), new ones lower case (2024.acl-long.1)."""
    return acl_id.upper() if re.fullmatch(r"[A-Za-z]\d{2}-\d{4}", acl_id) else acl_id.lower()


def classify(text: str) -> tuple[str, str] | None:
    """What a pasted string names: ("openreview" | "acl" | "doi" | "arxiv", id), or None for a title.

    The order matters: a DOI such as 10.1145/3626772.3657833 holds digits that look like an arXiv id."""
    t = (text or "").strip()
    if m := _OPENREVIEW_URL.search(t):
        return "openreview", m.group(1)
    if m := _ACL_URL.search(t) or _ACL_BARE.match(t):
        return "acl", _acl_case(m.group(1))
    if m := _DOI.search(unquote(t) if "%" in t else t):
        doi = norm_doi(m.group(1))
        if m2 := re.match(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", doi):
            return "arxiv", m2.group(1)
        if m2 := re.match(rf"10\.18653/v1/({_ACL_ID})$", doi, re.I):
            return "acl", _acl_case(m2.group(1))
        if doi_ok(doi):
            return "doi", doi
    if m := _ARXIV_ID.search(t):
        return "arxiv", m.group(1)
    return None


def looks_like_url(text: str) -> bool:
    return bool(re.match(r"\s*(?:https?://|www\.)", text or "", re.I))


# ---------- candidates ----------

def _http(url: Any) -> str:
    """A link from a remote answer is only ever http(s): it ends up in an href."""
    return str(url) if re.match(r"https?://", str(url or ""), re.I) else ""


def candidate(**kw: Any) -> dict[str, Any]:
    c = {"title": "", "authors": [], "year": None, "venue": "", "abstract": "", "url": "", "arxiv": None,
         "doi": None, "openreview": None, "acl": None, "sources": []}
    c.update({k: v for k, v in kw.items() if v not in (None, "")})
    c["url"] = _http(c["url"])
    c["key"] = key_of(c)
    return c


def key_of(c: dict[str, Any]) -> str:
    """arXiv wins (free PDF, the existing dedupe), then OpenReview and ACL, then the bare DOI."""
    if c.get("arxiv"):
        return f"arxiv:{c['arxiv']}"
    if c.get("openreview"):
        return ext_key("openreview", c["openreview"])
    if c.get("acl"):
        return ext_key("acl", c["acl"])
    if c.get("doi"):
        return ext_key("doi", c["doi"])
    return ""


def _clean(s: Any) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", str(s or "")).split())


def _year(parts: Any) -> int | None:
    try:
        return int(parts["date-parts"][0][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


# ---------- arXiv ----------

def arxiv_query(q: str, max_results: int, sort_by: str = "submittedDate") -> list[dict[str, Any]]:
    """arXiv API search, newest first by default (or by relevance); one request per 3 s app-wide."""
    with ARXIV_PACE:
        r = httpx.get(ARXIV_API, headers=UA, timeout=30, params={"search_query": q, "sortBy": sort_by,
                                                                 "sortOrder": "descending", "max_results": max_results})
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(r.text).findall("a:entry", ns):
        aid = (e.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1]
        out.append({"arxiv": re.sub(r"v\d+$", "", aid), "title": " ".join((e.findtext("a:title", "", ns) or "").split()),
                    "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)][:6],
                    "categories": [c.get("term") for c in e.findall("a:category", ns)],
                    "published": (e.findtext("a:published", "", ns) or "")[:10],
                    "summary": " ".join((e.findtext("a:summary", "", ns) or "").split())[:500]})
    return out


def _arxiv_candidate(p: dict[str, Any]) -> dict[str, Any]:
    return candidate(title=p["title"], authors=p["authors"], year=int(p["published"][:4]) if p["published"] else None,
                     venue="arXiv", abstract=p["summary"], url=f"https://arxiv.org/abs/{p['arxiv']}", arxiv=p["arxiv"],
                     sources=["arXiv"])


_STOP = {"a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with", "via", "using"}


def search_arxiv(q: str, n: int) -> list[dict[str, Any]]:
    """Titles are matched word by word (all words, any field); when the full set finds nothing
    (a typo, a word the title spells differently), the first few words get a second try."""
    words = [w for w in re.findall(r"[A-Za-z0-9][\w-]*", q) if w.lower() not in _STOP and len(w) > 1][:7]
    for ws in ([words, words[:4]] if len(words) > 4 else [words]):
        if got := arxiv_query(" AND ".join(f"all:{w}" for w in ws), n, "relevance") if ws else []:
            return [_arxiv_candidate(p) for p in got]
    return []


# ---------- Crossref / DOI ----------

def _crossref_candidate(it: dict[str, Any]) -> dict[str, Any]:
    authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "") for a in it.get("author", [])]
    year = _year(it.get("issued")) or _year(it.get("published")) or _year(it.get("created"))
    venue = (it.get("container-title") or [""])[0] or (it.get("event") or {}).get("name", "")
    doi = norm_doi(it.get("DOI", ""))
    return candidate(title=_clean((it.get("title") or [""])[0]), authors=[a for a in authors if a], year=year,
                     venue=_clean(venue), abstract=_clean(it.get("abstract")), url=_http(it.get("URL")) or f"https://doi.org/{doi}",
                     doi=doi, sources=["Crossref"])


def crossref_work(doi: str) -> dict[str, Any]:
    r = _check(_get(f"{CROSSREF}/works/{quote(doi, safe='/')}", "Crossref", pace=CROSSREF_PACE, timeout=20), "Crossref")
    return _crossref_candidate(_json(r, "Crossref")["message"])


def _csl_candidate(d: dict[str, Any], doi: str) -> dict[str, Any]:
    title = d.get("title")
    title = title[0] if isinstance(title, list) and title else title
    venue = d.get("container-title") or d.get("publisher") or ""
    venue = venue[0] if isinstance(venue, list) and venue else venue
    authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("literal", "") for a in d.get("author", [])]
    return candidate(title=_clean(title), authors=[a for a in authors if a], year=_year(d.get("issued")), venue=_clean(venue),
                     abstract=_clean(d.get("abstract")), url=f"https://doi.org/{doi}", doi=doi, sources=["doi.org"])


def doi_negotiated(doi: str) -> dict[str, Any]:
    """doi.org content negotiation (CSL JSON): also covers DOIs Crossref does not hold (DataCite, ...)."""
    r = _check(_get(f"https://doi.org/{quote(doi, safe='/')}", "doi.org", timeout=20,
                    headers={"Accept": "application/vnd.citationstyles.csl+json"}), "doi.org")
    return _csl_candidate(_json(r, "doi.org"), doi)


def doi_meta(doi: str) -> dict[str, Any]:
    """Crossref first, doi.org content negotiation when Crossref does not know the DOI."""
    try:
        cand = cached(f"crossref:{doi}", 30 * DAY, lambda: crossref_work(doi))[0]
    except NotFound:
        cand = cached(f"csl:{doi}", 30 * DAY, lambda: doi_negotiated(doi))[0]
    cand = {**cand, "doi": doi}  # the DOI that was asked for, not whichever form the registry answers with
    try:  # best effort: a preprint copy means a free PDF and the same key as the arXiv item
        cand = {**cand, **{k: v for k, v in openalex_copies(doi).items() if k == "arxiv" and v}}
    except SourceError:
        pass
    cand["key"] = key_of(cand)
    return cand


def search_crossref(q: str, n: int) -> list[dict[str, Any]]:
    r = _check(_get(f"{CROSSREF}/works", "Crossref", pace=CROSSREF_PACE, timeout=20,
                    params={"query.bibliographic": q, "rows": n + 4,
                            "select": "DOI,title,issued,container-title,author,type,URL,abstract,event"}), "Crossref")
    skip = {"peer-review", "component", "dataset", "grant", "report-component"}
    return [_crossref_candidate(it) for it in _json(r, "Crossref")["message"].get("items", [])
            if it.get("type") not in skip and it.get("title")]


# ---------- OpenAlex ----------

_OA_SELECT = "id,doi,title,publication_year,primary_location,authorships,locations,best_oa_location,type"


def _oa(path: str, **params: Any) -> Any:
    if key := secret("OPENALEX_API_KEY", "openalex"):
        params["api_key"] = key
    r = _check(_get(f"{OPENALEX}/{path}", "OpenAlex", pace=OPENALEX_PACE, params=params, timeout=30), "OpenAlex")
    return _json(r, "OpenAlex")


def _arxiv_in(locations: list[dict[str, Any]], doi: str | None) -> str | None:
    for loc in locations or []:
        for u in (loc.get("landing_page_url"), loc.get("pdf_url")):
            if m := re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", u or ""):
                return m.group(1)
    if m := re.match(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", doi or ""):
        return m.group(1)
    return None


def _openalex_candidate(w: dict[str, Any]) -> dict[str, Any]:
    doi = norm_doi(w["doi"]) if w.get("doi") else None
    prim = w.get("primary_location") or {}
    venue = (prim.get("source") or {}).get("display_name") or ""
    arxiv = _arxiv_in(w.get("locations") or [], doi)
    if venue.lower().startswith("arxiv"):
        venue = "arXiv"
    return candidate(title=_clean(w.get("title")), year=w.get("publication_year"), venue=venue, doi=doi, arxiv=arxiv,
                     authors=[(a.get("author") or {}).get("display_name", "") for a in w.get("authorships", [])][:8],
                     url=_http(prim.get("landing_page_url")) or (f"https://doi.org/{doi}" if doi else ""), sources=["OpenAlex"])


def search_openalex(q: str, n: int) -> list[dict[str, Any]]:
    d = _oa("works", search=q, **{"per-page": n + 2, "select": _OA_SELECT})
    return [_openalex_candidate(w) for w in d.get("results", []) if w.get("title")]


def openalex_copies(doi: str) -> dict[str, Any]:
    """Where a DOI's paper can be read for free: its arXiv id (if any) and open PDF links."""
    def load() -> dict[str, Any]:
        w = _oa(f"works/doi:{quote(doi, safe='/')}", select=_OA_SELECT)
        locs = [w.get("best_oa_location"), *(w.get("locations") or [])]
        pdfs = [loc["pdf_url"] for loc in locs if loc and loc.get("pdf_url")]
        return {"arxiv": _arxiv_in(w.get("locations") or [], doi), "pdf": list(dict.fromkeys(pdfs))}
    try:
        return cached(f"oa-doi:{doi}", 30 * DAY, load)[0]
    except NotFound:
        return {"arxiv": None, "pdf": []}


def doi_pdf_urls(doi: str) -> list[str]:
    """Open-access PDF links for a DOI (network; for the reader's last-resort PDF download)."""
    try:
        return openalex_copies(doi)["pdf"]
    except SourceError:
        return []


# ---------- OpenReview ----------

def _or_value(content: dict[str, Any], key: str) -> Any:
    v = content.get(key)
    return v.get("value") if isinstance(v, dict) else v


def _or_authors(content: dict[str, Any]) -> list[str]:
    out = []
    for a in _or_value(content, "authors") or []:
        name = a if isinstance(a, str) else (a.get("fullname") or a.get("name") or "")
        if name:
            out.append(name)
    return out


def _ms_date(ms: int | float | None) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat() if ms else ""


def _or_candidate(n: dict[str, Any]) -> dict[str, Any]:
    c = n.get("content") or {}
    forum = n.get("forum") or n.get("id")
    when = _ms_date(max(n.get("pdate") or 0, n.get("cdate") or 0))
    return candidate(title=_clean(_or_value(c, "title")), authors=_or_authors(c), year=int(when[:4]) if when else None,
                     venue=_clean(_or_value(c, "venue")), abstract=_clean(_or_value(c, "abstract")),
                     url=f"https://openreview.net/forum?id={forum}", openreview=forum, sources=["OpenReview"])


def openreview_meta(forum: str) -> dict[str, Any]:
    r = _get(f"{OPENREVIEW}/notes", "OpenReview", pace=OPENREVIEW_PACE, params={"id": forum}, timeout=20)
    if r.status_code == 403:  # a human check in front of the API: not something to work around
        raise SourceError("OpenReview 要求人机验证，暂时查不了标题")
    notes = _json(_check(r, "OpenReview"), "OpenReview").get("notes") or []
    if not notes:
        raise NotFound("OpenReview 里没有这条")
    return _or_candidate(notes[0])


def openreview_search(term: str, pages: int = 2, per_page: int = 100) -> list[dict[str, Any]]:
    """Notes matching `term`, most relevant first (the search cannot sort or filter by date)."""
    notes: list[dict[str, Any]] = []
    for page in range(pages):
        r = _get(f"{OPENREVIEW}/notes/search", "OpenReview", pace=OPENREVIEW_PACE, timeout=30,
                 params={"term": term, "source": "forum", "limit": per_page, "offset": page * per_page})
        if r.status_code == 403:
            raise SourceError("OpenReview 要求人机验证，暂时查不了")
        got = _json(_check(r, "OpenReview"), "OpenReview").get("notes") or []
        notes += got
        if len(got) < per_page:
            break
    return notes


# ---------- ACL Anthology ----------

_BIB_FIELD = re.compile(r'(\w+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|\{((?:[^{}]|\{[^{}]*\})*)\}|(\w+))', re.S)
_ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "=": "\u0304", ".": "\u0307",
            "c": "\u0327", "v": "\u030c", "u": "\u0306", "H": "\u030b", "k": "\u0328"}
_LATEX = {"o": "ø", "O": "Ø", "ss": "ß", "ae": "æ", "AE": "Æ", "l": "ł", "L": "Ł", "aa": "å", "AA": "Å", "i": "i", "j": "j"}
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def unlatex(s: str) -> str:
    s = re.sub(r"\{?\\([\"'`^~=.]|[cvuHk](?=[\s{]))\s*\{?\\?([A-Za-z])\}?\}?",
               lambda m: unicodedata.normalize("NFC", m.group(2) + _ACCENTS[m.group(1)]), s)
    s = re.sub(r"\{?\\(ss|ae|AE|aa|AA|o|O|l|L)\b\s*\}?", lambda m: _LATEX[m.group(1)], s)
    s = s.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_").replace("--", "–").replace("~", " ")
    s = re.sub(r"\\[A-Za-z]+\s*", "", s)
    return " ".join(re.sub(r"[{}]", "", s).split())


def parse_bib(text: str) -> list[dict[str, str]]:
    """BibTeX entries as {_type, _key, field: value}; macros like `month = jul` come through as words."""
    out = []
    for chunk in re.split(r"\n(?=@)", "\n" + text):
        head = re.match(r"\s*@(\w+)\s*\{\s*([^,\s]+)\s*,", chunk)
        if not head:
            continue
        rec = {"_type": head.group(1).lower(), "_key": head.group(2)}
        for m in _BIB_FIELD.finditer(chunk[head.end():]):
            rec[m.group(1).lower()] = next(v for v in m.groups()[1:] if v is not None)
        out.append(rec)
    return out


def _bib_authors(s: str) -> list[str]:
    names = []
    for a in re.split(r"\s+and\s+", unlatex(s)):
        a = a.strip()
        if "," in a:
            last, first = a.split(",", 1)
            a = f"{first.strip()} {last.strip()}"
        if a:
            names.append(a)
    return names


def acl_meta(acl_id: str) -> dict[str, Any]:
    r = _check(_get(f"{ANTHOLOGY}/{acl_id}.bib", "ACL Anthology", pace=ANTHOLOGY_PACE, timeout=20), "ACL Anthology")
    papers = [b for b in parse_bib(r.text) if b["_type"] != "proceedings"]
    if not papers:
        raise NotFound("ACL Anthology 里这条是论文集目录，不是论文")
    b = papers[0]
    return candidate(title=unlatex(b.get("title", "")), authors=_bib_authors(b.get("author", "")),
                     year=int(b["year"]) if b.get("year", "").isdigit() else None, venue=unlatex(b.get("booktitle", "")),
                     url=f"{ANTHOLOGY}/{acl_id}/", doi=norm_doi(b["doi"]) if b.get("doi") else None, acl=acl_id,
                     sources=["ACL Anthology"])


# Recent *CL events as Anthology volumes: (venue, volume suffixes, label, usual month of the event).
ACL_EVENTS = (
    ("acl", ("acl-long", "acl-short", "findings-acl"), "ACL", 7),
    ("emnlp", ("emnlp-main", "findings-emnlp"), "EMNLP", 11),
    ("naacl", ("naacl-long", "naacl-short", "findings-naacl"), "NAACL", 5),
    ("eacl", ("eacl-long", "eacl-short", "findings-eacl"), "EACL", 4),
    ("coling", ("coling-main",), "COLING", 1),
)
ACL_WINDOW_DAYS = 365
ACL_VOLUME_TTL = 14 * DAY
ACL_MISSING_TTL = 3 * DAY  # a volume that is not published yet (404)
_acl_fill = {"thread": None}
_acl_lock = threading.Lock()


def acl_volumes(today: date | None = None) -> list[tuple[str, str]]:
    """(volume id, label) of the events that can still be inside the window, newest first."""
    today = today or date.today()
    floor = today - timedelta(days=ACL_WINDOW_DAYS + 60)  # slack: the real month is read from the volume
    out = []
    for year in (today.year, today.year - 1):
        for _, suffixes, label, month in ACL_EVENTS:
            if date(year, month, 28) < floor:
                continue
            for s in suffixes:
                tag = " Findings" if s.startswith("findings") else (" 短文" if s.endswith("short") else "")
                out.append((f"{year}.{s}", f"{label} {year}{tag}"))
    return out


def _volume_file(vol: str) -> Path:
    return CACHE / "acl" / f"{vol}.json"


_volumes: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}  # parsed volumes by file stamp: every follow reads all of them


def _read_volume(vol: str) -> dict[str, Any] | None:
    f = _volume_file(vol)
    try:
        st = f.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        if (hit := _volumes.get(str(f))) and hit[0] == stamp:
            return hit[1]
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    _volumes[str(f)] = (stamp, data)
    return data


def _volume_ready(vol: str) -> bool:
    d = _read_volume(vol)
    return bool(d) and time.time() - d.get("t", 0) < (ACL_MISSING_TTL if d.get("missing") else ACL_VOLUME_TTL)


def backoff(fails: int) -> float:
    """Seconds to leave a failing source alone after its n-th failure in a row: 1, 2, 4 ... 30 minutes."""
    return min(60 * 2 ** max(fails - 1, 0), 1800)


def _fail_file(vol: str) -> Path:
    return CACHE / "acl" / f"{vol}.fail.json"


def _read_fail(vol: str) -> dict[str, Any] | None:
    try:
        return json.loads(_fail_file(vol).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _volume_failed(vol: str) -> dict[str, Any] | None:
    """The failure record of a volume that is still being left alone, else None."""
    d = _read_fail(vol)
    return d if d and time.time() - d.get("t", 0) < backoff(d.get("n", 1)) else None


def _record_failure(vol: str, error: str) -> None:
    f = _fail_file(vol)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"t": time.time(), "n": (_read_fail(vol) or {}).get("n", 0) + 1, "error": error}, ensure_ascii=False),
                 encoding="utf-8")


def acl_forget_failures() -> None:
    """A manual refresh tries the failed volumes again at once."""
    for f in (CACHE / "acl").glob("*.fail.json"):
        f.unlink(missing_ok=True)


def fetch_volume(vol: str) -> None:
    """Download one proceedings volume (its .bib) and keep a compact copy of every paper in it."""
    r = _get(f"{ANTHOLOGY}/volumes/{vol}.bib", "ACL Anthology", pace=ANTHOLOGY_PACE, timeout=120)
    f = _volume_file(vol)
    f.parent.mkdir(parents=True, exist_ok=True)
    if r.status_code == 404:
        data: dict[str, Any] = {"t": time.time(), "missing": True, "papers": []}
    else:
        papers = []
        for b in parse_bib(_check(r, "ACL Anthology").text):
            if b["_type"] == "proceedings" or not b.get("title"):
                continue
            m = re.search(r"aclanthology\.org/([^/\s]+)/?$", b.get("url", ""))
            if m and b.get("year", "").isdigit():
                papers.append({"id": m.group(1), "title": unlatex(b["title"]), "authors": _bib_authors(b.get("author", "")),
                               "year": int(b["year"]), "month": _MONTHS.get(b.get("month", "")[:3].lower(), 1)})
        data = {"t": time.time(), "missing": False, "papers": papers}
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    _fail_file(vol).unlink(missing_ok=True)


def acl_fill(vols: list[str]) -> None:
    """Download what is missing. The first failure (the Anthology unreachable, rate limited, a bad
    answer) is recorded for it and every volume still to come, and the run stops: they are left
    alone for a while (backoff) instead of timing out one by one, and the feed shows the reason."""
    for i, v in enumerate(vols):
        if _volume_ready(v) or _volume_failed(v):
            continue
        try:
            fetch_volume(v)
        except Exception as e:  # noqa: BLE001
            msg = str(e) if isinstance(e, SourceError) else f"ACL Anthology 下载失败：{e.__class__.__name__}"
            for rest in vols[i:]:
                if not _volume_ready(rest):
                    _record_failure(rest, msg)
            return


def _spawn_fill(vols: list[str]) -> None:
    """Download the missing volumes in the background: the first load is some 10 MB at one request per 2 s."""
    with _acl_lock:
        t = _acl_fill["thread"]
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=acl_fill, args=(vols,), daemon=True, name="acl-volumes")
        _acl_fill["thread"] = t
        t.start()


def acl_papers(today: date | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every paper of the recent events that is on disk, and the state of the download: how many
    volumes are in (`done`), how many are being left alone after a failure (`failed`, with the first
    `error`); the rest download in the background."""
    today = today or date.today()
    vols = acl_volumes(today)
    missing = [v for v, _ in vols if not _volume_ready(v)]
    failed = [r for v in missing if (r := _volume_failed(v))]
    if len(missing) > len(failed):
        _spawn_fill([v for v in missing if not _volume_failed(v)])
    cutoff = (today - timedelta(days=ACL_WINDOW_DAYS)).isoformat()
    out = []
    for vol, label in vols:
        d = _read_volume(vol)
        for p in (d or {}).get("papers", []):
            when = f"{p['year']}-{p['month']:02d}-01"
            if when >= cutoff[:8] + "01":
                out.append({**p, "venue": label, "published": when})
    return out, {"done": len(vols) - len(missing), "total": len(vols), "failed": len(failed),
                 "error": failed[0]["error"] if failed else None}


# ---------- matching for the feed ----------

def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in s if not unicodedata.combining(c)).lower()).strip()


def terms(value: str) -> list[str]:
    """A keyword follow as ANDed terms: words, or "quoted phrases" kept whole (as the arXiv query does)."""
    return [_fold(ph or w) for ph, w in re.findall(r'"([^"]+)"|(\S+)', value) if _fold(ph or w)]


def _stem(tok: str) -> str:
    """Folds plurals only ("agents" = "agent", "memories" = "memory"); "rag" must not match "leveraging"."""
    if len(tok) > 3 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _tokens(s: str) -> list[str]:
    return [_stem(t) for t in _fold(s).split()]


def has_terms(text: str, wanted: list[str]) -> bool:
    """Every term occurs in the text as whole words; a multi-word term ("memory consolidation") as
    those words next to each other, in that order."""
    hay = _tokens(text)

    def has(term: str) -> bool:
        t = _tokens(term)
        return bool(t) and any(hay[i:i + len(t)] == t for i in range(len(hay) - len(t) + 1))
    return bool(wanted) and all(has(t) for t in wanted)


def same_person(followed: str, author: str) -> bool:
    """Same surname, and the first names equal or one an initial of the other ("J. Smith" = "Jane Smith")."""
    a, b = _fold(followed).split(), _fold(author).split()
    if not a or not b or a[-1] != b[-1]:
        return False
    if len(a) == 1 or len(b) == 1:
        return len(a) == len(b)
    x, y = a[0], b[0]
    return x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))


def feed_openreview(f: dict[str, Any], days: int, today: date | None = None) -> list[dict[str, Any]]:
    """OpenReview submissions of the last `days` matching a followed keyword or person."""
    value = f["value"].strip()
    if f["kind"] == "person":
        term = f'"{value}"'
    else:
        term = value if '"' in value else " ".join(value.split())
    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
    wanted = terms(value) if f["kind"] != "person" else []
    out = []
    for n in openreview_search(term):
        c = n.get("content") or {}
        when = _ms_date(max(n.get("pdate") or 0, n.get("cdate") or 0))
        if not when or when < cutoff or not _or_value(c, "title"):
            continue
        if str(_or_value(c, "venueid") or "").startswith("dblp.org/"):
            continue  # DBLP's mirror (CoRR = arXiv again): dated by the import, and arXiv has the paper already
        if f["kind"] == "person":
            if not any(same_person(value, a) for a in _or_authors(c)):
                continue
        else:
            kws = _or_value(c, "keywords") or []
            if not has_terms(" ".join([_clean(_or_value(c, "title")), _clean(_or_value(c, "abstract")), *map(str, kws)]), wanted):
                continue
        cand = _or_candidate(n)
        out.append({"source": "openreview", "ext": cand["key"], "title": cand["title"], "authors": cand["authors"][:6],
                    "published": when, "summary": cand["abstract"][:500], "venue": cand["venue"], "url": cand["url"],
                    "keywords": [str(k) for k in (_or_value(c, "keywords") or [])][:6]})
    out.sort(key=lambda p: p["published"], reverse=True)
    return out


def feed_acl(f: dict[str, Any], today: date | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """ACL Anthology papers of the recent events whose title matches a followed keyword (volumes
    carry no abstracts) or whose author list holds a followed person; dated by the month of the event."""
    papers, progress = acl_papers(today)
    value = f["value"].strip()
    wanted = terms(value) if f["kind"] != "person" else []
    out = []
    for p in papers:
        if f["kind"] == "person":
            if not any(same_person(value, a) for a in p["authors"]):
                continue
        elif not has_terms(p["title"], wanted):
            continue
        out.append({"source": "acl", "ext": ext_key("acl", p["id"]), "title": p["title"], "authors": p["authors"][:6],
                    "published": p["published"], "summary": "", "venue": p["venue"], "month_only": True,
                    "url": f"{ANTHOLOGY}/{p['id']}/"})
    out.sort(key=lambda p: p["published"], reverse=True)
    return out, progress


# ---------- capture: resolve one thing, or search a title ----------

def resolve(kind: str, ident: str) -> dict[str, Any]:
    """The candidate for an OpenReview / ACL / DOI identifier. Raises NotFound for a miss,
    SourceError when the source cannot answer."""
    if kind == "openreview":
        return cached(f"or:{ident}", 30 * DAY, lambda: openreview_meta(ident))[0]
    if kind == "acl":
        return cached(f"acl:{ident}", 30 * DAY, lambda: acl_meta(ident))[0]
    if kind == "doi":
        return doi_meta(ident)
    raise ValueError(kind)


def _score(q: str, c: dict[str, Any]) -> float:
    nq, nt = _fold(q), _fold(c["title"])
    qt, tt = set(nq.split()), set(nt.split())
    if not qt or not tt:
        return 0.0
    overlap = len(qt & tt) / len(qt)
    return 0.7 * overlap + 0.3 * difflib.SequenceMatcher(None, nq, nt).ratio() + 0.05 * (len(c["sources"]) - 1)


def merge(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same paper found by several sources becomes one candidate (by arXiv id, DOI or title)."""
    out: list[dict[str, Any]] = []
    for c in cands:
        for o in out:
            if (c["arxiv"] and c["arxiv"] == o["arxiv"]) or (c["doi"] and c["doi"] == o["doi"]) \
                    or (_fold(c["title"]) and _fold(c["title"]) == _fold(o["title"])):
                for k in ("arxiv", "doi", "openreview", "acl", "abstract", "year"):
                    o[k] = o[k] or c[k]
                if c["venue"] and (not o["venue"] or o["venue"] == "arXiv"):
                    o["venue"] = c["venue"]
                    o["year"] = c["year"] or o["year"]
                o["authors"] = o["authors"] or c["authors"]
                o["sources"] = list(dict.fromkeys(o["sources"] + c["sources"]))
                break
        else:
            out.append(dict(c))
    for o in out:
        o["key"] = key_of(o)
        if o["arxiv"]:
            o["url"] = f"https://arxiv.org/abs/{o['arxiv']}"
    return out


SEARCHERS = (("arxiv", "arXiv", search_arxiv), ("crossref", "Crossref", search_crossref), ("openalex", "OpenAlex", search_openalex))


def search_title(q: str, n: int = 6) -> dict[str, Any]:
    """Top matches for a title from arXiv, Crossref and OpenAlex (queried side by side), with the
    state of each source: a source that fails is reported and the others still answer."""
    q = " ".join(q.split())

    def one(s: tuple[str, str, Callable[[str, int], list[dict[str, Any]]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        sid, label, fn = s
        try:
            got = fn(q, n)
            return {"id": sid, "label": label, "ok": True, "n": len(got), "error": None}, got
        except SourceError as e:
            return {"id": sid, "label": label, "ok": False, "n": 0, "error": str(e)}, []
        except Exception as e:  # noqa: BLE001 - an HTTP error or a malformed response: report it, keep the rest
            return {"id": sid, "label": label, "ok": False, "n": 0, "error": f"{label} 查询失败：{e.__class__.__name__}"}, []

    with ThreadPoolExecutor(len(SEARCHERS)) as pool:
        results = list(pool.map(one, SEARCHERS))
    found = merge([c for _, got in results for c in got if c["key"]])
    ranked = sorted(((_score(q, c), c) for c in found), key=lambda x: -x[0])
    return {"candidates": [c for s, c in ranked if s >= 0.35][:n], "sources": [st for st, _ in results]}


def with_ids(c: dict[str, Any]) -> dict[str, Any]:
    """A candidate that names itself by `key` (a feed card: openreview:<id>, acl:<id>, doi:<doi>,
    arxiv:<id>) gets the matching id field, so one shape of candidate reaches candidate_key."""
    key = str(c.get("key") or "")
    if m := re.fullmatch(r"arxiv:(\d{4}\.\d{4,5})", key):
        return {**c, "arxiv": m.group(1)}
    if parts := ext_parts(key):
        return {**c, parts[0]: parts[1]}
    return c


def ext_of(c: dict[str, Any]) -> str | None:
    """The external key a candidate also carries besides its arXiv id (kept on the reading item so a
    note found by DOI or an earlier OpenReview/DOI item is still recognised)."""
    for scheme in ("openreview", "acl", "doi"):
        if scheme == "doi" and norm_doi(str(c.get("doi") or "")).startswith("10.48550/arxiv."):
            continue  # arXiv's own DOI says nothing the arXiv id does not
        if c.get(scheme) and (k := ext_key(scheme, c[scheme])) and ext_parts(k):
            return k
    return None


def canonical(c: dict[str, Any]) -> dict[str, Any]:
    """The same classification a pasted DOI gets, for a candidate that arrives with only a DOI (a
    feed card, a Crossref match): an arXiv DOI is the arXiv paper, an ACL DOI the Anthology paper,
    any other DOI is checked for a preprint copy (best effort: without OpenAlex it stays a DOI)."""
    c = with_ids(c)
    if c.get("arxiv") or c.get("openreview") or c.get("acl") or not c.get("doi") or not doi_ok(norm_doi(str(c["doi"]))):
        return c
    hit = classify(f"doi:{c['doi']}")
    if hit and hit[0] in ("arxiv", "acl"):
        return {**c, hit[0]: hit[1]}
    try:
        if arxiv := openalex_copies(norm_doi(c["doi"])).get("arxiv"):
            return {**c, "arxiv": arxiv}
    except SourceError:
        pass
    return c


def candidate_key(c: dict[str, Any]) -> str | None:
    """The reading key of a candidate sent back by the browser, rebuilt from its ids; None unless
    it is a shape the app accepts."""
    if c.get("arxiv"):
        return f"arxiv:{c['arxiv']}" if re.fullmatch(r"\d{4}\.\d{4,5}", str(c["arxiv"])) else None
    k = key_of(c)
    return k if ext_parts(k) else None
