"""Bilingual PDFs made ahead of time for the papers you are about to read.

When a reading-list item becomes T1 (or T1/T2, setting bilingual_prefetch), its paper joins a queue,
and a background worker translates one paper at a time through bilingual.start. The worker starts a
job only when no bilingual job is running at all, so it never runs next to one you started from the
reader, and a paper never gets two jobs. Papers that already have a bilingual PDF are skipped.

The queue is per device, in the data dir (never in the synced vault). It survives restarts (a job cut
off by quitting goes back to the front of the queue) and can be paused. Like Zotero auto mode, the
first pass only records the list, so turning this on never back-fills; Settings offers that as a button.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import bilingual, reading
from .config import DATA_DIR, READING_LIST, load_settings, secret

QUEUE_FILE = DATA_DIR / "bilingual-queue.json"
MODES: dict[str, tuple[str, ...]] = {"off": (), "T1": ("T1",), "T1T2": ("T1", "T2")}
KEEP_FINISHED = 20
_lock = threading.RLock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def mode() -> str:
    m = load_settings().get("bilingual_prefetch", "T1T2")
    return m if m in MODES else "off"


def _load() -> dict[str, Any]:
    try:
        st = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st.setdefault("paused", False)
    st.setdefault("jobs", [])
    return st


def _save(st: dict[str, Any]) -> None:
    done = [j for j in st["jobs"] if j["state"] not in ("queued", "running")]
    keep = {id(j) for j in done[-KEEP_FINISHED:]}
    st["jobs"] = [j for j in st["jobs"] if j["state"] in ("queued", "running") or id(j) in keep]
    tmp = QUEUE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, QUEUE_FILE)
    _publish(st)


def _publish(st: dict[str, Any]) -> None:
    """Tell bilingual.status which papers are waiting, so the reader's 双语 button can say so."""
    hold = st["paused"] or mode() == "off"
    queued = [j["key"] for j in st["jobs"] if j["state"] == "queued"]
    bilingual.QUEUED.clear()
    bilingual.QUEUED.update({k: {"position": n, "paused": hold} for n, k in enumerate(queued, 1)})


def _qualifies(item: dict[str, Any] | None, tiers: tuple[str, ...]) -> bool:
    return bool(item) and item["status"] in ("queued", "reading") and item.get("tier") in tiers


def _title(item: dict[str, Any]) -> str:
    return item.get("title") or item.get("paper") or reading.key_of(item)


def _active(st: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((j for j in st["jobs"] if j["key"] == key and j["state"] in ("queued", "running")), None)


def _enqueue(st: dict[str, Any], item: dict[str, Any], why: str) -> bool:
    key = reading.key_of(item)
    if _active(st, key) or bilingual.output(key, "dual") or key in bilingual.running():
        return False
    st["jobs"].append({"key": key, "title": _title(item), "reason": why, "added": _now(), "state": "queued"})
    return True


def _reconcile(st: dict[str, Any]) -> bool:
    """Queue papers whose item became T1/T2 since the last look (the first look only records)."""
    stamp = _mtime(READING_LIST)
    if st.get("stamp") == stamp and "seen" in st:
        return False
    items = reading._load_raw()
    tiers = MODES[mode()]
    seen = st.get("seen")
    if seen is not None and tiers:
        for i in items:
            prev = seen.get(i["id"])
            was = prev and _qualifies({"status": prev[0], "tier": prev[1]}, tiers)
            if _qualifies(i, tiers) and not was:
                _enqueue(st, i, f"进入 {i['tier']}")
    st["seen"] = {i["id"]: [i["status"], i.get("tier")] for i in items}
    st["stamp"] = stamp
    return True


def tick() -> None:
    """One step of the worker: notice tier changes, settle the finished job, start the next one."""
    if bilingual._stopping.is_set() or _stop.is_set():
        return
    nxt = None
    with _lock:
        st = _load()
        changed = _reconcile(st)
        for j in st["jobs"]:
            if j["state"] == "running":
                job = bilingual._jobs.get(j["key"])
                if job is None or job["state"] == "running":
                    continue
                j.update(state="done" if job["state"] == "done" else "error", finished=_now(), engine=job.get("engine"))
                if job["state"] != "done":
                    j["error"] = job.get("error") or "失败"
                changed = True
            elif j["state"] == "queued" and bilingual.output(j["key"], "dual"):
                j.update(state="skipped", finished=_now(), error="已有双语版")
                changed = True
        busy = bilingual.running() or any(j["state"] == "running" for j in st["jobs"])
        tiers = MODES[mode()]
        if not st["paused"] and tiers and not busy and any(j["state"] == "queued" for j in st["jobs"]):
            items = {reading.key_of(i): i for i in reading._load_raw()}
            for j in st["jobs"]:
                if j["state"] != "queued":
                    continue
                if not _qualifies(items.get(j["key"]), tiers):
                    j.update(state="skipped", finished=_now(), error="已不在 " + "/".join(tiers))
                    changed = True
                    continue
                j.update(state="running", started=_now())
                nxt = j["key"]
                changed = True
                break
        if changed:
            _save(st)
    if nxt:
        try:
            bilingual.start(nxt, source="queue")
        except Exception as e:  # noqa: BLE001 - no PDF, or pdf2zh missing: shown in Settings
            with _lock:
                st = _load()
                if j := _active(st, nxt):
                    j.update(state="error", finished=_now(), error=str(e)[:300])
                    _save(st)


# ---------- for the routes ----------


def snapshot() -> dict[str, Any]:
    with _lock:
        st = _load()
        _publish(st)
    label, _ = bilingual.engine()
    tiers = MODES[mode()]
    running = next((j for j in st["jobs"] if j["state"] == "running"), None)
    if running:
        job = bilingual.status(running["key"])
        running = {**running, "progress": job.get("progress", 0), "started_ts": job.get("started")}
    finished = [j for j in st["jobs"] if j["state"] not in ("queued", "running")]
    queued_keys = {j["key"] for j in st["jobs"] if j["state"] in ("queued", "running")}
    backfill = [i for i in reading._load_raw() if _qualifies(i, tiers) and reading.key_of(i) not in queued_keys
                and not bilingual.output(reading.key_of(i), "dual")]
    return {"mode": mode(), "paused": st["paused"], "engine": label,
            "deepseek_key": bool(secret("DEEPSEEK_API_KEY", "deepseek")),
            "engine_setting": load_settings().get("translate_engine", "deepseek"),
            "running": running, "queued": [j for j in st["jobs"] if j["state"] == "queued"],
            "recent": finished[::-1][:8], "busy": [k for k in bilingual.running() if not running or k != running["key"]],
            "backfill": len(backfill)}


def set_paused(paused: bool) -> dict[str, Any]:
    with _lock:
        st = _load()
        st["paused"] = bool(paused)
        _save(st)
    return snapshot()


def backfill() -> int:
    """Queue every current T1/T2 paper (by the setting) that has no bilingual PDF yet."""
    tiers = MODES[mode()]
    with _lock:
        st = _load()
        n = sum(_enqueue(st, i, f"补齐 {i['tier']}") for i in reading._load_raw() if _qualifies(i, tiers))
        _save(st)
    return n


def remove(key: str) -> bool:
    """Take a waiting (or failed) paper out of the queue; a running job is left to finish."""
    with _lock:
        st = _load()
        before = len(st["jobs"])
        st["jobs"] = [j for j in st["jobs"] if not (j["key"] == key and j["state"] != "running")]
        _save(st)
        return len(st["jobs"]) != before


def retry(key: str) -> bool:
    with _lock:
        st = _load()
        failed = next((j for j in reversed(st["jobs"]) if j["key"] == key and j["state"] == "error"), None)
        if not failed or _active(st, key):
            return False
        failed.update(state="queued", added=_now(), reason="重试")
        failed.pop("error", None)
        st["jobs"].remove(failed)
        st["jobs"].append(failed)
        _save(st)
        return True


# ---------- worker thread ----------

_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    while not _stop.wait(2):
        try:
            tick()
        except Exception as e:  # noqa: BLE001 - keep the worker alive; the next tick tries again
            print(f"[bilingual queue] {e.__class__.__name__}: {e}")


def start_worker() -> None:
    """On startup: a job cut off by the last quit goes back to the front of the queue."""
    global _thread
    with _lock:
        st = _load()
        cut = [j for j in st["jobs"] if j["state"] == "running"]
        for j in cut:
            j["state"] = "queued"
        st["jobs"] = cut + [j for j in st["jobs"] if all(j is not c for c in cut)]
        _save(st)
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="bilingual-queue", daemon=True)
    _thread.start()


def stop_worker() -> None:
    """On shutdown: stop picking up work and end the queue's own pdf2zh process (the queue file still
    says running, so it is redone after the next start)."""
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
    for key in bilingual.running():
        job = bilingual._jobs.get(key) or {}
        proc = job.get("proc")
        if job.get("source") == "queue" and proc and proc.poll() is None:
            proc.terminate()
