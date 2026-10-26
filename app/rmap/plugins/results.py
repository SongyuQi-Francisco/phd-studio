"""Experiment results (#48): the `results` and `result_sources` attachment kinds, and the routes for pasting or
importing a CSV / JSON table, listing candidate files, and reading the watched ones. The page side is
web/rmap/ext/results.js; the command line is app/rmap/cli.py; the assistant's tool is in app/studio_tools.py."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ... import i18n
from ...config import DEVICE
from .. import project, registry
from ..results import importer, model, tabular, watcher

registry.attachment_kind(model.RESULTS)
registry.attachment_kind(model.SOURCES)

router = APIRouter(prefix="/api/rmap")
_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", ".idea"}
_MAX_SCAN = 20000


def _run(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]) if e.args else "not found")
    except (ValueError, OverflowError) as e:
        raise HTTPException(400, str(e))


@router.post("/projects/{slug}/results/preview")
def preview(slug: str, body: dict[str, Any]) -> dict[str, Any]:
    """Columns, a few rows and the mapping to start from, for pasted text (`text`) or a file (`path`)."""
    return _run(importer.preview, slug, text=body.get("text"), path=body.get("path"), fmt=body.get("format"), node=body.get("node"))


@router.post("/projects/{slug}/results/import")
def import_table(slug: str, body: dict[str, Any]) -> dict[str, Any]:
    """Upsert a table onto a node as one atomic batch. dry_run: check it and say what would change."""
    r = _run(importer.run_import, slug, body.get("node") or "", text=body.get("text"), path=body.get("path"),
             fmt=body.get("format"), mapping=body.get("mapping"), remember=body.get("remember", True),
             watch=body.get("watch"), label=body.get("label"), default_date=body.get("date"),
             who="import", dry_run=bool(body.get("dry_run")))
    if body.get("watch") and not body.get("dry_run"):
        watcher.start()
    return r


@router.post("/projects/{slug}/results/set")
def set_flags(slug: str, body: dict[str, Any]) -> dict[str, Any]:
    """Mark a config as a baseline (`baseline`) or say which way a metric improves (`better`) on every matching result
    record, however many: {node?, config?, metric?, set: {baseline | better}}."""
    return _run(importer.bulk_set, slug, body.get("set") or {}, node=body.get("node"), config=body.get("config"), metric=body.get("metric"))


@router.post("/projects/{slug}/results/sync")
def sync(slug: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read the files this Mac watches for this project now (force: even when they did not change)."""
    _run(project.get, slug)
    return {"synced": _run(importer.sync, slug, force=bool((body or {}).get("force")))}


@router.get("/projects/{slug}/results/status")
def status(slug: str) -> dict[str, Any]:
    """Which sources this Mac watches, whether the watcher runs, and why a watched file could not be read."""
    _run(project.get, slug)
    mine = _run(importer.watched, slug)
    if mine:
        watcher.start()
    return {"watching": [w["id"] for w in mine], "running": watcher.running(), "errors": importer.source_states(slug), "device": DEVICE}


@router.get("/results/files")
def files(q: str = "") -> dict[str, Any]:
    """CSV / JSON files under the results roots (`[paths] results_roots`), newest first, for picking one instead of
    typing a path. Without roots there is nothing to list and nothing can be imported from a file."""
    q = q.strip().lower()
    found: list[tuple[float, os.stat_result, str]] = []
    seen = 0
    roots = tabular.roots()
    for root in roots:
        base_depth = str(root).rstrip(os.sep).count(os.sep)
        for dirpath, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".") and dirpath.count(os.sep) - base_depth < 4]
            for name in names:
                seen += 1
                full = os.path.join(dirpath, name)
                if os.path.splitext(name)[1].lower() not in tabular.EXT_FORMAT or (q and q not in full.lower()):
                    continue
                try:
                    st = os.stat(full)
                    tabular.resolve(full)
                except (OSError, ValueError):
                    continue
                found.append((st.st_mtime, st, full))
            if seen > _MAX_SCAN:
                break
    found.sort(key=lambda x: x[0], reverse=True)
    return {"roots": [tabular.collapse(r) for r in roots],
            "files": [{"path": tabular.collapse(Path(f)), "size": st.st_size, "mtime": int(mt)} for mt, st, f in found[:60]],
            "message": None if roots else i18n.t("rmap_results.no_roots")}
