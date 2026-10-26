"""Results into a map, always through ops.apply: one atomic batch per import, per CLI report, per confirmed
proposal. A measurement is told apart by its source, config, dataset, metric, run and date (`model.identity`; the
date only when the data has one), so importing the same table twice changes nothing, and a watched file can be read
again whenever it changes.

    preview(slug, ...)        what a table looks like and the mapping to use; writes nothing
    run_import(slug, node, ...)  upsert a table (pasted, or a file) onto an experiment / result node
    build_report(slug, node, items, ...)  the ops for a few measurements a script or the assistant reports
    bulk_set(slug, ...)       mark a config as a baseline, say which way a metric improves, for many records at once
    sync(...)                 re-read the files this Mac watches (see watcher.py)

Every read-compute-write runs under a lock on the project's results that works across processes (the app's
watcher, `studio map result sync --loop` and the CLI all write), so two of them never both decide that a row is new.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import date
from pathlib import Path
from typing import Any

from ... import i18n
from ...config import DATA_DIR, DEVICE
from .. import model as map_model, ops, project
from ..records import child_live
from . import mapping as maps, model, refs, tabular

log = logging.getLogger(__name__)
SAMPLE_ROWS = 8
MAX_RECORDS = ops.MAX_IMPORT_OPS - 2  # one atomic batch: the records, a source record and the log line
BULK_CHUNK = 390                      # a person's batch is capped at ops.MAX_OPS; bulk_set goes in chunks of this
STATE_FILE = DATA_DIR / "rmap" / "results-watch.json"
_lock = threading.RLock()  # threads of this process; the file locks below are for the others


def today() -> str:
    return date.today().isoformat()


@contextmanager
def _flock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def writing(slug: str):
    """The cross-process lock around reading a project's results, deciding what to write, and writing it."""
    return _flock(project.LOCKS / f"{slug}.results.lock")


# ---- reading what is there (a node's results alone: the rest of the map's records are not needed) ----

def _core(slug: str) -> dict[str, Any]:
    return project.get(slug).read(attachments=False)


def _live_nodes(m: dict[str, Any]) -> set[str]:
    return {n["id"] for n in m["nodes"]}


def _existing(slug: str, m: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    live = _live_nodes(m)
    return [r for r in project.get(slug).kind_store(model.RESULTS).load_node(node_id, include_deleted=True) if child_live(r, live)]


def _sources(slug: str, m: dict[str, Any]) -> list[dict[str, Any]]:
    live = _live_nodes(m)
    return [r for r in project.get(slug).kind_store(model.SOURCES).load(include_deleted=True)[model.SOURCES.name]
            if child_live(r, live) and r.get("node") in live]


def _node(m: dict[str, Any], key_: str) -> dict[str, Any]:
    return refs.find_node(m, key_, types=model.NODE_TYPES)


def upsert_ops(existing: list[dict[str, Any]], node_id: str, recs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """attach.add for new measurements and attach.update for the ones whose value changed. A measurement is found
    again by `model.identity`; one whose date was only inferred (the day a table without dates was read) gets the new
    day when its value changes, since the new number is the measurement now."""
    have = {model.identity(a): a for a in existing}
    seen: dict[tuple, dict[str, Any]] = {}
    for r in recs:
        seen[model.identity(r)] = r  # a later row of the same measurement wins
    out: list[dict[str, Any]] = []
    stats = {"added": 0, "updated": 0, "unchanged": 0}
    for k, r in seen.items():
        old = have.get(k)
        if old is None:
            out.append({"op": "attach.add", "kind": "results", "node": node_id, "set": r})
            stats["added"] += 1
            continue
        patch: dict[str, Any] = {}
        if r["value"] != old.get("value"):
            patch["value"] = r["value"]
            if r.get("date_inferred"):
                patch["date"] = r["date"]
        for flag in ("baseline", "better"):  # only ever set from an import: a baseline ticked in the page stays ticked
            if r.get(flag) and r[flag] != old.get(flag):
                patch[flag] = r[flag]
        if patch:
            out.append({"op": "attach.update", "kind": "results", "id": old["id"], "set": patch})
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    return out, stats


def _check_size(ops_list: list[dict[str, Any]]) -> None:
    if len(ops_list) > MAX_RECORDS:
        raise ValueError(i18n.t("rmap_results.too_many", n=len(ops_list), max=MAX_RECORDS))


# ---- preview ----

def preview(slug: str, *, text: str | None = None, path: str | None = None, fmt: str | None = None,
            node: str | None = None) -> dict[str, Any]:
    """The columns, a few rows, the mapping to start from (the remembered one when there is one), the configs, and
    what is already kept for this file on the node (so the page does not undo a watch it did not set)."""
    m = _core(slug)
    table = tabular.read_file(path) if path else tabular.parse(text or "", fmt)
    shown = table.meta.get("path")
    sources = _sources(slug, m)
    remembered = maps.remembered(table, sources, shown)
    guess = remembered or maps.guess(table)
    try:
        cfgs = maps.configs(table, guess) if guess.get("config") else []
    except ValueError:
        cfgs = []
    here = next((s for s in sources if shown and s.get("path") == shown and (not node or s.get("node") == node)), None)
    return {"format": table.format, "columns": table.columns, "rows": table.rows[:SAMPLE_ROWS], "total": len(table.rows),
            "truncated": table.truncated, "mapping": guess, "remembered": bool(remembered), "configs": cfgs[:60],
            "file": table.meta or None, "device": DEVICE,
            "existing": {"id": here["id"], "watch": bool(here.get("watch")), "device": here.get("device")} if here else None}


# ---- import ----

def _source_op(sources: list[dict[str, Any]], node_id: str, table: tabular.Table, mapping: dict[str, Any], watch: bool | None) -> dict[str, Any] | None:
    shown = table.meta["path"]
    fields: dict[str, Any] = {"path": shown, "format": table.format, "columns": table.columns, "mapping": mapping}
    if watch is not None:  # None leaves the watch as it is: a manual import never turns the other Mac's watching off
        fields["watch"] = True if watch else None
        fields["device"] = DEVICE if watch else None
    old = next((s for s in sources if s.get("node") == node_id and s.get("path") == shown), None)
    if old is None:
        return {"op": "attach.add", "ref": "$source", "kind": "result_sources", "node": node_id, "set": {k: v for k, v in fields.items() if v is not None}}
    patch = {k: v for k, v in fields.items() if old.get(k) != v}
    return {"op": "attach.update", "kind": "result_sources", "id": old["id"], "set": patch} if patch else None


def run_import(slug: str, node_key: str, *, text: str | None = None, path: str | None = None, fmt: str | None = None,
               mapping: dict[str, Any] | None = None, remember: bool = True, watch: bool | None = None,
               label: str | None = None, default_date: str | None = None, who: str = "import", dry_run: bool = False,
               summary: str | None = None, trusted: bool = False) -> dict[str, Any]:
    """Upsert a table onto an experiment / result node as one atomic batch. Returns the counts
    (added, updated, unchanged, skipped), what was skipped, and for a real import the source record's id.
    watch: True / False sets whether this Mac watches the file (None leaves it as it is). trusted: the person at the
    command line named the file themselves, so it need not be inside a results root (a watched file always must)."""
    table = tabular.read_file(path, in_roots=not trusted or bool(watch)) if path else tabular.parse(text or "", fmt)
    with (nullcontext() if dry_run else writing(slug)), _lock:
        m = _core(slug)
        node = _node(m, node_key)
        sources = _sources(slug, m)
        shown = table.meta.get("path")
        chosen = maps.clean(mapping) if mapping else (maps.remembered(table, sources, shown) or maps.guess(table))
        source = shown if path else (label or "paste")
        day = model.day(default_date) or (table.meta.get("date") if path else today())
        recs, skipped = maps.records(table, chosen, default_date=day, source=source)
        if not recs:
            raise ValueError(i18n.t("rmap_results.no_rows"))
        ops_list, stats = upsert_ops(_existing(slug, m, node["id"]), node["id"], recs)
        src = _source_op(sources, node["id"], table, chosen, watch) if path and remember else None
        if src:
            ops_list.append(src)
        _check_size(ops_list)
        out = {**stats, "skipped": len(skipped), "skipped_rows": skipped[:10], "total": len(recs), "node": node["id"],
               "source": source, "truncated": table.truncated, "mapping": chosen,
               "configs": list(dict.fromkeys(r["config"] for r in recs))[:60]}
        if not ops_list:
            return out
        sentence = summary or i18n.t("rmap_results.imported", n=stats["added"] + stats["updated"], source=Path(source).name if path else source)
        if stats["added"] + stats["updated"] == 0:
            sentence = None  # only the source record changed: no log line
        if dry_run:
            for r in recs:
                model.RESULTS.clean(r, new=True)  # the checks a write makes, without loading everything to simulate it
            return {**out, "dry_run": True, "sample": [model.line(r) for r in recs[:12]]}
        res = ops.apply(slug, ops_list, who=who, summary=sentence)
        sid = res["refs"].get("$source") or (src or {}).get("id")
        if path and sid:
            _remember_state(slug, sid, table.meta)
        return {**out, "source_id": sid}


# ---- a few measurements reported by a script or the assistant ----

def clean_item(item: dict[str, Any], *, source: str, default_date: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(i18n.t("rmap_results.bad_item"))
    allowed = {"config", "dataset", "metric", "value", "date", "run", "baseline", "better", "source", "note"}
    unknown = set(item) - allowed
    if unknown:
        raise ValueError(i18n.t("rmap_results.unknown_fields", fields=", ".join(sorted(unknown))))
    rec = model.RESULTS.clean({"source": source, "date": default_date, **item}, new=True)
    return {k: v for k, v in rec.items() if v not in (None, "", [])}


def build_report(slug: str, node_key: str, items: list[dict[str, Any]], *, status: str | None = None,
                 summary: str | None = None, source: str = "cli", day: str | None = None, with_numbers: bool = False) -> dict[str, Any]:
    """The ops for measurements reported on a node (and optionally the node's new status), plus a sentence
    for the log. Nothing is written: the CLI applies the ops, the assistant proposes them.
    with_numbers: a sentence given by the caller gets the numbers after it (a proposal lists them only as a count)."""
    if not items:
        raise ValueError(i18n.t("rmap_results.nothing_to_report"))
    m = _core(slug)
    node = _node(m, node_key)
    recs = [clean_item(i, source=source, default_date=day or today()) for i in items]
    ops_list, stats = upsert_ops(_existing(slug, m, node["id"]), node["id"], recs)
    if status and status != node.get("status"):
        if status not in map_model.STATUSES:
            raise ValueError(i18n.t("rmap_results.bad_status", allowed=", ".join(map_model.STATUSES)))
        ops_list.append({"op": "node.update", "id": node["id"], "set": {"status": status}})
    shown = "; ".join(model.line(r) for r in recs[:6]) + (f" (+{len(recs) - 6})" if len(recs) > 6 else "")
    sentence = i18n.t("rmap_results.reported", node=node["title"], lines=shown)
    if summary:
        sentence = f"{summary} ({shown})" if with_numbers else summary
    sentence = sentence[:500]
    return {"ops": ops_list, "summary": sentence, "node": node, **stats, "records": recs}


def report(slug: str, node_key: str, items: list[dict[str, Any]], *, status: str | None = None, summary: str | None = None,
           source: str = "cli", day: str | None = None, who: str = "cli") -> dict[str, Any]:
    """build_report, then write it (one atomic batch)."""
    with writing(slug), _lock:
        built = build_report(slug, node_key, items, status=status, summary=summary, source=source, day=day)
        _check_size(built["ops"])
        if not built["ops"]:
            return {**{k: built[k] for k in ("added", "updated", "unchanged")}, "node": built["node"]["id"], "applied": False}
        res = ops.apply(slug, built["ops"], who=who, summary=built["summary"])
        return {**{k: built[k] for k in ("added", "updated", "unchanged")}, "node": built["node"]["id"], "applied": True,
                "stamp": res["stamp"]}


def _differs(r: dict[str, Any], key: str, value: Any) -> bool:
    if key == "baseline":
        return bool(r.get("baseline")) != bool(value)
    return model.direction(r) != value if value else r.get("better") is not None  # better: set a direction, or clear it


def bulk_set(slug: str, fields: dict[str, Any], *, node: str | None = None, config: str | None = None,
             metric: str | None = None) -> dict[str, int]:
    """Set `baseline` (true / null) or `better` ("higher" / "lower" / null) on every result record of a node (or of
    the map) that matches config and metric. Goes in chunks of BULK_CHUNK operations (each chunk is atomic) because a
    person's batch is capped; a config with thousands of records is still one click."""
    unknown = set(fields) - {"baseline", "better"}
    if unknown or not fields:
        raise ValueError(i18n.t("rmap_results.unknown_fields", fields=", ".join(sorted(unknown)) or "-"))
    want = model.RESULTS.clean(fields, new=False)
    with writing(slug), _lock:
        m = _core(slug)
        nodes = [_node(m, node)["id"]] if node else [n["id"] for n in m["nodes"] if n.get("type") in model.NODE_TYPES]
        todo: list[dict[str, Any]] = []
        for nid in nodes:
            for r in _existing(slug, m, nid):
                if (config is not None and r.get("config") != config) or (metric is not None and (r.get("metric") or "").lower() != metric.lower()):
                    continue
                patch = {k: v for k, v in want.items() if _differs(r, k, v)}
                if patch:
                    todo.append({"op": "attach.update", "kind": "results", "id": r["id"], "set": patch})
        for i in range(0, len(todo), BULK_CHUNK):
            ops.apply(slug, todo[i:i + BULK_CHUNK], who="user")
        return {"updated": len(todo)}


# ---- watched files (per Mac: the state lives in the data dir, not in the vault) ----

def _state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _remember_state(slug: str, source_id: str, meta: dict[str, Any], error: str | None = None) -> None:
    with _flock(STATE_FILE.with_suffix(".lock")):  # read, change, write: two processes must not lose each other's rows
        state = _state()
        state[f"{slug}:{source_id}"] = {"mtime_ns": meta.get("mtime_ns"), "size": meta.get("size"), "error": error}
        tmp = STATE_FILE.with_name(f"{STATE_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, STATE_FILE)


def watched(slug: str | None = None) -> list[dict[str, Any]]:
    """The sources this Mac watches: {slug, id, node, path}. Cheap (one small file per project)."""
    out = []
    for s in [slug] if slug else project.slugs():
        p = project.get(s)
        if not (p.dir / model.SOURCES.filename).exists():
            continue
        for rec in p.kind_store(model.SOURCES).load()[model.SOURCES.name]:
            if rec.get("watch") and rec.get("device") == DEVICE:
                out.append({"slug": s, "id": rec["id"], "node": rec.get("node"), "path": rec.get("path"),
                            "mapping": rec.get("mapping")})
    return out


def _message(e: Exception) -> str:
    return str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e)


def sync(slug: str | None = None, *, force: bool = False) -> list[dict[str, Any]]:
    """Re-read every watched file whose size or time moved since the last read (all of them with force).
    One result per file read: {slug, source, path, added, updated} or {..., error}. A file that fails says why and
    the others are still read."""
    done = []
    with _lock:
        for w in watched(slug):
            state = _state().get(f"{w['slug']}:{w['id']}", {})
            try:
                p = tabular.resolve(w["path"])
            except ValueError as e:
                if state.get("error") != str(e):
                    _remember_state(w["slug"], w["id"], {}, str(e))
                    done.append({**_head(w), "error": str(e)})
                continue
            st = p.stat()
            if not force and state.get("mtime_ns") == st.st_mtime_ns and state.get("size") == st.st_size:
                continue
            meta = {"mtime_ns": st.st_mtime_ns, "size": st.st_size}
            try:
                r = run_import(w["slug"], w["node"], path=w["path"], mapping=w.get("mapping"), remember=False, who="import")
                _remember_state(w["slug"], w["id"], meta)
                done.append({**_head(w), "added": r["added"], "updated": r["updated"], "unchanged": r["unchanged"]})
            except Exception as e:  # a bad file (or a bug) must not stop the files after it
                if not isinstance(e, (KeyError, ValueError)):
                    log.exception("results file %s could not be imported", w["path"])
                msg = _message(e)
                _remember_state(w["slug"], w["id"], meta, msg)
                done.append({**_head(w), "error": msg})
    return done


def _head(w: dict[str, Any]) -> dict[str, Any]:
    return {"slug": w["slug"], "source": w["id"], "path": w["path"]}


def source_states(slug: str) -> dict[str, dict[str, Any]]:
    """{source id: {error}} for the page: a watched file that could not be read says why."""
    return {sid.split(":", 1)[1]: {"error": v.get("error")} for sid, v in _state().items() if sid.startswith(slug + ":") and v.get("error")}


__all__ = ["preview", "run_import", "report", "build_report", "bulk_set", "sync", "watched", "upsert_ops", "source_states", "writing"]
