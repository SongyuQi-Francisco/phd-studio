"""「立即巡检」: run the weekly research_tracker (fetch → ingest → render → audit) in the background.

Same commands and window as the tracker's weekly runbook: RUN = today (local date), SINCE = the
latest run date in runs/. The weekly review (the ANALYSIS block) stays a Claude step; the page
offers to hand it to the assistant when the run is done. One run at a time: a second start is
refused while a run (from this process or another one, via a pid file) is going.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta
from typing import Any

from . import tracking
from .config import DATA_DIR, TRACKER_DIR

PID_FILE = DATA_DIR / "tracker-run.pid"
_lock = threading.Lock()
_state: dict[str, Any] = {"running": False}


class Busy(RuntimeError):
    pass


def _pid_alive() -> bool:
    try:
        if datetime.now().timestamp() - PID_FILE.stat().st_mtime > 2 * 3600:  # stale: no run takes that long
            return False
        pid = int(PID_FILE.read_text())
        os.kill(pid, 0)
        return pid != os.getpid() or _state.get("running", False)
    except (OSError, ValueError):
        return False


def status() -> dict[str, Any]:
    s = dict(_state)
    s["log"] = s.get("log", [])[-14:]
    s["available"] = bool(TRACKER_DIR and (TRACKER_DIR / "tracker.py").exists())
    s["linkage"] = tracking.linkage()
    return s


def _window() -> tuple[str, str]:
    run = date.today().isoformat()
    runs = sorted(p.name for p in (TRACKER_DIR / "runs").glob("*") if p.is_dir()) if TRACKER_DIR else []
    since = (date.fromisoformat(run) - timedelta(days=8)).isoformat()
    if runs:
        since = runs[-1]
        if since >= run:  # already ran today: re-run the same window
            try:
                since = json.loads((TRACKER_DIR / "runs" / runs[-1] / "candidates.json").read_text())["since"]
            except (OSError, KeyError, json.JSONDecodeError):
                since = (date.fromisoformat(run) - timedelta(days=8)).isoformat()
    return run, since


def start() -> dict[str, Any]:
    if not TRACKER_DIR or not (TRACKER_DIR / "tracker.py").exists():
        raise FileNotFoundError("没有配置 research_tracker（studio.toml 的 tracker）")
    with _lock:
        if _state.get("running") or _pid_alive():
            raise Busy("巡检已在进行中，等这次跑完再说")
        run, since = _window()
        _state.clear()
        _state.update(running=True, run=run, since=since, stage="prepare", queries=0, queries_done=0,
                      started=datetime.now().isoformat(timespec="seconds"), finished=None, ok=None, error=None,
                      log=[], stats=None, audit=None)
        PID_FILE.write_text(str(os.getpid()))
    threading.Thread(target=_run, args=(run, since), daemon=True).start()
    return status()


def _cmd(*args: str) -> list[str]:
    return [sys.executable, "-u", str(TRACKER_DIR / args[0]), *args[1:]]


def _log(line: str) -> None:
    line = line.rstrip()
    if line:
        _state["log"] = (_state.get("log", []) + [line])[-200:]


def _stream(cmd: list[str], on_line: Any = None, timeout: int = 1800) -> tuple[int, str]:
    p = subprocess.Popen(cmd, cwd=TRACKER_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         env={**os.environ, "PYTHONUNBUFFERED": "1"})
    out = []
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    try:
        for line in p.stdout:  # type: ignore[union-attr]
            out.append(line)
            _log(line)
            if on_line:
                on_line(line)
        return p.wait(), "".join(out)
    finally:
        timer.cancel()


def _run(run: str, since: str) -> None:
    try:
        tracking.export_follows()
        q = subprocess.run(_cmd("tracker.py", "queries"), cwd=TRACKER_DIR, capture_output=True, text=True, timeout=60)
        _state["queries"] = len([x for x in q.stdout.splitlines() if x.strip()])

        _state["stage"] = "fetch"

        def tick(line: str) -> None:
            if line.startswith("query done"):
                _state["queries_done"] += 1
        code, out = _stream(_cmd("tracker.py", "fetch", "--since", since, "--run", run), tick)
        if code:
            raise RuntimeError(out.strip().splitlines()[-1] if out.strip() else f"fetch 失败（{code}）")

        _state["stage"] = "ingest"
        code, out = _stream(_cmd("tracker.py", "ingest", "--records", f"raw/{run}.json", "--since", since, "--run", run))
        if code:
            raise RuntimeError(f"ingest 失败（{code}）")
        try:
            _state["stats"] = json.loads(out[out.index("{"):])
        except ValueError:
            pass

        _state["stage"] = "render"
        code, out = _stream(_cmd("tracker.py", "render", "--run", run))
        if code:
            raise RuntimeError(f"render 失败（{code}）")

        _state["stage"] = "audit"
        if (TRACKER_DIR / "vault_audit.py").exists():
            a = subprocess.run(_cmd("vault_audit.py"), cwd=TRACKER_DIR, capture_output=True, text=True, timeout=300)
            _state["audit"] = (a.stdout.strip().splitlines() or [""])[0][:300]
        _state.update(ok=True, stage="done")
    except Exception as e:  # report every failure on the page instead of killing the thread
        _state.update(ok=False, error=str(e)[:400], stage="failed")
    finally:
        _state.update(running=False, finished=datetime.now().isoformat(timespec="seconds"))
        PID_FILE.unlink(missing_ok=True)
