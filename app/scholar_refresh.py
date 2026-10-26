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
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from . import i18n, msgref, notes, scholar_capture, scholar_rank as R, scholars
from .config import DATA_DIR, DEVICE, LAB_TRACKER_DIR
from .store import Store

RANKING_FILE = LAB_TRACKER_DIR / "scholar-ranking.yaml"
DIR = DATA_DIR / "scholar-refresh"
SNAPS = DIR / "snapshots"
CHECK_TTL = 30 * 86400
NOISE_TTL = 180 * 86400

store = Store(RANKING_FILE, ("settings", "excluded", "runs", "labs"),
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
    "one_per_lab": True,  # keep only the best-cited scholar of each lab
    "openalex_topics": ["T10203"],  # field topic used to tell namesakes apart (Recommender Systems)
    "cadence_days": 30, "auto": True,
    "runner": "",  # the one machine that runs automatic recomputes (set by the first run)
}

for _d in (DIR, SNAPS):
    _d.mkdir(parents=True, exist_ok=True)


# ---------- settings, pins, excludes ----------

def rules() -> dict[str, Any]:
    rec = next((r for r in store.load()["settings"] if r["id"] == "rules"), {})
    return {**DEFAULT_RULES, **{k: v for k, v in rec.items() if k in DEFAULT_RULES}}


def set_rules(patch: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in patch.items() if k in DEFAULT_RULES}
    for k in ("one_per_lab", "auto"):
        if k in clean:
            clean[k] = bool(clean[k])
    if "runner" in clean:
        clean["runner"] = str(clean["runner"] or "")[:64]
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
        raise ValueError(i18n.t("scholars.refresh.need_name"))
    for e in excluded():
        if R.same_person(e, rec):
            return _shown_excluded(e)
    # kept in the shared vault file as {code, params, text} (msgref.persist): worded when the page reads it
    return _shown_excluded(store.add("excluded", {**rec, "reason": reason or msgref.persist(msgref.ref("scholars.refresh.manual_exclude"))}, "x"))


def _shown_excluded(rec: dict[str, Any]) -> dict[str, Any]:
    return {**rec, "reason": msgref.show(rec.get("reason"))}


def unexclude(xid: str) -> None:
    store.delete("excluded", xid)


def lab_hints() -> list[dict[str, Any]]:
    """People's labs that neither the list nor the Lab Tracker notes know (for one-per-lab)."""
    return store.load()["labs"]


def set_lab_hint(person: dict[str, Any], lab_id: str | None, lab_name: str | None) -> dict[str, Any]:
    rec = {k: person.get(k) for k in ("name", "scholar_id", "openalex") if person.get(k)}
    if not rec.get("name"):
        raise ValueError(i18n.t("scholars.refresh.need_name"))
    old = next((h for h in lab_hints() if R.same_person(h, rec)), None)
    if not lab_id and not lab_name:  # "no lab": forget the hint
        if old:
            store.delete("labs", old["id"])
        return {**rec, "lab_id": None}
    patch = {**rec, "lab_id": lab_id, "lab_name": lab_name or lab_id}
    return store.update("labs", old["id"], patch) if old else store.add("labs", patch, "l")


def set_pin(sid: str, on: bool) -> dict[str, Any]:
    return scholars.store.update("scholars", sid, {"pinned": bool(on) or None})


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- snapshots ----------

def save_snapshot(entries: list[dict[str, Any]], source: str, day: str | None = None, *, slug: str = "snapshot",
                  via: str | None = None) -> dict[str, Any]:
    """`source` is a msgref reference (worded when shown); `via` = "app" marks a read of the Scholar window."""
    snap = {"date": day or date.today().isoformat(), "source": source, "saved": _now(), "entries": entries,
            **({"via": via} if via else {})}
    name = f"{snap['date']}-{slug}.json"
    _atomic_json(SNAPS / name, snap)
    return {"file": name, "date": snap["date"], "source": msgref.show(source), "count": len(entries)}


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
        raise ValueError(i18n.t("scholars.refresh.no_rows"))
    return save_snapshot(entries, msgref.ref("scholars.refresh.src_import"), day, slug="google-scholar-import")


def latest_snapshot() -> dict[str, Any] | None:
    files = sorted(SNAPS.glob("*.json"), key=lambda f: f.stat().st_mtime)
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


def resolve(c: dict[str, Any], r: dict[str, Any] | None = None) -> dict[str, Any]:
    """OpenAlex author for a Scholar candidate. Someone already on the list keeps their id; for
    others the match must be confident (affiliation, or the field topic on a single namesake),
    otherwise this candidate is skipped (Unresolved) rather than judged by a namesake's papers."""
    if c.get("openalex"):
        return {"openalex": c["openalex"]}
    key = _person_key(c)
    with _cache_lock:
        hit = _load("ids.json").get(key)
    if hit and hit.get("unresolved") and time.time() - hit.get("ts", 0) < CHECK_TTL:
        raise R.Unresolved(hit["unresolved"])
    if hit and not hit.get("unresolved"):
        return hit
    d = _oa("authors", search=c["name"], per_page=10,
            select="id,display_name,cited_by_count,last_known_institutions,affiliations,topics")
    best, conf = R.pick_author(d.get("results", []), c["name"], c.get("affiliation"),
                               (r or rules()).get("openalex_topics") or [])
    if best is None or conf != "high":
        why = msgref.ref("scholars.refresh.no_author") if best is None else msgref.ref("scholars.refresh.ambiguous")
        out = {"unresolved": why, "ts": time.time()}
    else:
        out = {"openalex": best["id"].rsplit("/", 1)[-1], "oa_name": best["display_name"],
               "oa_cites": best.get("cited_by_count"), "at": _now()}
    with _cache_lock:
        ids = _load("ids.json")
        ids[key] = out
        _atomic_json(DIR / "ids.json", ids)
    if out.get("unresolved"):
        raise R.Unresolved(out["unresolved"])
    return out


def _rules_hash(r: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps([r["keywords"], r["topic_groups"], r["months"]]).encode()).hexdigest()[:8]


def check(c: dict[str, Any], r: dict[str, Any]) -> dict[str, Any]:
    """Field papers in the window (+ noise for those who pass), cached per OpenAlex id and rules."""
    oa = resolve(c, r)["openalex"]
    since = (date.today() - timedelta(days=int(r["months"] * 30.44))).isoformat()
    key = f"{oa}|{since[:7]}|{_rules_hash(r)}"
    with _cache_lock:
        hit = _load("checks.json").get(key)
    if hit and time.time() - hit.get("at", 0) < CHECK_TTL:
        if hit["field_papers"] >= r["min_papers"] and not hit.get("noise_checked"):
            # cached under a higher threshold, before noise mattered: check it now
            hit = {**hit, "noise": _noise(oa, R.compile_model(scholars.topic_model())), "noise_checked": True}
            with _cache_lock:
                cache = _load("checks.json")
                cache[key] = hit
                _atomic_json(DIR / "checks.json", cache)
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
           "last": max((w["date"] for w in works), default=None), "noise": None, "noise_checked": False,
           "at": time.time()}
    if len(titles) >= r["min_papers"]:
        out["noise"], out["noise_checked"] = _noise(oa, compiled), True
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

def _attempts() -> dict[str, Any]:
    """This machine's automatic-run attempts (failures in a row, next allowed try): survives restarts."""
    return _load("attempts.json")


def _record_attempt(ok: bool, error: str | None = None) -> None:
    a = _attempts()
    n = 0 if ok else int(a.get("failures") or 0) + 1
    _atomic_json(DIR / "attempts.json", {"failures": n, "last": _now(), "error": error,
                                         "next_try": time.time() + R.backoff_seconds(n)})


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
        pending = _shown_run({**pending, **{k: pending.get(k) or [] for k in ("ranking", "alternates", "skipped", "enter", "leave", "moves")}})
    att = _attempts()
    return {"job": _shown_job(), "rules": r, "excluded": [_shown_excluded(x) for x in excluded()], "pending": pending,
            "lab_hints": lab_hints(),
            "device": DEVICE, "attempts": {"failures": att.get("failures") or 0, "error": msgref.show(att.get("error")),
                                           "next_try": datetime.fromtimestamp(att["next_try"]).isoformat(timespec="minutes")
                                           if att.get("failures") else None},
            "history": [{k: msgref.show(x.get(k)) if k == "source" else x.get(k)
                         for k in ("id", "created", "source", "status", "applied_at")} for x in runs[:12]],
            "snapshot": {k: msgref.show(snap.get(k)) if k == "source" else snap.get(k)
                         for k in ("date", "source", "file")} | {"count": len(snap.get("entries", []))} if snap else None,
            "capture": scholar_capture.available(), "next_auto": nxt}


def _shown_job() -> dict[str, Any]:
    """The job as the page reads it: its step, source and error are kept as msgref references."""
    return {**_job, **{k: msgref.show(_job[k]) for k in ("current", "source", "error") if k in _job}}


def _shown_run(run: dict[str, Any]) -> dict[str, Any]:
    """A stored suggestion with its source and every reason worded in this Mac's interface language."""
    out = {**run, "source": msgref.show(run.get("source"))}
    for k in ("ranking", "alternates", "skipped", "enter", "leave"):
        out[k] = [{**c, "reason": msgref.show(c["reason"])} if c.get("reason") else c for c in run.get(k) or []]
    return out


def maybe_schedule() -> None:
    """Monthly by default: start an automatic recompute when the last one is older than the
    cadence, on the one machine chosen to run them, not sooner than the failure backoff allows."""
    r = rules()
    if not r["auto"] or _job.get("running"):
        return
    if r["runner"] and r["runner"] != DEVICE:
        return  # the other machine runs the automatic recomputes
    if time.time() < (_attempts().get("next_try") or 0):
        return  # failed recently: 6 h, 12 h, … up to a week
    runs = store.load()["runs"]
    month = f"{date.today():%Y-%m}"
    if any(not x.get("manual") and (x.get("created") or "")[:7] == month for x in runs):
        return  # this month's automatic run exists
    last = max((x.get("created", "") for x in runs), default="")
    if not last:  # never recomputed: count from when the list was last edited
        last = max((s.get("updated_at", "") for s in scholars.scholars()), default="")
    if last and (datetime.now() - datetime.fromisoformat(last)).days < r["cadence_days"]:
        return
    try:
        start(manual=False)
    except RuntimeError:  # a manual run started in between
        pass


def start(manual: bool = True) -> dict[str, Any]:
    with _lock:
        if _job.get("running"):
            raise RuntimeError(i18n.t("scholars.refresh.running"))
        _job.clear()
        _job.update(running=True, manual=manual, stage="prepare", done=0, total=0, current=None, needs_user=None,
                    error=None, source=None, started=_now())
    threading.Thread(target=_run, args=(manual,), daemon=True).start()
    return status()


def _progress(**kw: Any) -> None:
    _job.update(kw)


def _scholar_entries(manual: bool, r: dict[str, Any], current: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, str]:
    """(entries, source label, citation source) from the best available Scholar data."""
    snap = latest_snapshot()
    if snap and (snap.get("via") == "app" or "App" in snap.get("source", "")) and snap.get("saved"):  # "App": snapshots from before `via`
        age_h = (datetime.now() - datetime.fromisoformat(snap["saved"])).total_seconds() / 3600
        if age_h <= (12 if manual else 72):  # a retry never reads Scholar again for a recent capture
            return snap["entries"], msgref.ref("scholars.refresh.src_read", source=snap["source"], date=snap["date"]), f"Google Scholar label ranking, {snap['date']}"
    if scholar_capture.available():
        _progress(stage="scholar")
        try:
            extra = [c["scholar_id"] for c in current if c.get("scholar_id")]
            entries = scholar_capture.capture(r["labels"], r["pages_per_label"], extra, show_on_block=manual,
                                              progress=lambda **kw: _progress(**kw), cache=DIR / "capture-today.json")
            if entries:
                save_snapshot(entries, msgref.ref("scholars.refresh.src_app_window"), slug="google-scholar-app", via="app")
                return entries, msgref.ref("scholars.refresh.src_app_read"), f"Google Scholar label ranking, {date.today()}"
        except scholar_capture.NeedsUser as e:
            _progress(needs_user=e.state)  # automatic runs never pop the window: fall through
        except scholar_capture.CaptureError as e:
            _progress(error=msgref.ref("scholars.refresh.scholar_failed", error=str(e)))
    if snap and (date.today() - date.fromisoformat(snap["date"])).days <= 2 * r["cadence_days"]:
        return snap["entries"], msgref.ref("scholars.refresh.src_snapshot", source=snap["source"], date=snap["date"]), f"Google Scholar label ranking, {snap['date']}"
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
             "affiliation": s.get("affiliation"), "lab_id": s.get("lab_id"), "lab_name": s.get("lab"), "id": s["id"]}
            for s in scholars.scholars()]


def _save_run(run_id: str, run: dict[str, Any]) -> str:
    """Write a new suggestion into the vault file (the one before it, if still open, is superseded); its id."""
    with store.editing() as data:
        for old in data["runs"]:  # one open suggestion at a time
            if old.get("status") == "proposed" and not old.get("deleted"):
                old.update(status="superseded", updated_at=_now())
        while any(x["id"] == run_id and x.get("status") != "proposed" for x in data["runs"]):
            run_id += "-b"  # never overwrite a run that was applied or dismissed (e.g. synced in)
        data["runs"] = [x for x in data["runs"] if x["id"] != run_id]
        data["runs"].append({"id": run_id, **msgref.persist(run), "updated_at": _now()})  # the references in it, as fields
    return run_id


def _run(manual: bool) -> None:
    run_id = (f"run-{date.today():%Y-%m}" if not manual else f"run-{datetime.now():%Y-%m-%d-%H%M}") + f"-{DEVICE}"
    try:
        r = rules()
        if not r["runner"]:
            set_rules({"runner": DEVICE})  # the first machine to recompute runs the automatic ones
        current = _current()
        entries, source, cite_src = _scholar_entries(manual, r, current)
        if entries:
            # the current list joins the candidates (and lends its known OpenAlex ids); citations
            # come from this ranking when the person is in it
            known = [{k: v for k, v in c.items() if k not in ("rank", "id", "lab_id", "lab_name")} for c in current]
            for c in known:
                if any(R.same_person(c, e) for e in entries):
                    c.pop("citations", None)
            candidates = R.merge_entries(entries, known)
        else:
            _progress(stage="openalex")
            source, cite_src = msgref.ref("scholars.refresh.src_openalex"), f"OpenAlex, {date.today()}"
            candidates = _openalex_candidates(current, r)
        candidates = _with_labs(candidates, current)
        _progress(stage="check", source=source, total=len(candidates))
        counter = {"n": 0}

        def judge(c: dict[str, Any]) -> dict[str, Any]:
            counter["n"] += 1
            _progress(done=counter["n"], current=c.get("name"))
            return check(c, r)
        pins = [c for c in current if c["pinned"]]
        result = R.rank(candidates, judge, top_n=r["top_n"], alternates=r["alternates"], min_papers=r["min_papers"],
                        pinned=pins, excluded=excluded(), one_per_lab=r["one_per_lab"])
        d = R.diff(current, result["list"])
        slim = lambda c: {k: c.get(k) for k in ("name", "scholar_id", "openalex", "citations", "affiliation", "rank",  # noqa: E731
                                                "pinned", "reason", "scholar_rank", "via", "lab_name", "same_lab")} | (
            {"field_papers": c["verdict"].get("field_papers"), "titles": c["verdict"].get("titles", [])[:3],
             "openalex": c["verdict"].get("openalex") or c.get("openalex")} if c.get("verdict") else {})
        run = {"created": _now(), "source": source, "citation_source": cite_src, "status": "proposed", "manual": manual,
               "rules": {k: r[k] for k in ("min_papers", "months", "top_n", "keywords", "topic_groups", "one_per_lab")},
               "ranking": [slim(c) for c in result["list"]], "alternates": [slim(c) for c in result["alternates"]],
               "skipped": [slim(c) for c in result["skipped"][:40]],
               "enter": [slim(c) for c in d["enter"]], "leave": [slim(c) for c in d["leave"]], "moves": d["moves"]}
        run_id = _save_run(run_id, run)
        _progress(stage="done", run_id=run_id)
        _record_attempt(True)
    except R.CheckUnavailable as e:
        _progress(stage="failed", error=msgref.ref("scholars.refresh.oa_unavailable", error=str(e)))
        _record_attempt(False, str(e))
    except Exception as e:  # keep the page usable whatever happens
        _progress(stage="failed", error=msgref.ref("scholars.refresh.failed", cls=e.__class__.__name__, error=scholars.redact(str(e))))
        _record_attempt(False, _job["error"])
    finally:
        _progress(running=False, finished=_now(), finished_at=time.time())


def apply(run_id: str) -> dict[str, Any]:
    """Apply exactly what the suggestion showed, re-checked against today's pins and excludes
    (scholar_rank.plan_apply): add the shown entrants, remove the shown leavers, update the
    ranks; people added since are left alone. Records keep their previous rank for ▲/▼."""
    run = next((x for x in store.load()["runs"] if x["id"] == run_id), None)
    if not run or run.get("status") != "proposed":
        raise ValueError(i18n.t("scholars.refresh.already_handled"))
    today = date.today().isoformat()
    current = scholars.scholars()
    plan = R.plan_apply(current, run, excluded())
    shown = {R.norm_name(p["name"]): p for p in run.get("ranking") or []}
    from_scholar = "Scholar" in (run.get("citation_source") or "")
    for c in plan["remove"]:
        scholars.store.delete("scholars", c["id"])
    keep: set[str] = set()
    for n, c in plan["order"]:
        p = shown.get(R.norm_name(c["name"])) or {}
        patch: dict[str, Any] = {"rank": n, "prev_rank": c.get("rank") if c.get("id") else None, "rank_changed": today}
        if p:
            patch.update(rank_by=run["citation_source"],
                         field_papers={"count": p.get("field_papers"), "since": (run.get("rules") or {}).get("months")})
            if p.get("scholar_id") and from_scholar:
                patch["scholar_citations"] = {"value": p["citations"], "as_of": run["created"][:10], "source": run["citation_source"]}
        if c.get("id"):  # already on the list
            scholars.store.update("scholars", c["id"], patch)
            keep.add(c["id"])
            continue
        sid = c.get("scholar_id")
        lab_id, lab = _lab_for(c["name"])
        new = {"name": c["name"], "openalex": c.get("openalex"), "scholar_id": sid, "affiliation": c.get("affiliation"),
               "lab_id": lab_id, "lab": lab or c.get("lab_name"),
               "photo": f"https://scholar.googleusercontent.com/citations?view_op=view_photo&user={sid}&citpid=2" if sid else None,
               "added": run["id"], "sources": {"rank": msgref.show(run["source"], i18n.vault_lang()),  # provenance kept in the vault list: its language
                                                  "added": i18n.tv("scholars.refresh.added_by", id=run["id"], day=today)}, **patch}
        keep.add(scholars.store.add("scholars", {k: v for k, v in new.items() if v is not None}, "s")["id"])
    store.update("runs", run_id, {"status": "applied", "applied_at": _now(), "apply_notes": plan["notes"] or None})
    scholars.refresh_in_background(ids=keep)
    return {**status(), "notes": [msgref.show(n) for n in plan["notes"]]}


def _lab_index() -> dict[str, tuple[str, str]]:
    """Person (normalised name) -> (lab_id, lab note name) from the Lab Tracker lab notes."""
    out: dict[str, tuple[str, str]] = {}
    labs_dir = LAB_TRACKER_DIR / "Labs"
    for f in sorted(labs_dir.glob("*.md")) if labs_dir.exists() else []:
        fm = notes.read(f)["frontmatter"]
        for n in fm.get("arxiv_names") or []:
            if fm.get("lab_id"):
                out.setdefault(R.norm_name(n), (fm["lab_id"], f.stem))
    return out


def _lab_for(name: str) -> tuple[str | None, str | None]:
    """(lab_id, lab name) of the Lab Tracker lab that lists this person, if any."""
    return _lab_index().get(R.norm_name(name), (None, None))


def _with_labs(candidates: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every candidate a lab key (list record's lab, else the Lab Tracker notes) for one-per-lab."""
    idx = _lab_index()
    hints = lab_hints()
    out = []
    for c in candidates:
        rec = next((x for x in current if R.same_person(c, x)), None) \
            or next(({"lab_id": h.get("lab_id"), "lab_name": h.get("lab_name")} for h in hints if R.same_person(c, h)), None)
        lab_id = (rec or {}).get("lab_id") or idx.get(R.norm_name(c.get("name", "")), (None, None))[0]
        lab_name = (rec or {}).get("lab_name") or idx.get(R.norm_name(c.get("name", "")), (None, None))[1]
        key = lab_id or (f"name:{lab_name.lower()}" if lab_name else None)
        out.append(dict(c, lab=key, lab_name=lab_name or lab_id))
    return out


def dismiss(run_id: str) -> dict[str, Any]:
    store.update("runs", run_id, {"status": "dismissed"})
    return status()
