"""Background ingest: every few seconds, look for research maps whose files changed (the other Mac synced
something in, or the CLI wrote) and ingest their new events, so event subscribers hear about them even
when no map page is open. Cheap: one stat per YAML file per round."""
from __future__ import annotations

import logging
import threading

from ..config import RMAP_DIR
from . import project

log = logging.getLogger(__name__)
INTERVAL = 5.0
_stop = threading.Event()
_thread: threading.Thread | None = None


def once(seen: dict[str, str]) -> list[str]:
    """One round: ingest every project whose change stamp moved since `seen`; returns their slugs."""
    moved = []
    for slug in project.slugs():
        st = project.stamp_of(RMAP_DIR / slug)
        if seen.get(slug) != st:
            try:
                project.get(slug).ingest()
            except (KeyError, OSError, ValueError):
                log.exception("research map ingest failed for %s", slug)
            seen[slug] = project.stamp_of(RMAP_DIR / slug)
            moved.append(slug)
    return moved


def _run() -> None:
    seen: dict[str, str] = {}
    while not _stop.wait(INTERVAL):
        try:
            once(seen)
        except Exception:  # never let the watcher die
            log.exception("research map watcher round failed")


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="rmap-watch", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
