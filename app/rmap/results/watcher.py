"""Re-reads the results files this Mac watches (a source with `watch` and this device's name), whenever their
size or time moved. A background thread, started the first time a results endpoint is used (the research map
page asks when it opens) and by `studio map result sync --loop`; each pass is a stat per watched file."""
from __future__ import annotations

import logging
import threading

from . import importer

log = logging.getLogger(__name__)
INTERVAL = 8.0
_stop = threading.Event()
_thread: threading.Thread | None = None
_guard = threading.Lock()


def _run() -> None:
    while not _stop.wait(INTERVAL):
        try:
            importer.sync()
        except Exception:  # never let the watcher die
            log.exception("results watcher round failed")


def start() -> None:
    global _thread
    with _guard:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run, name="rmap-results-watch", daemon=True)
        _thread.start()


def stop() -> None:
    _stop.set()


def running() -> bool:
    return bool(_thread and _thread.is_alive() and not _stop.is_set())
