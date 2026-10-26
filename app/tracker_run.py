"""「立即巡检」: run the weekly research_tracker (fetch → ingest → render → audit) in the background.

Same commands and window as the tracker's weekly runbook: RUN = today (local date), SINCE = the
latest run date in runs/. The weekly review (the ANALYSIS block) stays a Claude step; the page
offers to hand it to the assistant when the run is done.

- Interpreter: the project's venv Python (else python3 on PATH), never sys.executable: inside the
  native app bundle that is the launcher stub, which would only re-open the window. The stub's
  PYTHONHOME / PYTHONNOUSERSITE are dropped from the child's environment.
- One run at a time, across app restarts too: a run holds an exclusive flock on
  DATA_DIR/tracker-run.lock that every tracker child inherits, so the lock lives as long as any
  step is still running, even if the app quit. A start is also refused while any other
  `tracker.py fetch|ingest|render` process is alive (e.g. the scheduled weekly run).
- When the app exits, the running step is terminated (its whole process group).
"""
from __future__ import annotations

import atexit
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import threading
from datetime import date, datetime, timedelta
from typing import Any

from . import i18n, msgref, tracking
from .config import DATA_DIR, STUDIO_DIR, TRACKER_DIR

LOCK_FILE = DATA_DIR / "tracker-run.lock"
INFO_FILE = DATA_DIR / "tracker-run.json"  # what the lock holder is doing, for status after a restart
_ENV_DROP = ("PYTHONHOME", "PYTHONNOUSERSITE", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__")
_TRACKER_PROC = re.compile(r"tracker\.py\s+(fetch|ingest|render|browser-js)\b")
_lock = threading.Lock()
_state: dict[str, Any] = {"running": False}
_child: dict[str, Any] = {"proc": None, "fd": None, "stop": False}


class Busy(RuntimeError):
    pass


class RunError(RuntimeError):
    """A failed step. Its message is a stored reference (msgref), worded when `status()` is read, so the
    banner of a failed run follows the interface language if that changes before the next run."""

    def __init__(self, key: str, /, **params: object):
        super().__init__(msgref.ref(key, **params))


def python_for_tracker() -> str:
    venv = STUDIO_DIR / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    found = shutil.which("python3")
    if not found:
        raise FileNotFoundError(i18n.t("track.run.no_python"))
    return found


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _ENV_DROP}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _try_lock() -> int | None:
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _lock_held_elsewhere() -> bool:
    fd = _try_lock()
    if fd is None:
        return True
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    return False


def _other_tracker_procs() -> list[str]:
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if _TRACKER_PROC.search(line)]


def status() -> dict[str, Any]:
    s = dict(_state)
    s["log"] = s.get("log", [])[-14:]
    if "error" in s:
        s["error"] = msgref.show(s["error"])
    s["available"] = bool(TRACKER_DIR and (TRACKER_DIR / "tracker.py").exists())
    s["linkage"] = tracking.linkage()
    if not s.get("running") and _lock_held_elsewhere():  # a run from before an app restart is still going
        try:
            info = json.loads(INFO_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            info = {}
        s.update(running=True, external=True, stage=info.get("stage", "running"), run=info.get("run"),
                 since=info.get("since"), queries=0, queries_done=0)
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
        raise FileNotFoundError(i18n.t("track.run.not_configured"))
    python_for_tracker()
    with _lock:
        if _state.get("running"):
            raise Busy(i18n.t("track.run.busy_running"))
        fd = _try_lock()
        if fd is None:
            raise Busy(i18n.t("track.run.busy_lock"))
        if _other_tracker_procs():
            os.close(fd)
            raise Busy(i18n.t("track.run.busy_proc"))
        run, since = _window()
        _state.clear()
        _state.update(running=True, run=run, since=since, stage="prepare", queries=0, queries_done=0,
                      started=datetime.now().isoformat(timespec="seconds"), finished=None, ok=None, error=None,
                      log=[], stats=None, audit=None)
        _child.update(fd=fd, stop=False, proc=None)
        _write_info()
    threading.Thread(target=_run, args=(run, since), daemon=True).start()
    return status()


def _write_info() -> None:
    info = {k: _state.get(k) for k in ("run", "since", "stage", "started")}
    info["server_pid"] = os.getpid()
    proc = _child.get("proc")
    info["child_pid"] = proc.pid if proc else None
    try:
        INFO_FILE.write_text(json.dumps(info), encoding="utf-8")
    except OSError:
        pass


def _cmd(*args: str) -> list[str]:
    return [python_for_tracker(), "-u", str(TRACKER_DIR / args[0]), *args[1:]]


def _log(line: str) -> None:
    line = line.rstrip()
    if line:
        _state["log"] = (_state.get("log", []) + [line])[-200:]


def _stream(cmd: list[str], on_line: Any = None, timeout: int = 1800) -> tuple[int, str]:
    if _child["stop"]:
        raise RunError("track.run.stopped")
    fd = _child["fd"]
    p = subprocess.Popen(cmd, cwd=TRACKER_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         env=_child_env(), pass_fds=(fd,) if fd is not None else (), start_new_session=True)
    _child["proc"] = p
    _write_info()
    out = []
    timer = threading.Timer(timeout, _terminate)
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
        _child["proc"] = None


def _terminate() -> None:
    p = _child.get("proc")
    if not p or p.poll() is not None:
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass


@atexit.register
def stop() -> None:
    """App is quitting: no further steps, and the running one goes down with it."""
    _child["stop"] = True
    _terminate()


def _stage(name: str) -> None:
    _state["stage"] = name
    _write_info()


def _run(run: str, since: str) -> None:
    try:
        tracking.export_follows()
        code, out = _stream(_cmd("tracker.py", "queries"), timeout=60)
        if code:
            raise RunError("track.run.step_failed", step="queries", code=code)
        _state["queries"] = len([x for x in out.splitlines() if x.strip()])

        _stage("fetch")

        def tick(line: str) -> None:
            if line.startswith("query done"):
                _state["queries_done"] += 1
        code, out = _stream(_cmd("tracker.py", "fetch", "--since", since, "--run", run), tick)
        if code:
            if out.strip():
                raise RuntimeError(out.strip().splitlines()[-1])
            raise RunError("track.run.step_failed", step="fetch", code=code)
        if not (TRACKER_DIR / "raw" / f"{run}.json").exists():
            raise RunError("track.run.no_raw")

        _stage("ingest")
        code, out = _stream(_cmd("tracker.py", "ingest", "--records", f"raw/{run}.json", "--since", since, "--run", run))
        if code:
            raise RunError("track.run.step_failed", step="ingest", code=code)
        try:
            _state["stats"] = json.loads(out[out.index("{"):])
        except ValueError:
            raise RunError("track.run.no_stats") from None

        _stage("render")
        code, out = _stream(_cmd("tracker.py", "render", "--run", run))
        if code or "rendered run" not in out:
            raise RunError("track.run.step_failed", step="render", code=code)

        _stage("audit")
        if (TRACKER_DIR / "vault_audit.py").exists():
            _, out = _stream(_cmd("vault_audit.py"), timeout=300)  # exits 1 when links are missing
            _state["audit"] = (out.strip().splitlines() or [""])[0][:300]
        _state.update(ok=True, stage="done")
    except Exception as e:  # report every failure on the page instead of killing the thread
        _state.update(ok=False, error=str(e) if isinstance(e, RunError) else str(e)[:400], stage="failed")
    finally:
        _state.update(running=False, finished=datetime.now().isoformat(timespec="seconds"))
        fd = _child.get("fd")
        _child.update(fd=None, proc=None)
        INFO_FILE.unlink(missing_ok=True)
        if fd is not None:
            os.close(fd)  # releases the lock (children that inherited it are gone by now)
