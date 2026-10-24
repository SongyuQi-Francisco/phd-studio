"""Tracking page: weekly lab-tracker runs, lab activity, and a live feed for follows.

- Runs come from the tracker's runs/<date>/candidates.json plus the vault report
  (its Claude-written ANALYSIS block).
- Labs come from the vault's Lab Tracker/Labs notes; activity is counted across runs.
- Follows (keywords, people) live in plan.yaml and are queried live against arXiv (cached 6 h,
  one request every 3 s as arXiv asks), OpenReview (cached 6 h) and the ACL Anthology (volumes
  cached on disk), each source on its own: one that fails shows as a status, the rest still
  answer. They are also exported to the tracker's config/follows.json so the weekly run
  fetches and keeps them too (arXiv only).
- Capture: an arXiv, OpenReview, DOI or ACL Anthology link or id goes straight into the reading
  inbox; a title is searched (arXiv, Crossref, OpenAlex) and the user picks the match.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import library, notes, plan, reading, sources
from .config import DATA_DIR, LAB_TRACKER_DIR, TRACKER_DIR

FEED_CACHE = DATA_DIR / "follow_feed"
CACHE_TTL = 6 * 3600
FEED_SOURCES = (("arxiv", "arXiv"), ("openreview", "OpenReview"), ("acl", "ACL Anthology"))
OPENREVIEW_DAYS = 90  # what the cached OpenReview answer covers; the page cuts it to its own window
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


def _exported() -> dict[str, Any]:
    f = follows_file()
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else {}
    except (OSError, ValueError):  # half-synced or hand-edited: treat as not exported yet
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("follows", []), list) else {}


def _wanted() -> list[dict[str, str]]:
    return [{"kind": x["kind"], "value": x["value"]} for x in follows()]


def export_follows() -> Path | None:
    """Write the follows where the stdlib-only tracker can read them (it has no YAML parser).
    Called only when follows change and right before a run, and writes only on a real change,
    so two machines whose plan.yaml has not synced yet do not keep overwriting each other."""
    f = follows_file()
    if not f:
        return None
    want = _wanted()
    if _exported().get("follows") == want:
        return f
    data = {"_doc": "Written by PhD Studio from plan.yaml follows; read by tracker.py (fetch + ingest). Do not edit.",
            "exported": datetime.now().isoformat(timespec="seconds"), "follows": want}
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=".follows.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=1)
        os.replace(tmp, f)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return f


def linkage() -> dict[str, Any]:
    """Read-only: whether the weekly tracker reads the workbench follows and what it last saw."""
    code = (TRACKER_DIR / "tracker.py") if TRACKER_DIR else None
    try:
        reads = bool(code and code.exists() and "follows.json" in code.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        reads = False
    exported = _exported()
    return {"tracker": bool(code and code.exists()), "reads_follows": reads, "exported": exported.get("exported"),
            "count": len(exported.get("follows", [])), "in_sync": exported.get("follows", []) == _wanted()}


def _query(f: dict[str, Any]) -> str:
    v = f["value"].replace('"', "")
    if f["kind"] == "person":
        return f'au:"{v}"'
    # words are ANDed; "quoted phrases" stay exact
    parts = re.findall(r'"([^"]+)"|(\S+)', f["value"])
    terms = " AND ".join(f'all:"{ph}"' if ph else f"all:{w}" for ph, w in parts)
    return f"({terms}) AND (cat:cs.IR OR cat:cs.CL OR cat:cs.AI OR cat:cs.LG OR cat:cs.MA)"


def _fetch(q: str, max_results: int) -> list[dict[str, Any]]:
    return sources.arxiv_query(q, max_results)


def _read_cache(f: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _failure(label: str, e: Exception) -> str:
    return str(e) if isinstance(e, sources.SourceError) else f"{label} 查询失败：{e.__class__.__name__}"


def _source_papers(sid: str, f: dict[str, Any], refresh: bool, today: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One source's papers for one follow, with its status. Never raises: a failing source serves
    its last cached answer (marked stale) or nothing, and says why."""
    label = dict(FEED_SOURCES)[sid]
    status: dict[str, Any] = {"id": sid, "label": label, "ok": True, "n": 0, "error": None, "stale": False, "loading": False}
    cache = FEED_CACHE / (f"{f['id']}.json" if sid == "arxiv" else f"{f['id']}.{sid}.json")
    papers: list[dict[str, Any]] = []
    try:
        if sid == "acl":  # filters volumes already on disk; the missing ones download in the background
            papers, progress = sources.feed_acl(f, today)
            if progress["done"] < progress["total"]:
                status.update(loading=True, progress=progress)
        elif not refresh and cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL:
            papers = _read_cache(cache)
        else:
            papers = _fetch(_query(f), 25) if sid == "arxiv" else sources.feed_openreview(f, OPENREVIEW_DAYS, today)
            cache.write_text(json.dumps(papers, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - whatever one source does, the others must still answer
        status.update(ok=False, error=_failure(label, e))
        papers = _read_cache(cache) if sid != "acl" else []
        status["stale"] = bool(papers)
    for p in papers:
        p["source"] = sid
    status["n"] = len(papers)
    return papers, status


def _merge_sources(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same paper found by several sources shows once (arXiv first); the others ride along as `also`."""
    order = {sid: n for n, (sid, _) in enumerate(FEED_SOURCES)}
    seen: dict[str, dict[str, Any]] = {}
    out = []
    for p in sorted(papers, key=lambda p: order.get(p["source"], 9)):
        t = re.sub(r"[^a-z0-9]+", "", p["title"].lower())
        if t and t in seen:
            seen[t].setdefault("also", []).append({k: p.get(k) for k in ("source", "ext", "venue", "url")})
            continue
        if t:
            seen[t] = p
        out.append(p)
    out.sort(key=lambda p: p["published"], reverse=True)
    return out


def feed(days: int = 30, per_follow: int = 8, refresh: bool = False, today: date | None = None) -> list[dict[str, Any]]:
    """Recent papers per follow from arXiv, OpenReview and the ACL Anthology (at most `per_follow` of
    each, so a burst on one source cannot crowd out the others), each labelled with its source, plus
    the state of every source (`sources`). Anthology papers are dated by the month of their event,
    so they use a year-long window instead of `days`."""
    FEED_CACHE.mkdir(parents=True, exist_ok=True)
    today = today or datetime.now(timezone.utc).date()
    cutoff = {sid: (today - timedelta(days=sources.ACL_WINDOW_DAYS if sid == "acl" else days)).isoformat()
              for sid, _ in FEED_SOURCES}
    items = reading.load()
    listed = {i.get("arxiv"): i["status"] for i in items if i.get("arxiv")}
    listed_ext = {i["ext"]: i["status"] for i in items if i.get("ext")}
    idx, ext_idx = library.arxiv_index(), library.ext_index()
    out = []
    for f in follows():
        with ThreadPoolExecutor(len(FEED_SOURCES)) as pool:  # different hosts: no reason to queue behind arXiv's 3 s
            results = list(pool.map(lambda s: _source_papers(s[0], f, refresh, today), FEED_SOURCES))
        recent = []
        for (papers, st), (sid, _) in zip(results, FEED_SOURCES):
            inside = sorted((p for p in papers if p.get("published", "") >= cutoff[sid]), key=lambda p: p["published"], reverse=True)
            st["n"] = len(inside)  # what the source had inside the window; the page lists at most per_follow of each
            recent += inside[:per_follow]
        recent = _merge_sources(recent)
        for p in recent:
            if p.get("arxiv"):
                p["listed"], p["note"] = listed.get(p["arxiv"]), idx.get(p["arxiv"])
            else:
                p["listed"], p["note"] = listed_ext.get(p.get("ext")), ext_idx.get(p.get("ext"))
        statuses = [st for _, st in results]
        out.append({**f, "papers": recent, "sources": statuses,  # every message already names its source
                    "error": "；".join(st["error"] for st in statuses if st["error"]) or None})
    return out


# ---------- capture ----------

KIND_LABELS = {"openreview": "OpenReview", "acl": "ACL Anthology", "doi": "DOI"}


def _push(cand: dict[str, Any], reason: str, tier: str | None, lines: list[str] | None) -> dict[str, Any]:
    cand = sources.with_ids(cand)
    key = sources.candidate_key(cand)
    if not key:
        raise ValueError("not a paper this app can list")
    title = (cand.get("title") or "").strip()[:300] or None
    if cand.get("arxiv"):
        item, what = reading.push(arxiv=cand["arxiv"], title=title, reason=reason, source="self",
                                  pushed_by="追踪 · 捕捉", tier=tier, lines=lines)
    else:
        url = cand.get("url") if str(cand.get("url") or "").startswith("https://") else None
        item, what = reading.push(ext=key, title=title, url=url, reason=reason, source="self",
                                  pushed_by="追踪 · 捕捉", tier=tier, lines=lines)
    return {"ok": True, "item": item, "result": what, "resolved": {"key": key, "title": title, "venue": cand.get("venue"),
                                                                    "year": cand.get("year")}}


def _capture_identified(kind: str, ident: str, reason: str, tier: str | None, lines: list[str] | None) -> dict[str, Any]:
    warning = None
    try:
        cand = sources.resolve(kind, ident)
    except sources.NotFound:
        return {"ok": False, "error": f"{KIND_LABELS[kind]} 里找不到 {ident}，链接或编号是不是写错了？"}
    except sources.SourceError as e:  # the identifier alone is enough to list it; the title can wait
        url = {"openreview": f"https://openreview.net/forum?id={ident}", "acl": f"https://aclanthology.org/{ident}/",
               "doi": f"https://doi.org/{ident}"}[kind]
        cand = sources.candidate(**{kind: ident}, url=url)
        warning = f"{e}：先按编号记进收件箱，标题没取到"
    out = _push(cand, reason, tier, lines)
    return {**out, "warning": warning} if warning else out


def capture(text: str, reason: str, tier: str | None = None, lines: list[str] | None = None) -> dict[str, Any]:
    """A pasted arXiv / OpenReview / DOI / ACL Anthology link or id goes straight into the reading
    inbox. A title is searched and the matches come back as `candidates` to pick from (see
    capture_pick). Anything else is returned as `unresolved` so the UI can hand it to the assistant."""
    text = (text or "").strip()
    reason = reason or "手动捕捉"
    if hit := sources.classify(text):
        kind, ident = hit
        if kind == "arxiv":
            item, what = reading.push(arxiv=ident, reason=reason, source="self", pushed_by="追踪 · 捕捉", tier=tier, lines=lines)
            return {"ok": True, "item": item, "result": what}
        return _capture_identified(kind, ident, reason, tier, lines)
    if len(text) < 6 or sources.looks_like_url(text):
        return {"ok": False, "unresolved": True}
    found = sources.search_title(text[:300])
    if found["candidates"]:
        return {"ok": False, "candidates": found["candidates"], "sources": found["sources"]}
    if not any(s["ok"] for s in found["sources"]):
        return {"ok": False, "error": "检索来源都没连上：" + "；".join(s["error"] for s in found["sources"]), "sources": found["sources"]}
    return {"ok": False, "unresolved": True, "sources": found["sources"]}


def capture_pick(cand: dict[str, Any], reason: str, tier: str | None = None, lines: list[str] | None = None) -> dict[str, Any]:
    """Put one match of a title search (or a feed card without an arXiv id) into the reading inbox."""
    return _push(cand, reason or "手动捕捉", tier, lines)
