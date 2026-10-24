"""Keeps the top-scholar list current: recompute the ranking monthly (or on 「立即更新」) and turn the
result into a suggestion that only takes effect when the user applies it.

- Rules, excluded people and one record per recompute (`runs`: source, ranked list, alternates,
  skipped with reasons, who enters/leaves) live in the vault next to the list
  (<lab_tracker>/scholar-ranking.yaml, sync-safe Store). Pins are a flag on the list record.
- Scholar data: the desktop app's Scholar window (scholar_capture: the user signs in once; login
  and CAPTCHA are handed to the user, never automated), else the newest imported or captured
  snapshot, else OpenAlex citations (labelled as such). Snapshots and OpenAlex lookups are
  cached under DATA_DIR/scholar-refresh.
- The rules themselves (ordering, field filter, noise, pins, excludes) are in scholar_rank.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from . import notes, scholar_capture, scholar_rank as R, scholars
from .config import DATA_DIR, LAB_TRACKER_DIR
from .store import Store

RANKING_FILE = LAB_TRACKER_DIR / "scholar-ranking.yaml"
DIR = DATA_DIR / "scholar-refresh"
SNAPS = DIR / "snapshots"
CHECK_TTL = 30 * 86400
NOISE_TTL = 180 * 86400

store = Store(RANKING_FILE, ("settings", "excluded", "runs"),
              "# How the top-scholar list is recomputed, who is excluded, and every recompute's result.\n"
              "# Managed by PhD Studio (追踪 → 学者动态 → 立即更新). Personal: lives in the vault, not in the repo.\n")

DEFAULT_RULES: dict[str, Any] = {
    # Scholar author searches (mauthors=...), merged by user id
    "labels": ["label:recommender_systems", "label:recommendation", "label:recommender_system",
               "label:recommendation_systems", "label:recommendation_system",
               "label:recommender_systems label:large_language_models"],
    "pages_per_label": 5,
    # field filter: OpenAlex works matching the keywords, then topic groups that must all be hit
    "keywords": ["recommendation", "recommender"],
    "topic_groups": [["recsys"], ["llm", "agent"]],
    "min_papers": 3, "months": 24, "top_n": 10, "alternates": 5,
    "cadence_days": 30, "auto": True,
}

for _d in (DIR, SNAPS):
    _d.mkdir(parents=True, exist_ok=True)


# ---------- settings, pins, excludes ----------

def rules() -> dict[str, Any]:
    rec = next((r for r in store.load()["settings"] if r["id"] == "rules"), {})
    return {**DEFAULT_RULES, **{k: v for k, v in rec.items() if k in DEFAULT_RULES}}


def set_rules(patch: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in patch.items() if k in DEFAULT_RULES}
    for k in ("min_papers", "months", "top_n", "alternates", "cadence_days", "pages_per_label"):
        if k in clean:
            clean[k] = max(1, int(clean[k]))
    with store.editing() as data:
        rec = next((r for r in data["settings"] if r["id"] == "rules"), None)
        if rec is None:
            rec = {"id": "rules", "created_at": _now()}
            data["settings"].append(rec)
        rec.update(clean, updated_at=_now())
    return rules()


def excluded() -> list[dict[str, Any]]:
    return store.load()["excluded"]


def exclude(person: dict[str, Any], reason: str) -> dict[str, Any]:
    rec = {k: person.get(k) for k in ("name", "scholar_id", "openalex") if person.get(k)}
    if not rec.get("name"):
        raise ValueError("需要姓名")
    for e in excluded():
        if R.same_person(e, rec):
            return e
    return store.add("excluded", {**rec, "reason": reason or "手动排除"}, "x")


def unexclude(xid: str) -> None:
    store.delete("excluded", xid)


def set_pin(sid: str, on: bool) -> dict[str, Any]:
    return scholars.store.update("scholars", sid, {"pinned": bool(on) or None})


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- snapshots ----------

def save_snapshot(entries: list[dict[str, Any]], source: str, day: str | None = None) -> dict[str, Any]:
    snap = {"date": day or date.today().isoformat(), "source": source, "saved": _now(), "entries": entries}
    name = f"{snap['date']}-{re.sub(r'[^a-z]+', '-', source.lower()).strip('-')[:20] or 'snapshot'}.json"
    _atomic_json(SNAPS / name, snap)
    return {"file": name, "date": snap["date"], "source": source, "count": len(entries)}


def import_snapshot(text: str) -> dict[str, Any]:
    """A pasted ranking (the markdown rows from a Scholar session) or a JSON snapshot."""
    text = (text or "").strip()
    if text.startswith("{"):
        data = json.loads(text)
        entries, day = data.get("entries") or [], data.get("date")
    else:
        parsed = R.parse_snapshot_md(text)
        entries, day = parsed["entries"], parsed["date"]
    if not entries:
        raise ValueError("没有读到任何学者行（格式：'12. 姓名 · Scholar id · 65k · 机构 · 备注'）")
    return save_snapshot(entries, "Google Scholar（导入）", day)


def latest_snapshot() -> dict[str, Any] | None:
    files = sorted(SNAPS.glob("*.json"))
    for f in reversed(files):
        try:
            return {**json.loads(f.read_text(encoding="utf-8")), "file": f.name}
        except (OSError, ValueError):
            continue
    return None


def _atomic_json(path: Path, data: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load(name: str) -> dict[str, Any]:
    try:
        return json.loads((DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------- OpenAlex lookups (cached; raise CheckUnavailable when they cannot be made) ----------

_cache_lock = threading.Lock()


def _person_key(c: dict[str, Any]) -> str:
    return f"gs:{c['scholar_id']}" if c.get("scholar_id") else f"name:{R.norm_name(c.get('name', ''))}"


def _oa(path: str, **params: Any) -> dict[str, Any]:
    try:
        return scholars._oa(path, **params)
    except (httpx.HTTPError, ValueError) as e:
        raise R.CheckUnavailable(scholars.redact(str(e))) from None


_STOP = {"university", "of", "the", "and", "institute", "school", "college", "department", "technology", "science",
         "research", "national", "professor", "lab", "laboratory", "center", "centre", "computer", "univ"}


def resolve(c: dict[str, Any]) -> dict[str, Any]:
    """OpenAlex author for a Scholar candidate: name search, weighed by affiliation overlap."""
    if c.get("openalex"):
        return {"openalex": c["openalex"]}
    key = _person_key(c)
    with _cache_lock:
        hit = _load("ids.json").get(key)
    if hit:
        return hit
    d = _oa("authors", search=c["name"], per_page=8,
            select="id,display_name,cited_by_count,last_known_institutions,affiliations")
    want = {w for w in re.findall(r"[a-z]{4,}", (c.get("affiliation") or "").lower()) if w not in _STOP}
    best, best_score = None, -1.0
    for a in d.get("results", []):
        if R.norm_name(a["display_name"]).split()[-1:] != R.norm_name(c["name"]).split()[-1:]:
            continue  # surname must agree
        insts = " ".join(i.get("display_name", "") for i in a.get("last_known_institutions") or [])
        insts += " " + " ".join(x["institution"].get("display_name", "") for x in (a.get("affiliations") or [])[:15])
        overlap = len([w for w in want if w in insts.lower()]) / max(1, len(want))
        score = (a.get("cited_by_count") or 0) * (1 + 3 * overlap)
        if score > best_score:
            best, best_score = a, score
    if best is None:
        raise R.CheckUnavailable(f"OpenAlex 里找不到 {c['name']}")
    out = {"openalex": best["id"].rsplit("/", 1)[-1], "oa_name": best["display_name"],
           "oa_cites": best.get("cited_by_count"), "at": _now()}
    with _cache_lock:
        ids = _load("ids.json")
        ids[key] = out
        _atomic_json(DIR / "ids.json", ids)
    return out


def _rules_hash(r: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps([r["keywords"], r["topic_groups"], r["months"]]).encode()).hexdigest()[:8]


def check(c: dict[str, Any], r: dict[str, Any]) -> dict[str, Any]:
    """Field papers in the window (+ noise for those who pass), cached per OpenAlex id and rules."""
    oa = resolve(c)["openalex"]
    since = (date.today() - timedelta(days=int(r["months"] * 30.44))).isoformat()
    key = f"{oa}|{since[:7]}|{_rules_hash(r)}"
    with _cache_lock:
        hit = _load("checks.json").get(key)
    if hit and time.time() - hit.get("at", 0) < CHECK_TTL:
        return hit
    compiled = R.compile_model(scholars.topic_model())
    search = " OR ".join(f'"{k}"' if " " in k else k for k in r["keywords"])
    works = []
    for page in (1, 2, 3):
        d = _oa("works", filter=f"author.id:{oa},from_publication_date:{since},title_and_abstract.search:{search}",
                per_page=200, page=page, select="display_name,publication_date,abstract_inverted_index")
        works += [{"title": w.get("display_name") or "", "date": w.get("publication_date") or "",
                   "abstract": scholars._abstract(w.get("abstract_inverted_index"))} for w in d.get("results", [])]
        if d["meta"]["count"] <= page * 200:
            break
    titles = R.field_papers(works, compiled, r["topic_groups"], since)
    out = {"openalex": oa, "field_papers": len(titles), "titles": titles[:8], "since": since,
           "last": max((w["date"] for w in works), default=None), "noise": None, "at": time.time()}
    if len(titles) >= r["min_papers"]:
        out["noise"] = _noise(oa, compiled)
    with _cache_lock:
        cache = _load("checks.json")
        cache[key] = out
        _atomic_json(DIR / "checks.json", {k: v for k, v in cache.items() if time.time() - v.get("at", 0) < NOISE_TTL})
    return out


def _noise(oa: str, compiled: dict[str, Any]) -> str | None:
    d = _oa("works", filter=f"author.id:{oa}", sort="cited_by_count:desc", per_page=40,
            select="display_name,cited_by_count,institutions_distinct_count")
    top = [{"title": w.get("display_name") or "", "cites": w.get("cited_by_count") or 0,
            "team": w.get("institutions_distinct_count") or 0} for w in d.get("results", [])]
    return R.noise_verdict(top, compiled)


# ---------- the recompute job ----------

_job: dict[str, Any] = {"running": False}
_lock = threading.Lock()


def status() -> dict[str, Any]:
    runs = sorted(store.load()["runs"], key=lambda x: x.get("created", ""), reverse=True)
    snap = latest_snapshot()
    r = rules()
    last = runs[0] if runs else None
    nxt = None
    if r["auto"]:
        base = (last or {}).get("created", "")[:10] or date.today().isoformat()
        nxt = (date.fromisoformat(base) + timedelta(days=r["cadence_days"])).isoformat() if last else date.today().isoformat()
    pending = next((x for x in runs if x.get("status") == "proposed"), None)
    if pending:  # the Store drops empty lists on write
        pending = {**pending, **{k: pending.get(k) or [] for k in ("ranking", "alternates", "skipped", "enter", "leave", "moves")}}
    return {"job": dict(_job), "rules": r, "excluded": excluded(), "pending": pending,
            "history": [{k: x.get(k) for k in ("id", "created", "source", "status", "applied_at")} for x in runs[:12]],
            "snapshot": {k: snap.get(k) for k in ("date", "source", "file")} | {"count": len(snap.get("entries", []))} if snap else None,
            "capture": scholar_capture.available(), "next_auto": nxt}


def maybe_schedule() -> None:
    """Monthly by default: start an automatic recompute when the last one is older than the cadence."""
    r = rules()
    if not r["auto"] or _job.get("running"):
        return
    if _job.get("finished_at") and time.time() - _job["finished_at"] < 6 * 3600:
        return  # a failed automatic attempt waits a few hours
    runs = store.load()["runs"]
    month_id = f"run-{date.today():%Y-%m}"
    if any(x["id"] == month_id for x in runs):
        return  # this month's automatic run exists (maybe from the other machine)
    last = max((x.get("created", "") for x in runs), default="")
    if not last:  # never recomputed: count from when the list was last edited
        last = max((s.get("updated_at", "") for s in scholars.scholars()), default="")
    if last and (datetime.now() - datetime.fromisoformat(last)).days < r["cadence_days"]:
        return
    start(manual=False)


def start(manual: bool = True) -> dict[str, Any]:
    with _lock:
        if _job.get("running"):
            raise RuntimeError("名单正在重算")
        _job.clear()
        _job.update(running=True, manual=manual, stage="prepare", done=0, total=0, current=None, needs_user=None,
                    error=None, source=None, started=_now())
    threading.Thread(target=_run, args=(manual,), daemon=True).start()
    return status()


def _progress(**kw: Any) -> None:
    _job.update(kw)


def _scholar_entries(manual: bool, r: dict[str, Any], current: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, str]:
    """(entries, source label, citation source) from the best available Scholar data."""
    if scholar_capture.available():
        _progress(stage="scholar")
        try:
            extra = [c["scholar_id"] for c in current if c.get("scholar_id")]
            entries = scholar_capture.capture(r["labels"], r["pages_per_label"], extra, show_on_block=manual,
                                              progress=lambda **kw: _progress(**kw))
            if entries:
                save_snapshot(entries, "Google Scholar（App 窗口）")
                return entries, "Google Scholar（App 内窗口读取）", f"Google Scholar label ranking, {date.today()}"
        except scholar_capture.NeedsUser as e:
            _progress(needs_user=e.state)  # automatic runs never pop the window: fall through
        except scholar_capture.CaptureError as e:
            _progress(error=f"Scholar 读取失败：{e}")
    snap = latest_snapshot()
    if snap and (date.today() - date.fromisoformat(snap["date"])).days <= 2 * r["cadence_days"]:
        return snap["entries"], f"{snap['source']}（快照 {snap['date']}）", f"Google Scholar label ranking, {snap['date']}"
    return [], "", ""


def _openalex_candidates(current: list[dict[str, Any]], r: dict[str, Any]) -> list[dict[str, Any]]:
    """Fallback without Scholar data: the current list plus the last snapshot's people, ordered by
    OpenAlex citations (labelled as such)."""
    snap = latest_snapshot()
    people = R.merge_entries(current, (snap or {}).get("entries", []))
    out = []
    for c in people:
        if c.get("noise") or c.get("deceased"):
            out.append(c)
            continue
        info = resolve(c)
        cites = info.get("oa_cites")
        if cites is None:
            a = _oa(f"authors/{info['openalex']}", select="cited_by_count")
            cites = a.get("cited_by_count")
        out.append(dict(c, openalex=info["openalex"], citations=cites or 0))
    return out


def _current() -> list[dict[str, Any]]:
    return [{"name": s["name"], "scholar_id": s.get("scholar_id"), "openalex": s.get("openalex"), "rank": s.get("rank"),
             "pinned": bool(s.get("pinned")), "citations": (s.get("scholar_citations") or {}).get("value") or 0,
             "affiliation": s.get("affiliation"), "id": s["id"]} for s in scholars.scholars()]


def _run(manual: bool) -> None:
    run_id = f"run-{date.today():%Y-%m}" if not manual else f"run-{datetime.now():%Y-%m-%d-%H%M}"
    try:
        r = rules()
        current = _current()
        entries, source, cite_src = _scholar_entries(manual, r, current)
        if entries:
            # the current list joins the candidates (and lends its known OpenAlex ids); citations
            # come from this ranking when the person is in it
            known = [{k: v for k, v in c.items() if k not in ("rank", "id")} for c in current]
            for c in known:
                if any(R.same_person(c, e) for e in entries):
                    c.pop("citations", None)
            candidates = R.merge_entries(entries, known)
        else:
            _progress(stage="openalex")
            source, cite_src = "OpenAlex（Scholar 不可用，按 OpenAlex 被引排序）", f"OpenAlex, {date.today()}"
            candidates = _openalex_candidates(current, r)
        _progress(stage="check", source=source, total=len(candidates))
        counter = {"n": 0}

        def judge(c: dict[str, Any]) -> dict[str, Any]:
            counter["n"] += 1
            _progress(done=counter["n"], current=c.get("name"))
            return check(c, r)
        pins = [c for c in current if c["pinned"]]
        result = R.rank(candidates, judge, top_n=r["top_n"], alternates=r["alternates"], min_papers=r["min_papers"],
                        pinned=pins, excluded=excluded())
        d = R.diff(current, result["list"])
        slim = lambda c: {k: c.get(k) for k in ("name", "scholar_id", "openalex", "citations", "affiliation", "rank",  # noqa: E731
                                                "pinned", "reason", "scholar_rank", "via")} | (
            {"field_papers": c["verdict"].get("field_papers"), "titles": c["verdict"].get("titles", [])[:3],
             "openalex": c["verdict"].get("openalex") or c.get("openalex")} if c.get("verdict") else {})
        run = {"created": _now(), "source": source, "citation_source": cite_src, "status": "proposed", "manual": manual,
               "rules": {k: r[k] for k in ("min_papers", "months", "top_n", "keywords", "topic_groups")},
               "ranking": [slim(c) for c in result["list"]], "alternates": [slim(c) for c in result["alternates"]],
               "skipped": [slim(c) for c in result["skipped"][:40]],
               "enter": [slim(c) for c in d["enter"]], "leave": [slim(c) for c in d["leave"]], "moves": d["moves"]}
        with store.editing() as data:
            for old in data["runs"]:  # one open suggestion at a time
                if old.get("status") == "proposed" and not old.get("deleted"):
                    old.update(status="superseded", updated_at=_now())
            data["runs"] = [x for x in data["runs"] if x["id"] != run_id]
            data["runs"].append({"id": run_id, **run, "updated_at": _now()})
        _progress(stage="done", run_id=run_id)
    except R.CheckUnavailable as e:
        _progress(stage="failed", error=f"OpenAlex 暂时不可用：{e}（已查过的结果已缓存，稍后再试会接着算）")
    except Exception as e:  # keep the page usable whatever happens
        _progress(stage="failed", error=scholars.redact(f"{e.__class__.__name__}: {e}"))
    finally:
        _progress(running=False, finished=_now(), finished_at=time.time())


def apply(run_id: str) -> dict[str, Any]:
    """Make the suggestion the list: update ranks/citations, add who enters, remove who leaves
    (never pinned ones). Each record remembers its previous rank for the ▲/▼ on the cards."""
    run = next((x for x in store.load()["runs"] if x["id"] == run_id), None)
    if not run or run.get("status") != "proposed":
        raise ValueError("这条建议已经处理过了")
    today = date.today().isoformat()
    current = {s["id"]: s for s in scholars.scholars()}
    keep: set[str] = set()
    for p in run["ranking"]:
        rec = next((s for s in current.values() if R.same_person(p, s)), None)
        patch = {"rank": p["rank"], "prev_rank": rec.get("rank") if rec else None, "rank_changed": today,
                 "rank_by": run["citation_source"],
                 "field_papers": {"count": p.get("field_papers"), "since": (run.get("rules") or {}).get("months")}}
        if p.get("scholar_id") and "Scholar" in (run.get("citation_source") or ""):
            patch["scholar_citations"] = {"value": p["citations"], "as_of": run["created"][:10], "source": run["citation_source"]}
        if rec:
            scholars.store.update("scholars", rec["id"], patch)
            keep.add(rec["id"])
            continue
        sid = p.get("scholar_id")
        lab_id, lab = _lab_for(p["name"])
        new = {"name": p["name"], "openalex": p.get("openalex"), "scholar_id": sid, "affiliation": p.get("affiliation"),
               "lab_id": lab_id, "lab": lab,
               "photo": f"https://scholar.googleusercontent.com/citations?view_op=view_photo&user={sid}&citpid=2" if sid else None,
               "added": run["id"],
               "sources": {"rank": run["source"], "added": f"{run['id']} 建议，{today} 确认"}, **patch, "prev_rank": None}
        keep.add(scholars.store.add("scholars", {k: v for k, v in new.items() if v is not None}, "s")["id"])
    for sid, s in current.items():
        if sid not in keep and not s.get("pinned"):
            scholars.store.delete("scholars", sid)
    store.update("runs", run_id, {"status": "applied", "applied_at": _now()})
    scholars.refresh_in_background(ids=keep)
    return status()


def _lab_for(name: str) -> tuple[str | None, str | None]:
    """(lab_id, lab name) of the Lab Tracker lab that lists this person, if any."""
    want = R.norm_name(name)
    labs_dir = LAB_TRACKER_DIR / "Labs"
    for f in sorted(labs_dir.glob("*.md")) if labs_dir.exists() else []:
        fm = notes.read(f)["frontmatter"]
        if any(R.norm_name(n) == want for n in fm.get("arxiv_names") or []):
            return fm.get("lab_id"), f.stem
    return None, None


def dismiss(run_id: str) -> dict[str, Any]:
    store.update("runs", run_id, {"status": "dismissed"})
    return status()
