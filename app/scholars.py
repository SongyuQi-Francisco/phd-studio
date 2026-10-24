"""Top scholars of the research field: who they are, what they and their labs publish.

- The list is personal and lives in the vault (<lab_tracker>/scholars.yaml, a sync-safe
  Store with tombstones); the repo only ships scholars.example.yaml. Each record keeps
  where every fact came from (`sources`).
- Metrics and papers come from OpenAlex (author stats, counts_by_year, works of the last
  365 days), plus arXiv for preprints newer than the OpenAlex index. Requests are paced
  (OpenAlex 5/s, arXiv one per 3 s through tracking._fetch) and cached under
  DATA_DIR/scholars for a day; offline, the last cache is served and marked stale.
- Papers are tagged with the weekly tracker's topic model (research_tracker/config/
  topics.json, same A/B/C tiers as the radar) and flagged when they touch one of the
  profile's research lines (keywords per line kept next to the list).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pymupdf as fitz

from . import library, notes, reading, tracking
from .config import DATA_DIR, LAB_TRACKER_DIR, PROFILE, TRACKER_DIR, VAULT_DIR, secret
from .store import Store

OPENALEX = "https://api.openalex.org"
UA = {"User-Agent": "PhD-Studio/0.1 (local research workbench)"}
CACHE = DATA_DIR / "scholars"
THUMBS = DATA_DIR / "track-thumbs"  # DATA_DIR/thumbs belongs to the library thumbnails
TTL = 24 * 3600
SCHOLARS_FILE = LAB_TRACKER_DIR / "scholars.yaml"

store = Store(SCHOLARS_FILE, ("scholars", "lines"),
              "# Top scholars followed on the 追踪 page and keywords per research line, managed by PhD Studio.\n"
              "# Personal: lives in the vault, never in the repo (see scholars.example.yaml).\n")

for _d in (CACHE, CACHE / "img", THUMBS):
    _d.mkdir(parents=True, exist_ok=True)


class _Pace:
    """Request starts at least `gap` seconds apart. `with` holds the slot for the whole request
    (short API calls); `wait()` only reserves a start time (long downloads run unlocked)."""

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

    def wait(self) -> None:
        with self.lock:
            start = max(time.time(), self.last + self.gap)
            self.last = start
        time.sleep(max(0.0, start - time.time()))


_oa_pace = _Pace(0.2)
_pdf_pace = _Pace(3.0)


def redact(text: str) -> str:
    """Errors reach toasts and banners: never let the OpenAlex key ride along in a URL."""
    text = re.sub(r"(api_key=)[^&\s'\"]+", r"\1***", str(text))
    key = secret("OPENALEX_API_KEY", "openalex")
    return text.replace(key, "***") if key else text


def _oa(path: str, **params: Any) -> dict[str, Any]:
    key = secret("OPENALEX_API_KEY", "openalex")
    if key:
        params["api_key"] = key
    try:
        with _oa_pace:
            r = httpx.get(f"{OPENALEX}/{path}", params=params, headers=UA, timeout=40)
    except httpx.HTTPError as e:  # the message may carry the URL (and the key): rebuild it
        raise httpx.HTTPError(f"OpenAlex 连接失败（{e.__class__.__name__}）") from None
    if r.status_code == 429:
        raise httpx.HTTPError("OpenAlex 今日免费额度已用完（可在 secrets/openalex.env 里放 OPENALEX_API_KEY）")
    if r.status_code >= 400:
        raise httpx.HTTPError(f"OpenAlex 返回 HTTP {r.status_code}")
    return r.json()


def _short(oa_id: str | None) -> str:
    return (oa_id or "").rsplit("/", 1)[-1]


def _today() -> date:
    return date.today()  # local date, as the user reads it


# ---------- the list ----------

def scholars() -> list[dict[str, Any]]:
    return sorted(store.load()["scholars"], key=lambda s: (s.get("rank") or 999, s["name"]))


def _lab_ids() -> dict[str, dict[str, str]]:
    """lab_id -> {name, path} of the Lab Tracker lab notes."""
    out = {}
    labs_dir = LAB_TRACKER_DIR / "Labs"
    for f in sorted(labs_dir.glob("*.md")) if labs_dir.exists() else []:
        n = notes.read(f)
        if n["frontmatter"].get("lab_id"):
            out[n["frontmatter"]["lab_id"]] = {"name": n["name"], "path": n["path"]}
    return out


def listing() -> dict[str, Any]:
    labs = _lab_ids()
    out = []
    for s in scholars():
        out.append({**s, "tracked_lab": labs.get(s.get("lab_id") or "")})  # None when outside the 48 labs
    return {"scholars": out, "file": str(SCHOLARS_FILE.relative_to(VAULT_DIR)) if SCHOLARS_FILE.is_relative_to(VAULT_DIR) else str(SCHOLARS_FILE),
            "lab_count": len(labs), "lines": line_terms()}


def search_authors(q: str) -> list[dict[str, Any]]:
    """OpenAlex author candidates for the add box, most cited first."""
    d = _oa("authors", search=q, per_page=8,
            select="id,display_name,works_count,cited_by_count,summary_stats,last_known_institutions,topics")
    out = []
    for a in d.get("results", []):
        inst = (a.get("last_known_institutions") or [{}])[0]
        out.append({"openalex": _short(a["id"]), "name": a["display_name"], "works": a.get("works_count"),
                    "citations": a.get("cited_by_count"), "h_index": (a.get("summary_stats") or {}).get("h_index"),
                    "affiliation": inst.get("display_name"), "country": inst.get("country_code"),
                    "topics": [t["display_name"] for t in (a.get("topics") or [])[:3]]})
    return out


def add(body: dict[str, Any]) -> dict[str, Any]:
    name = (body.get("name") or "").strip()
    oa = _short(body.get("openalex"))
    if not name or not re.fullmatch(r"A\d+", oa):
        raise ValueError("需要姓名和 OpenAlex 作者 id（A 开头）")
    for s in scholars():
        if s.get("openalex") == oa:
            return s
    today = _today().isoformat()
    rec = {"name": name, "openalex": oa, "affiliation": body.get("affiliation"), "lab": body.get("lab"),
           "lab_id": body.get("lab_id"), "homepage": body.get("homepage"),
           "rank": body.get("rank") or (max([s.get("rank") or 0 for s in scholars()] or [0]) + 1),
           "added": "user", "sources": {"openalex": f"OpenAlex author search, {today}",
                                         "affiliation": f"OpenAlex last_known_institutions, {today}"}}
    return store.add("scholars", {k: v for k, v in rec.items() if v not in (None, "")}, "s")


def update(sid: str, patch: dict[str, Any]) -> dict[str, Any]:
    allowed = {"name", "rank", "affiliation", "lab", "lab_id", "lab_url", "homepage", "photo", "photo_alt", "scholar_id", "note"}
    return store.update("scholars", sid, {k: v for k, v in patch.items() if k in allowed})


def remove(sid: str) -> None:
    store.delete("scholars", sid)


# ---------- research lines ----------

_GENERIC_TAGS = {"idea", "research-line", "reading-course", "paper", "lab", "lab-tracker", "moc", "atlas"}


def line_terms() -> dict[str, list[str]]:
    """Keywords per profile line: the list file's `lines` records, else the tags of the research
    note named after the line. A term `a + b` needs both parts; parts match as word prefixes."""
    saved = {r["line"]: r.get("keywords") or [] for r in store.load()["lines"] if r.get("line")}
    out: dict[str, list[str]] = {}
    for line in PROFILE.get("lines", []):
        if saved.get(line):
            out[line] = saved[line]
            continue
        key = re.sub(r"\s+", "", line.lower())
        tags: list[str] = []
        for rel in PROFILE.get("research_notes", []):
            p = VAULT_DIR / rel
            if p.exists() and key in re.sub(r"\s+", "", p.stem.lower()):
                tags += [t.replace("-", " ") for t in notes.read(p)["frontmatter"].get("tags") or []
                         if isinstance(t, str) and t not in _GENERIC_TAGS and not t.endswith("-line")]
        out[line] = tags
    return out


def set_line_keywords(line: str, keywords: list[str]) -> dict[str, list[str]]:
    if line not in PROFILE.get("lines", []):
        raise ValueError(f"unknown line {line}")
    kw = [k.strip() for k in keywords if k.strip()]
    rec = next((r for r in store.load()["lines"] if r.get("line") == line), None)
    if rec:
        store.update("lines", rec["id"], {"keywords": kw})
    else:
        store.add("lines", {"line": line, "keywords": kw}, "l")
    return line_terms()


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().replace("-", " "))


def _term_hit(term: str, text: str) -> bool:
    return all(re.search(r"(?<![a-z0-9])" + re.escape(_norm_text(p).strip()), text)
               for p in term.split(" + ") if p.strip())


def _lines_of(text: str, terms: dict[str, list[str]]) -> dict[str, list[str]]:
    t = _norm_text(text)
    return {line: hits for line, ts in terms.items() if (hits := [x for x in ts if _term_hit(x, t)])}


# ---------- topic model (shared with the weekly tracker) ----------

_FALLBACK_TOPICS = {
    "domain_gate": {"patterns": ["recommend", "recsys", "personali[sz]", "user model"]},
    "topics": [
        {"id": "recsys", "label": "RecSys", "weight": 2, "patterns": ["recommend", "recsys", "collaborative filtering"]},
        {"id": "llm", "label": "LLM", "weight": 2, "patterns": ["large language model", r"\bllms?\b"]},
        {"id": "agent", "label": "Agentic", "weight": 3, "patterns": [r"\bagent", "agentic", "tool[- ]use"]},
        {"id": "evaluation", "label": "Evaluation", "weight": 1, "patterns": ["benchmark", "evaluation"]},
    ],
    "tiers": {"A": {"min_score": 10}, "B": {"min_score": 6}, "C": {"min_score": 2}},
}
_tm_cache: dict[str, Any] = {}


def topic_model() -> dict[str, Any]:
    f = TRACKER_DIR / "config" / "topics.json" if TRACKER_DIR else None
    stamp = f.stat().st_mtime if f and f.exists() else 0
    if _tm_cache.get("stamp") != stamp:
        data = json.loads(f.read_text(encoding="utf-8")) if stamp else json.loads(json.dumps(_FALLBACK_TOPICS))
        fl = re.IGNORECASE
        data["_gate"] = [re.compile(p, fl) for p in data["domain_gate"]["patterns"]]
        for t in data["topics"]:
            t["_re"] = [re.compile(p, fl) for p in t["patterns"]]
        _tm_cache.update(stamp=stamp, model=data)
    return _tm_cache["model"]


def score(title: str, abstract: str) -> tuple[int, list[str], str | None]:
    """Same scoring as research_tracker/tracker.py score_record."""
    tm = topic_model()
    hits, sc = [], 0
    for t in tm["topics"]:
        if any(r.search(title) for r in t["_re"]):
            sc += 2 * t["weight"]; hits.append(t["id"])
        elif any(r.search(abstract) for r in t["_re"]):
            sc += t["weight"]; hits.append(t["id"])
    for name in ("A", "B", "C"):
        spec = tm["tiers"][name]
        if sc >= spec["min_score"] and all(set(g) & set(hits) for g in spec.get("require_groups", [])):
            return sc, hits, name
    return sc, hits, None


def topic_labels() -> dict[str, str]:
    return {t["id"]: t.get("label", t["id"]) for t in topic_model()["topics"]}


def in_domain(text: str, categories: list[str] | None = None) -> bool:
    return "cs.IR" in (categories or []) or any(p.search(text) for p in topic_model()["_gate"])


# ---------- venues ----------

_VENUES = [
    ("SIGIR", r"\bsigir\b|research and development in information retrieval"), ("KDD", r"\bkdd\b|knowledge discovery and data mining"),
    ("WWW", r"web conference|\bwww\b"), ("WSDM", r"\bwsdm\b|web search and data mining"),
    ("CIKM", r"\bcikm\b|information and knowledge management"), ("RecSys", r"\brecsys\b|conference on recommender systems"),
    ("ACL", r"\bacl\b|annual meeting of the association for computational linguistics"),
    ("EMNLP", r"\bemnlp\b|empirical methods in natural language"), ("NAACL", r"\bnaacl\b|north american chapter"),
    ("NeurIPS", r"neurips|neural information processing systems"), ("ICLR", r"\biclr\b|learning representations"),
    ("ICML", r"\bicml\b|international conference on machine learning"), ("AAAI", r"\baaai\b"), ("IJCAI", r"\bijcai\b"),
    ("TOIS", r"transactions on information systems"), ("TKDE", r"transactions on knowledge and data engineering"),
    ("TORS", r"transactions on recommender systems"), ("ICDE", r"\bicde\b|international conference on data engineering"),
    ("MM", r"acm multimedia|\bacm mm\b"), ("ECIR", r"\becir\b|european conference on information retrieval"),
    ("UMAP", r"\bumap\b|user modeling, adaptation"), ("Findings", r"findings of the association"),
]


def venue_short(name: str | None) -> str | None:
    if not name:
        return None
    low = name.lower()
    if "arxiv" in low:
        return "arXiv"
    for short, pat in _VENUES:
        if re.search(pat, low):
            return short
    return name if len(name) <= 28 else name[:26] + "…"


# ---------- fetching ----------

def _cache_get(name: str) -> tuple[Any, float] | None:
    f = CACHE / f"{name}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8")), f.stat().st_mtime
    except json.JSONDecodeError:
        return None


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _cache_put(name: str, data: Any) -> None:
    _atomic_write(CACHE / f"{name}.json", json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _abstract(inv: dict[str, list[int]] | None) -> str:
    if not inv:
        return ""
    return " ".join(w for _, w in sorted((i, w) for w, idx in inv.items() for i in idx))


_ARXIV_IN = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})|10\.48550/arxiv\.(\d{4}\.\d{4,5})", re.I)


def _arxiv_of(w: dict[str, Any]) -> str | None:
    for s in [w.get("doi") or ""] + [(loc or {}).get("landing_page_url") or "" for loc in w.get("locations") or []]:
        m = _ARXIV_IN.search(s)
        if m:
            return m.group(1) or m.group(2)
    return None


def _work(w: dict[str, Any]) -> dict[str, Any]:
    src = ((w.get("primary_location") or {}).get("source") or {})
    venue = src.get("display_name")
    published = next((((loc or {}).get("source") or {}).get("display_name") for loc in w.get("locations") or []
                      if ((loc or {}).get("source") or {}).get("type") in ("conference", "journal")), None)
    return {
        "oa": _short(w.get("id")), "title": " ".join((w.get("display_name") or w.get("title") or "").split()),
        "date": w.get("publication_date"), "venue": venue_short(published or venue),
        "venue_full": published or venue, "arxiv": _arxiv_of(w), "doi": w.get("doi"),
        "cites": w.get("cited_by_count") or 0, "abstract": _abstract(w.get("abstract_inverted_index"))[:1500],
        "oa_topic": ((w.get("primary_topic") or {}).get("display_name")),
        "authors": [{"name": a["author"].get("display_name"), "oa": _short(a["author"].get("id")),
                     "inst": ", ".join(i.get("display_name", "") for i in a.get("institutions") or [])[:80]}
                    for a in (w.get("authorships") or [])],
        "source": "OpenAlex",
    }


WORK_FIELDS = ("id,doi,display_name,publication_date,primary_location,locations,authorships,"
               "cited_by_count,primary_topic,abstract_inverted_index,type")


def _fetch_author(s: dict[str, Any]) -> dict[str, Any]:
    oa = s["openalex"]
    a = _oa(f"authors/{oa}", select="id,display_name,works_count,cited_by_count,summary_stats,"
                                    "last_known_institutions,counts_by_year,topics,orcid")
    since = (_today() - timedelta(days=365)).isoformat()
    works: list[dict[str, Any]] = []
    for page in (1, 2):
        d = _oa("works", filter=f"author.id:{oa},from_publication_date:{since}", sort="publication_date:desc",
                per_page=200, page=page, select=WORK_FIELDS)
        works += [_work(w) for w in d.get("results", [])]
        if d["meta"]["count"] <= page * 200:
            break
    inst = (a.get("last_known_institutions") or [{}])[0]
    return {
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "author": {"name": a.get("display_name"), "works_count": a.get("works_count"),
                   "cited_by_count": a.get("cited_by_count"), "h_index": (a.get("summary_stats") or {}).get("h_index"),
                   "i10": (a.get("summary_stats") or {}).get("i10_index"),
                   "mean2y": (a.get("summary_stats") or {}).get("2yr_mean_citedness"), "orcid": a.get("orcid"),
                   "inst": {"name": inst.get("display_name"), "id": _short(inst.get("id")), "country": inst.get("country_code")},
                   "topics": [t["display_name"] for t in (a.get("topics") or [])[:5]]},
        "by_year": sorted([{"year": c["year"], "works": c["works_count"], "cites": c["cited_by_count"]}
                           for c in a.get("counts_by_year") or []], key=lambda c: c["year"]),
        "works": works,
    }


def _fetch_arxiv(name: str) -> list[dict[str, Any]]:
    """Newest arXiv preprints by this name; namesakes are cut by the RecSys/IR domain gate."""
    out = []
    for p in tracking._fetch(f'au:"{name}"', 50):
        if not in_domain(p["title"] + " " + p.get("summary", ""), p.get("categories")):
            continue
        out.append({"title": p["title"], "date": p["published"], "venue": "arXiv", "venue_full": "arXiv",
                    "arxiv": p["arxiv"], "doi": None, "cites": 0, "abstract": p.get("summary", ""),
                    "authors": [{"name": n} for n in p.get("authors", [])], "source": "arXiv"})
    return out


def _norm_title(t: str) -> str:
    s = unicodedata.normalize("NFKD", t or "")
    return re.sub(r"[^a-z0-9]+", "", s.lower())[:120]


def fetch_one(s: dict[str, Any], refresh: bool = False) -> dict[str, Any]:
    """OpenAlex + arXiv activity for one scholar, cached for a day; stale cache when offline."""
    name = f"author-{s['openalex']}"
    hit = _cache_get(name)
    if hit and not refresh and time.time() - hit[1] < TTL:
        return {**hit[0], "stale": False}
    try:
        data = _fetch_author(s)
        try:
            data["arxiv"] = _fetch_arxiv(s["name"])
        except (httpx.HTTPError, ValueError, ET.ParseError) as e:  # keep the OpenAlex part either way
            data["arxiv"], data["arxiv_error"] = (hit[0].get("arxiv", []) if hit else []), e.__class__.__name__
        _cache_put(name, data)
        _failed_at.pop(s["id"], None)
        return {**data, "stale": False}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        _failed_at[s["id"]] = time.time()
        msg = redact(str(e)) if isinstance(e, httpx.HTTPError) else f"OpenAlex 数据异常（{e.__class__.__name__}）"
        if hit:
            return {**hit[0], "stale": True, "error": msg}
        return {"stale": True, "error": msg, "works": [], "arxiv": [], "by_year": [], "author": {}}


# ---------- background refresh ----------

_job: dict[str, Any] = {"running": False, "done": 0, "total": 0, "current": None, "error": None, "finished": None}
_job_lock = threading.Lock()
_todo: list[dict[str, Any]] = []  # the running job's queue; scholars added mid-run are appended
_forced: set[str] = set()
_failed_at: dict[str, float] = {}  # scholar id -> last failed fetch (automatic retries wait 30 min)


def refresh_in_background(force: bool = False, ids: set[str] | None = None) -> dict[str, Any]:
    want = [s for s in scholars() if s.get("openalex") and (ids is None or s["id"] in ids)]
    with _job_lock:
        if _job["running"]:  # join the running job instead of being dropped
            queued = {s["id"] for s in _todo}
            extra = [s for s in want if s["id"] not in queued]
            _todo.extend(extra)
            _forced.update(s["id"] for s in extra if force)
            _job["total"] = len(_todo)
            return dict(_job)
        _todo[:] = want
        _forced.clear()
        _forced.update(s["id"] for s in want if force)
        _job.update(running=True, done=0, total=len(want), current=None, error=None)
    threading.Thread(target=_run_job, daemon=True).start()
    return dict(_job)


def _run_job() -> None:
    i, inst_for = 0, -1
    while True:
        with _job_lock:  # "nothing left" and "not running" flip together, so late additions start a new job
            n = len(_todo)
            if i >= n and inst_for == n:
                _job.update(running=False, current=None, finished=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                return
            s = _todo[i] if i < n else None
        if s is None:
            try:
                _institutions()
            except Exception as e:  # logos are optional
                _job["error"] = redact(f"机构信息：{e}")
            inst_for = n
            _prune(CACHE / "img", keep=300, days=180)
            continue
        _job["current"] = s["name"]
        try:
            got = fetch_one(s, refresh=s["id"] in _forced)
            if got.get("error"):
                _job["error"] = got["error"]
        except Exception as e:  # one scholar's failure never stops the others
            _failed_at[s["id"]] = time.time()
            _job["error"] = redact(f"{s['name']}：{e.__class__.__name__}")
        i += 1
        _job["done"] = i


def _needs_fetch(s: dict[str, Any]) -> bool:
    hit = _cache_get(f"author-{s['openalex']}")
    return not hit or time.time() - hit[1] >= TTL


# ---------- aggregation ----------

def _months(n: int = 12) -> list[str]:
    t = _today().replace(day=1)
    out = []
    for _ in range(n):
        out.append(t.strftime("%Y-%m"))
        t = (t - timedelta(days=1)).replace(day=1)
    return out[::-1]


def activity(refresh: bool = False) -> dict[str, Any]:
    """Everything the scholars tab draws, from cache; kicks off a background fetch when stale."""
    sch = [s for s in scholars() if s.get("openalex")]
    if refresh:
        refresh_in_background(force=True)
    else:
        due = {s["id"] for s in sch if _needs_fetch(s) and time.time() - _failed_at.get(s["id"], 0) > 1800}
        if due:
            refresh_in_background(ids=due)
    terms = line_terms()
    labels = topic_labels()
    labs = _lab_ids()
    items = reading.load()
    listed = {i.get("arxiv"): i["status"] for i in items if i.get("arxiv")}
    idx = library.arxiv_index()
    insts = (_cache_get("institutions") or ({}, 0))[0]
    today = _today()
    cut = {k: (today - timedelta(days=k)).isoformat() for k in (30, 90, 365)}
    months = _months(12)
    papers: dict[str, dict[str, Any]] = {}
    rows = []
    for s in sch:
        hit = _cache_get(f"author-{s['openalex']}")
        data = hit[0] if hit else {}
        fetched_age = time.time() - hit[1] if hit else None
        own: dict[str, dict[str, Any]] = {}
        for w in data.get("works", []) + data.get("arxiv", []):
            if not w.get("date") or w["date"] < cut[365] or not w.get("title"):
                continue
            k = w.get("arxiv") or _norm_title(w["title"])
            tk = _norm_title(w["title"])
            dup = own.get(k) or next((p for p in own.values() if _norm_title(p["title"]) == tk), None)
            if dup:  # keep the published venue, the arXiv id and the earliest date
                if dup.get("venue") in (None, "arXiv") and w.get("venue") not in (None, "arXiv"):
                    dup["venue"], dup["venue_full"] = w["venue"], w.get("venue_full")
                dup["arxiv"] = dup.get("arxiv") or w.get("arxiv")
                dup["cites"] = max(dup.get("cites") or 0, w.get("cites") or 0)
                exact = [x["date"] for x in (dup, w) if not (x.get("source") == "OpenAlex" and x["date"].endswith("-01-01"))]
                dup["date"] = min(exact) if exact else min(dup["date"], w["date"])
                if exact:
                    dup["source"] = "arXiv" if w.get("source") == "arXiv" or dup.get("source") == "arXiv" else dup.get("source")
                continue
            own[k] = dict(w)
        venues, coauthors, topics, monthly = Counter(), Counter(), Counter(), Counter()
        rel = 0
        for w in own.values():
            sc, hits, tier = score(w["title"], w.get("abstract", ""))
            ln = _lines_of(w["title"] + " " + w.get("abstract", ""), terms)
            # OpenAlex files year-only dates as YYYY-01-01: keep them out of month-level counts
            approx = w.get("source") == "OpenAlex" and w["date"].endswith("-01-01")
            w.update(score=sc, topics=hits, tier=tier, lines=sorted(ln), line_hits=ln, date_approx=approx)
            rel += bool(ln)
            venues[w.get("venue") or "未知"] += 1
            for t in hits:
                topics[t] += 1
            if not approx:
                monthly[w["date"][:7]] += 1
            for a in w.get("authors", []):
                if a.get("oa") != s["openalex"] and a.get("name") and _norm_title(a["name"]) != _norm_title(s["name"]):
                    coauthors[a["name"]] += 1
            key = w.get("arxiv") or w.get("doi") or _norm_title(w["title"])
            p = papers.get(key) or next((q for q in papers.values() if _norm_title(q["title"]) == _norm_title(w["title"])), None)
            if p:
                p["scholars"].append(s["id"])
                if p.get("venue") in (None, "arXiv") and w.get("venue") not in (None, "arXiv"):
                    p["venue"], p["venue_full"] = w["venue"], w.get("venue_full")
                p["arxiv"] = p.get("arxiv") or w.get("arxiv")
                if p.get("date_approx") and not approx:
                    p["date"], p["date_approx"] = w["date"], False
            else:
                papers[key] = {**{k: v for k, v in w.items() if k != "line_hits"}, "line_hits": ln, "scholars": [s["id"]],
                               "abstract": (w.get("abstract") or "")[:900],
                               "authors": [a["name"] for a in w.get("authors", [])][:12],
                               "n_authors": len(w.get("authors", []))}
        ws = list(own.values())
        author = data.get("author") or {}
        inst = insts.get((author.get("inst") or {}).get("id") or "", {})
        rows.append({
            **s, "tracked_lab": labs.get(s.get("lab_id") or ""),
            "metrics": {"citations": author.get("cited_by_count"), "h_index": author.get("h_index"),
                        "i10": author.get("i10"), "works": author.get("works_count"), "mean2y": author.get("mean2y"),
                        "d30": sum(w["date"] >= cut[30] and not w["date_approx"] for w in ws),
                        "d90": sum(w["date"] >= cut[90] and not w["date_approx"] for w in ws),
                        "d365": len(ws), "relevant": rel, "this_year": sum(w["date"][:4] == str(today.year) for w in ws)},
            "inst": {**(author.get("inst") or {}), "logo": bool(inst.get("logo")), "url": inst.get("homepage")},
            "by_year": [y for y in data.get("by_year", []) if y["year"] <= today.year][-10:],
            "monthly": [monthly.get(m, 0) for m in months],
            "venues": venues.most_common(6), "coauthors": coauthors.most_common(10),
            "topics": [(labels.get(t, t), n) for t, n in topics.most_common(6)],
            "fetched": data.get("fetched"), "age_h": round(fetched_age / 3600, 1) if fetched_age is not None else None,
            "error": data.get("error"),
        })
    plist = sorted(papers.values(), key=lambda p: p["date"], reverse=True)
    for p in plist:
        p["listed"] = listed.get(p.get("arxiv"))
        p["note"] = idx.get(p.get("arxiv")) if p.get("arxiv") else None
    exact = [p for p in plist if not p.get("date_approx")]
    totals = {"scholars": len(rows), "d30": sum(p["date"] >= cut[30] for p in exact),
              "d90": sum(p["date"] >= cut[90] for p in exact), "d365": len(plist),
              "year_only": len(plist) - len(exact),
              "relevant": sum(bool(p["lines"]) for p in plist),
              "relevant90": sum(bool(p["lines"]) and p["date"] >= cut[90] for p in plist)}
    monthly_all = [{"month": m, "relevant": sum(1 for p in exact if p["date"][:7] == m and p["lines"]),
                    "other": sum(1 for p in exact if p["date"][:7] == m and not p["lines"])} for m in months]
    return {"as_of": today.isoformat(), "months": months, "scholars": rows, "papers": plist, "totals": totals,
            "monthly": monthly_all, "topic_labels": labels, "lines": list(terms), "job": dict(_job)}


# ---------- institutions, avatars, thumbnails ----------

def _institutions(refresh: bool = False) -> dict[str, Any]:
    """Logo + homepage for each scholar's current institution (one batched OpenAlex call)."""
    hit = _cache_get("institutions")
    ids = set()
    for s in scholars():
        a = _cache_get(f"author-{s.get('openalex')}")
        iid = (((a or ({}, 0))[0].get("author") or {}).get("inst") or {}).get("id")
        if iid:
            ids.add(iid)
    have = hit[0] if hit else {}
    todo = sorted(ids - set(have)) if not refresh else sorted(ids)
    if todo:
        d = _oa("institutions", filter="openalex_id:" + "|".join(todo), per_page=50,
                select="id,display_name,image_thumbnail_url,homepage_url,country_code")
        for i in d.get("results", []):
            img = i.get("image_thumbnail_url") or ""
            # Wikimedia images are often campus photos; only keep what is named like a logo
            is_logo = re.search(r"logo|seal|emblem|insignia|wordmark|\.svg", img, re.I)
            have[_short(i["id"])] = {"name": i["display_name"], "logo": img if is_logo else None,
                                     "homepage": i.get("homepage_url"), "country": i.get("country_code")}
        _cache_put("institutions", have)
    return have


_bad_hosts: dict[str, float] = {}  # host -> skip until (unreachable or refusing)
FAIL_TTL = 86400


def _fresh(marker: Path, ttl: float = FAIL_TTL) -> bool:
    return marker.exists() and time.time() - marker.stat().st_mtime < ttl


def _download(url: str, limit: int, timeout: float = 30) -> tuple[bytes, str]:
    """GET with a size cap checked before (Content-Length) and while streaming; marks dead hosts."""
    host = urlsplit(url).hostname or ""
    if _bad_hosts.get(host, 0) > time.time():
        raise ValueError(f"{host} skipped after a recent failure")
    try:
        with httpx.stream("GET", url, headers=UA, timeout=timeout, follow_redirects=True) as r:
            r.raise_for_status()
            if int(r.headers.get("content-length") or 0) > limit:
                raise ValueError("too large")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise ValueError("too large")
            return bytes(buf), r.headers.get("content-type", "")
    except (httpx.TransportError, httpx.HTTPStatusError) as e:
        if isinstance(e, httpx.TransportError) or e.response.status_code in (403, 429) or e.response.status_code >= 500:
            _bad_hosts[host] = time.time() + 3600
        raise


def _prune(folder: Path, keep: int, days: int) -> None:
    """Re-creatable caches: drop files older than `days`, then all but the newest `keep`."""
    try:
        files = sorted((f for f in folder.iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return
    cutoff = time.time() - days * 86400
    for n, f in enumerate(files):
        if n >= keep or f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def _download_image(url: str, dest_stem: Path) -> Path | None:
    for ext in (".jpg", ".png", ".gif", ".webp", ".svg"):
        if dest_stem.with_suffix(ext).exists():
            return dest_stem.with_suffix(ext)
    fail = dest_stem.with_suffix(".fail")
    if _fresh(fail):
        return None
    try:
        content, ctype = _download(url, 8_000_000, timeout=20)
    except (httpx.HTTPError, ValueError):
        fail.touch()
        return None
    ext = ".png" if "png" in ctype else ".gif" if "gif" in ctype else ".webp" if "webp" in ctype \
        else ".svg" if "svg" in ctype else ".jpg" if ("jpeg" in ctype or "jpg" in ctype) else None
    if not ext:
        fail.touch()
        return None
    if ext in (".jpg", ".png") and len(content) > 80_000:  # card avatars are ≤ 64 px: keep a small copy
        try:
            pix = fitz.Pixmap(content)
            while pix.width > 480:
                pix.shrink(1)
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)
            out = dest_stem.with_suffix(".jpg")
            _atomic_write(out, pix.tobytes("jpeg", jpg_quality=85))
            return out
        except Exception:  # pymupdf cannot decode it: keep the original bytes
            pass
    out = dest_stem.with_suffix(ext)
    _atomic_write(out, content)
    return out


def avatar(sid: str) -> Path | None:
    """`photo` first (e.g. the Scholar profile picture), then `photo_alt` (e.g. the homepage portrait)."""
    s = next((x for x in scholars() if x["id"] == sid), None) or {}
    for url in (s.get("photo"), s.get("photo_alt")):
        stem = f"p-{sid}-{hashlib.sha1(url.encode()).hexdigest()[:10]}" if url else ""  # a new URL gets a new file
        got = _download_image(url, CACHE / "img" / stem) if url else None
        if got:
            return got
    return None


def logo(inst_id: str) -> Path | None:
    if not re.fullmatch(r"I\d+", inst_id):
        return None
    info = (_cache_get("institutions") or ({}, 0))[0].get(inst_id) or {}
    return _download_image(info["logo"], CACHE / "img" / f"i-{inst_id}") if info.get("logo") else None


# Thumbnails are rendered by a small paced worker pool; the page asks which are ready
# (thumbs_status) and the image route only serves finished files, so no request waits on arXiv.
_ARXIV_ID = re.compile(r"\d{4}\.\d{4,5}")
_thumb_q: list[str] = []
_thumb_busy: set[str] = set()
_thumb_workers = [0]
_thumb_lock = threading.Lock()
THUMB_WORKERS = 2
MAX_PDF = 40_000_000


def thumbnail(arxiv: str) -> Path | None:
    out = THUMBS / f"{arxiv}.jpg"
    return out if _ARXIV_ID.fullmatch(arxiv) and out.exists() else None


def thumbs_status(ids: list[str]) -> dict[str, list[str]]:
    ready, failed, pending = [], [], []
    for a in dict.fromkeys(ids[:80]):
        if not isinstance(a, str) or not _ARXIV_ID.fullmatch(a):
            continue
        if (THUMBS / f"{a}.jpg").exists():
            ready.append(a)
        elif _fresh(THUMBS / f"{a}.fail"):
            failed.append(a)
        else:
            pending.append(a)
    with _thumb_lock:
        for a in pending:
            if a not in _thumb_q and a not in _thumb_busy:
                _thumb_q.append(a)
        del _thumb_q[:-200]  # only the most recent asks matter
        while _thumb_workers[0] < THUMB_WORKERS and len(_thumb_q) > _thumb_workers[0] - len(_thumb_busy):
            _thumb_workers[0] += 1
            threading.Thread(target=_thumb_worker, daemon=True).start()
    return {"ready": ready, "failed": failed, "pending": pending}


def _thumb_worker() -> None:
    while True:
        with _thumb_lock:
            if not _thumb_q:
                _thumb_workers[0] -= 1
                last = _thumb_workers[0] == 0
                break
            a = _thumb_q.pop(0)
            _thumb_busy.add(a)
        try:
            _render_thumb(a)
        finally:
            with _thumb_lock:
                _thumb_busy.discard(a)
    if last:
        _prune(THUMBS, keep=600, days=120)


def _render_thumb(arxiv: str) -> None:
    out, fail = THUMBS / f"{arxiv}.jpg", THUMBS / f"{arxiv}.fail"
    if out.exists() or _fresh(fail):
        return
    try:
        _pdf_pace.wait()  # arXiv etiquette: starts ≥ 3 s apart; the download itself is not serialized
        content, _ = _download(f"https://export.arxiv.org/pdf/{arxiv}", MAX_PDF, timeout=60)
        if not content.startswith(b"%PDF"):
            raise ValueError("not a pdf")
        with fitz.open(stream=content, filetype="pdf") as doc:
            page = doc[0]
            zoom = 640 / page.rect.width  # sharp on a 2x screen at card width
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                  clip=fitz.Rect(0, page.rect.height * 0.05, page.rect.width, page.rect.height * 0.5))
            _atomic_write(out, pix.tobytes("jpeg", jpg_quality=82))
    except Exception:  # bad download or a PDF pymupdf cannot open: the card keeps its placeholder
        fail.touch()
