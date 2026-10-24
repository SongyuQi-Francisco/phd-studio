"""Tracking page: weekly lab-tracker runs, lab activity, and a live arXiv feed for follows.

- Runs come from the tracker's runs/<date>/candidates.json plus the vault report
  (its Claude-written ANALYSIS block).
- Labs come from the vault's Lab Tracker/Labs notes; activity is counted across runs.
- Follows (keywords, people) live in plan.yaml and are queried live against the
  arXiv API (cached 6 h, one request every 3 s as arXiv asks). They are also exported to
  the tracker's config/follows.json so the weekly run fetches and keeps them too.
"""
from __future__ import annotations

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from . import library, notes, plan, reading
from .config import DATA_DIR, LAB_TRACKER_DIR, TRACKER_DIR

ARXIV_API = "https://export.arxiv.org/api/query"
FEED_CACHE = DATA_DIR / "follow_feed"
CACHE_TTL = 6 * 3600
_arxiv_lock = threading.Lock()
_last_call = [0.0]
_ANALYSIS = re.compile(r"<!-- BEGIN ANALYSIS -->(.*?)<!-- END ANALYSIS -->", re.S)


# ---------- runs ----------

def _runs_dir() -> Path | None:
    d = TRACKER_DIR / "runs" if TRACKER_DIR else None
    return d if d and d.exists() else None


def runs() -> list[dict[str, Any]]:
    d = _runs_dir()
    out = []
    for f in sorted(d.glob("*/candidates.json"), reverse=True) if d else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"run": data.get("run", f.parent.name), "since": data.get("since"),
                    "generated": data.get("generated"), "stats": data.get("stats", {})})
    return out


def run(run_id: str) -> dict[str, Any]:
    d = _runs_dir()
    f = d / run_id / "candidates.json" if d else None
    if not f or not f.exists() or f.parent.parent != d:
        raise FileNotFoundError(run_id)
    data = json.loads(f.read_text(encoding="utf-8"))
    report = LAB_TRACKER_DIR / "Runs" / f"{run_id} Lab arXiv Radar.md"
    analysis = ""
    if report.exists():
        m = _ANALYSIS.search(report.read_text(encoding="utf-8", errors="replace"))
        analysis = m.group(1).strip() if m else ""
    items = reading.load()
    listed = {i.get("arxiv"): i["status"] for i in items if i.get("arxiv")}
    by_paper = {i.get("paper"): i["status"] for i in items if i.get("paper")}
    idx = library.arxiv_index()
    papers = []
    for p in data.get("papers", []):
        aid = re.sub(r"v\d+$", "", str(p.get("id", "")))
        note = idx.get(aid) or p.get("in_vault")
        papers.append({
            "arxiv": aid, "title": p.get("title"), "authors": p.get("authors", [])[:8],
            "published": p.get("published"), "tier": p.get("tier"), "score": p.get("score"),
            "labs": p.get("labs", []), "follows": p.get("follows", []), "topics": p.get("topics", []),
            "abstract": p.get("abstract", ""),
            "note": note if isinstance(note, str) else None,
            "listed": listed.get(aid) or (by_paper.get(note) if isinstance(note, str) else None),
        })
    order = {"A": 0, "B": 1, "C": 2}
    papers.sort(key=lambda p: (order.get(p["tier"], 3), -(p["score"] or 0)))
    return {"run": data.get("run", run_id), "since": data.get("since"), "generated": data.get("generated"),
            "stats": data.get("stats", {}), "analysis": analysis, "papers": papers,
            "report_url": notes.obsidian_url(report) if report.exists() else None}


# ---------- labs ----------

def labs() -> list[dict[str, Any]]:
    activity: dict[str, list[dict[str, Any]]] = {}
    d = _runs_dir()
    for f in sorted(d.glob("*/candidates.json")) if d else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for p in data.get("papers", []):
            for lab in p.get("labs", []):
                activity.setdefault(lab, []).append({"title": p.get("title"), "arxiv": p.get("id"),
                                                     "published": p.get("published"), "tier": p.get("tier"),
                                                     "run": data.get("run")})
    out = []
    for f in sorted((LAB_TRACKER_DIR / "Labs").glob("*.md")) if (LAB_TRACKER_DIR / "Labs").exists() else []:
        n = notes.read(f)
        fm = n["frontmatter"]
        recent = sorted(activity.get(fm.get("lab_id"), []), key=lambda x: x.get("published") or "", reverse=True)
        out.append({"name": n["name"], "lab_id": fm.get("lab_id"), "institution": fm.get("institution"),
                    "country": fm.get("country"), "sector": fm.get("sector"), "tracks": fm.get("tracks") or [],
                    "people": fm.get("arxiv_names") or [], "count": len(recent), "recent": recent[:5],
                    "path": n["path"], "obsidian_url": n["obsidian_url"]})
    out.sort(key=lambda x: (-x["count"], x["name"]))
    return out


def lab_matrix() -> dict[str, Any]:
    """Papers kept per lab per weekly run (for the cross-lab heatmap), oldest run first."""
    d = _runs_dir()
    runs_, counts, tiers = [], {}, {}
    for f in sorted(d.glob("*/candidates.json")) if d else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        r = data.get("run", f.parent.name)
        runs_.append({"run": r, "since": data.get("since"), "by_tier": data.get("stats", {}).get("by_tier", {}),
                      "kept": data.get("stats", {}).get("kept", len(data.get("papers", [])))})
        for p in data.get("papers", []):
            for lab in p.get("labs", []):
                counts.setdefault(lab, {}).setdefault(r, 0)
                counts[lab][r] += 1
                tiers.setdefault(lab, Counter())[p.get("tier")] += 1
    roster = TRACKER_DIR / "config" / "roster.json" if TRACKER_DIR else None
    try:
        names = {k: v.get("name", k) for k, v in json.loads(roster.read_text(encoding="utf-8"))["tracks"].items()} if roster else {}
    except (OSError, KeyError, json.JSONDecodeError):
        names = {}
    return {"runs": runs_, "counts": counts, "tiers": {k: dict(v) for k, v in tiers.items()}, "track_names": names}


# ---------- follows: live arXiv feed ----------

def follows() -> list[dict[str, Any]]:
    return plan.plan_store.load().get("follows", [])


def add_follow(kind: str, value: str, note: str | None = None) -> dict[str, Any]:
    if kind not in ("keyword", "person"):
        raise ValueError("kind must be keyword or person")
    value = value.strip()
    if not value:
        raise ValueError("empty")
    for f in follows():
        if f["kind"] == kind and f["value"].lower() == value.lower():
            return f
    rec = plan.plan_store.add("follows", {"kind": kind, "value": value, "note": note}, "f")
    export_follows()
    return rec


def remove_follow(fid: str) -> None:
    plan.plan_store.delete("follows", fid)
    export_follows()


def follows_file() -> Path | None:
    return TRACKER_DIR / "config" / "follows.json" if TRACKER_DIR and (TRACKER_DIR / "config").exists() else None


def export_follows() -> Path | None:
    """Write the follows where the stdlib-only tracker can read them (it has no YAML parser)."""
    f = follows_file()
    if not f:
        return None
    data = {"_doc": "Written by PhD Studio from plan.yaml follows; read by tracker.py (fetch + ingest). Do not edit.",
            "exported": datetime.now().isoformat(timespec="seconds"),
            "follows": [{"kind": x["kind"], "value": x["value"]} for x in follows()]}
    old = f.read_text(encoding="utf-8") if f.exists() else ""
    if json.loads(old or "{}").get("follows") != data["follows"]:
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(f)
    return f


def linkage() -> dict[str, Any]:
    """Whether the weekly tracker reads the workbench follows, and what it last saw (synced first:
    follows edited on the other machine arrive through plan.yaml)."""
    f = export_follows()
    code = (TRACKER_DIR / "tracker.py") if TRACKER_DIR else None
    reads = bool(code and code.exists() and "follows.json" in code.read_text(encoding="utf-8", errors="replace"))
    exported = json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else {}
    return {"tracker": bool(code and code.exists()), "reads_follows": reads,
            "exported": exported.get("exported"), "count": len(exported.get("follows", []))}


def _query(f: dict[str, Any]) -> str:
    v = f["value"].replace('"', "")
    if f["kind"] == "person":
        return f'au:"{v}"'
    # words are ANDed; "quoted phrases" stay exact
    parts = re.findall(r'"([^"]+)"|(\S+)', f["value"])
    terms = " AND ".join(f'all:"{ph}"' if ph else f"all:{w}" for ph, w in parts)
    return f"({terms}) AND (cat:cs.IR OR cat:cs.CL OR cat:cs.AI OR cat:cs.LG OR cat:cs.MA)"


def _fetch(q: str, max_results: int) -> list[dict[str, Any]]:
    with _arxiv_lock:
        wait = 3.0 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        r = httpx.get(ARXIV_API, params={"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending",
                                         "max_results": max_results}, timeout=30)
        _last_call[0] = time.time()
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


def feed(days: int = 30, per_follow: int = 8, refresh: bool = False) -> list[dict[str, Any]]:
    FEED_CACHE.mkdir(parents=True, exist_ok=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    items = reading.load()
    listed = {i.get("arxiv"): i["status"] for i in items if i.get("arxiv")}
    idx = library.arxiv_index()
    out = []
    for f in follows():
        cache = FEED_CACHE / f"{f['id']}.json"
        papers, error = [], None
        if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL:
            papers = json.loads(cache.read_text(encoding="utf-8"))
        else:
            try:
                papers = _fetch(_query(f), 25)
                cache.write_text(json.dumps(papers, ensure_ascii=False), encoding="utf-8")
            except (httpx.HTTPError, ET.ParseError) as e:
                error = f"arXiv 查询失败：{e.__class__.__name__}"
                if cache.exists():
                    papers = json.loads(cache.read_text(encoding="utf-8"))
        recent = [p for p in papers if p["published"] >= cutoff][:per_follow]
        for p in recent:
            p["listed"] = listed.get(p["arxiv"])
            p["note"] = idx.get(p["arxiv"])
        out.append({**f, "papers": recent, "error": error})
    return out


# ---------- capture ----------

_ARXIV_ANY = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


def capture(text: str, reason: str, tier: str | None = None, lines: list[str] | None = None) -> dict[str, Any]:
    """A pasted arXiv link / id goes straight into the reading inbox; anything else is
    returned as `unresolved` so the UI can hand it to the assistant."""
    m = _ARXIV_ANY.search(text or "")
    if not m:
        return {"ok": False, "unresolved": True}
    item, what = reading.push(arxiv=m.group(1), reason=reason or "手动捕捉", source="self",
                              pushed_by="追踪 · 捕捉", tier=tier, lines=lines)
    return {"ok": True, "item": item, "result": what}
