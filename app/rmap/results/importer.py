"""Results into a map, always through ops.apply: one atomic batch per import, per CLI report, per confirmed
proposal. Importing the same table twice changes nothing (a measurement is told apart by its config, dataset,
metric, run, date and source), so a watched file can be read again whenever it changes.

    preview(slug, ...)        what a table looks like and the mapping to use; writes nothing
    run_import(slug, node, ...)  upsert a table (pasted, or a file) onto an experiment / result node
    build_report(slug, node, items, ...)  the ops for a few measurements a script or the assistant reports
    sync(...)                 re-read the files this Mac watches (see watcher.py)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any

from ... import i18n
from ...config import DATA_DIR, DEVICE
from .. import model as map_model, ops, project
from . import mapping as maps, model, refs, tabular

log = logging.getLogger(__name__)
SAMPLE_ROWS = 8
STATE_FILE = DATA_DIR / "rmap" / "results-watch.json"
_lock = threading.RLock()


def today() -> str:
    return date.today().isoformat()


def key(rec: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """What tells two measurements apart (per node and source)."""
    return (rec.get("config") or "", rec.get("dataset") or "", rec.get("metric") or "", rec.get("run") or "", rec.get("date") or "")


def upsert_ops(m: dict[str, Any], node_id: str, recs: list[dict[str, Any]], source: str | None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """attach.add for new measurements and attach.update for the ones whose value changed (same source only)."""
    have = {key(a): a for a in m["attachments"]["results"] if a.get("node") == node_id and (a.get("source") or None) == (source or None)}
    seen: dict[tuple, dict[str, Any]] = {}
    for r in recs:
        seen[key(r)] = r  # a later row of the same measurement wins
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
        for flag in ("baseline", "better"):  # only ever set from an import: a baseline ticked in the page stays ticked
            if r.get(flag) and r[flag] != old.get(flag):
                patch[flag] = r[flag]
        if patch:
            out.append({"op": "attach.update", "kind": "results", "id": old["id"], "set": patch})
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    return out, stats


def _node(m: dict[str, Any], key_: str) -> dict[str, Any]:
    return refs.find_node(m, key_, types=model.NODE_TYPES)


# ---- preview ----

def preview(slug: str, *, text: str | None = None, path: str | None = None, fmt: str | None = None) -> dict[str, Any]:
    """The columns, a few rows, the mapping to start from (the remembered one when there is one) and the configs."""
    m = project.get(slug).read()
    table = tabular.read_file(path) if path else tabular.parse(text or "", fmt)
    shown = table.meta.get("path")
    remembered = maps.remembered(table, m["attachments"]["result_sources"], shown)
    guess = remembered or maps.guess(table)
    try:
        cfgs = maps.configs(table, guess) if guess.get("config") else []
    except ValueError:
        cfgs = []
    return {"format": table.format, "columns": table.columns, "rows": table.rows[:SAMPLE_ROWS], "total": len(table.rows),
            "truncated": table.truncated, "mapping": guess, "remembered": bool(remembered), "configs": cfgs[:60],
            "file": table.meta or None}


# ---- import ----

def _source_op(m: dict[str, Any], node_id: str, table: tabular.Table, mapping: dict[str, Any], watch: bool | None) -> dict[str, Any] | None:
    shown = table.meta["path"]
    fields: dict[str, Any] = {"path": shown, "format": table.format, "columns": table.columns, "mapping": mapping}
    if watch is not None:
        fields["watch"] = True if watch else None
        fields["device"] = DEVICE if watch else None
    old = next((s for s in m["attachments"]["result_sources"] if s.get("node") == node_id and s.get("path") == shown), None)
    if old is None:
        return {"op": "attach.add", "ref": "$source", "kind": "result_sources", "node": node_id, "set": {k: v for k, v in fields.items() if v is not None}}
    patch = {k: v for k, v in fields.items() if old.get(k) != v}
    return {"op": "attach.update", "kind": "result_sources", "id": old["id"], "set": patch} if patch else None


def run_import(slug: str, node_key: str, *, text: str | None = None, path: str | None = None, fmt: str | None = None,
               mapping: dict[str, Any] | None = None, remember: bool = True, watch: bool | None = None,
               label: str | None = None, default_date: str | None = None, who: str = "import", dry_run: bool = False,
               summary: str | None = None) -> dict[str, Any]:
    """Upsert a table onto an experiment / result node as one atomic batch. Returns the counts
    (added, updated, unchanged, skipped), what was skipped, and for a real import the source record's id.
    watch: True / False sets whether this Mac watches the file (None leaves it as it is)."""
    with _lock:
        m = project.get(slug).read()
        node = _node(m, node_key)
        table = tabular.read_file(path) if path else tabular.parse(text or "", fmt)
        shown = table.meta.get("path")
        chosen = maps.clean(mapping) if mapping else (maps.remembered(table, m["attachments"]["result_sources"], shown) or maps.guess(table))
        source = shown if path else (label or "paste")
        day = model.day(default_date) or (table.meta.get("date") if path else today())
        recs, skipped = maps.records(table, chosen, default_date=day, source=source)
        if not recs:
            raise ValueError(i18n.t("rmap_results.no_rows"))
        ops_list, stats = upsert_ops(m, node["id"], recs, source)
        src = _source_op(m, node["id"], table, chosen, watch) if path and remember else None
        if src:
            ops_list.append(src)
        out = {**stats, "skipped": len(skipped), "skipped_rows": skipped[:10], "total": len(recs), "node": node["id"],
               "source": source, "truncated": table.truncated, "mapping": chosen,
               "configs": list(dict.fromkeys(r["config"] for r in recs))[:60]}
        if not ops_list:
            return out
        sentence = summary or i18n.t("rmap_results.imported", n=stats["added"] + stats["updated"], source=Path(source).name if path else source)
        if stats["added"] + stats["updated"] == 0:
            sentence = None  # only the source record changed: no log line
        if dry_run:
            ops.simulate(slug, ops_list, who=who, summary=sentence)
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
    m = project.get(slug).read()
    node = _node(m, node_key)
    recs = [clean_item(i, source=source, default_date=day or today()) for i in items]
    ops_list, stats = upsert_ops(m, node["id"], recs, source)
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
    with _lock:
        built = build_report(slug, node_key, items, status=status, summary=summary, source=source, day=day)
        if not built["ops"]:
            return {**{k: built[k] for k in ("added", "updated", "unchanged")}, "node": built["node"]["id"], "applied": False}
        res = ops.apply(slug, built["ops"], who=who, summary=built["summary"])
        return {**{k: built[k] for k in ("added", "updated", "unchanged")}, "node": built["node"]["id"], "applied": True,
                "stamp": res["stamp"]}


# ---- watched files (per Mac: the state lives in the data dir, not in the vault) ----

def _state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def _remember_state(slug: str, source_id: str, meta: dict[str, Any], error: str | None = None) -> None:
    state = _state()
    state[f"{slug}:{source_id}"] = {"mtime_ns": meta.get("mtime_ns"), "size": meta.get("size"), "error": error}
    _save_state(state)


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


def sync(slug: str | None = None, *, force: bool = False) -> list[dict[str, Any]]:
    """Re-read every watched file whose size or time moved since the last read (all of them with force).
    One result per file read: {slug, source, path, added, updated} or {..., error}."""
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
            try:
                r = run_import(w["slug"], w["node"], path=w["path"], mapping=w.get("mapping"), remember=False, who="import",
                               summary=None)
                _remember_state(w["slug"], w["id"], {"mtime_ns": st.st_mtime_ns, "size": st.st_size})
                done.append({**_head(w), "added": r["added"], "updated": r["updated"], "unchanged": r["unchanged"]})
            except (KeyError, ValueError) as e:
                msg = str(e.args[0]) if e.args else str(e)
                _remember_state(w["slug"], w["id"], {"mtime_ns": st.st_mtime_ns, "size": st.st_size}, msg)
                done.append({**_head(w), "error": msg})
    return done


def _head(w: dict[str, Any]) -> dict[str, Any]:
    return {"slug": w["slug"], "source": w["id"], "path": w["path"]}


def source_states(slug: str) -> dict[str, dict[str, Any]]:
    """{source id: {error}} for the page: a watched file that could not be read says why."""
    return {sid.split(":", 1)[1]: {"error": v.get("error")} for sid, v in _state().items() if sid.startswith(slug + ":") and v.get("error")}


__all__ = ["preview", "run_import", "report", "build_report", "sync", "watched", "upsert_ops", "source_states"]
