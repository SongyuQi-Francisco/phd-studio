"""The LaunchAgent that runs the reminder check a few times a day, with or without the workbench open.

~/Library/LaunchAgents/local.phd-studio.reminders.plist. Installed and removed only from the button in
Settings (POST /api/reminders/agent), never automatically: it changes the user's system setup.

What it runs:
- PhD Studio.app's own executable, when an app built from this checkout exists. The stub ignores its
  arguments, so the task goes in PHD_STUDIO_TASK (app.desktop reads it). Running as the app matters
  twice: macOS treats the process as the app, so the app's permission to read the Desktop folder
  (project and vault live there) applies, where a bare Python started by launchd would need its own;
  and notifications carry the app's name and icon, and a click opens the app.
- Otherwise the project venv's Python: `.venv/bin/python -m app.cli remind --check`. Never
  sys.executable: inside the desktop app that is the native stub, which would just open a window.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import STUDIO_DIR

LABEL = "local.phd-studio.reminders"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LOG = Path.home() / "Library" / "Logs" / "PhD Studio Reminders.log"
CHECK_TIMES = ((9, 0), (13, 0), (18, 0))
TASK = "remind --check"
APP_NAMES = ("PhD Studio.app",)


def plist_path() -> Path:
    return AGENTS_DIR / f"{LABEL}.plist"


def venv_python() -> Path:
    return STUDIO_DIR / ".venv" / "bin" / "python"


def _built_here(app: Path) -> Path | None:
    """The app's executable, if the app is PhD Studio built from this checkout (its stub runs this code)."""
    try:
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        exe = app / "Contents" / "MacOS" / info["CFBundleExecutable"]
        if not str(info.get("CFBundleIdentifier", "")).startswith("local.phd-studio"):
            return None
        return exe if f"sys.path.insert(0, '{STUDIO_DIR}')".encode() in exe.read_bytes() else None
    except (OSError, KeyError, plistlib.InvalidFileException):
        return None


def find_app() -> tuple[Path, Path] | None:
    """(app bundle, its executable) for PhD Studio.app built from this checkout, if there is one."""
    seen: list[Path] = []
    if os.environ.get("STUDIO_APP"):
        seen.append(Path(os.environ["STUDIO_APP"]))
    for base in (Path.home() / "Desktop", Path("/Applications"), Path.home() / "Applications"):
        seen += [base / n for n in APP_NAMES]
    if sys.platform == "darwin":
        try:
            from AppKit import NSWorkspace

            urls = NSWorkspace.sharedWorkspace().URLsForApplicationsWithBundleIdentifier_("local.phd-studio") or []
            seen += [Path(str(u.path())) for u in urls]
        except Exception:  # noqa: BLE001 - LaunchServices is only a convenience here
            pass
    for app in seen:
        exe = _built_here(app)
        if exe:
            return app, exe
    return None


def definition(found: tuple[Path, Path] | None | bool = True) -> dict[str, Any]:
    """The plist. found: find_app()'s result if already known (True = look it up)."""
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONUNBUFFERED": "1"}
    for k in ("STUDIO_CONFIG", "STUDIO_DEVICE"):  # keep a non-default config / device name
        if os.environ.get(k):
            env[k] = os.environ[k]
    found = find_app() if found is True else found
    d: dict[str, Any] = {"Label": LABEL}
    if found:  # the stub changes into the project itself, as the app
        d["ProgramArguments"] = [str(found[1])]
        env["PHD_STUDIO_TASK"] = TASK
    else:
        d["ProgramArguments"] = [str(venv_python()), "-m", "app.cli", *TASK.split()]
        d["WorkingDirectory"] = str(STUDIO_DIR)
    return {
        **d,
        "EnvironmentVariables": env,
        "StartCalendarInterval": [{"Hour": h, "Minute": m} for h, m in CHECK_TIMES],
        "RunAtLoad": True,  # also at login, and right after installing
        "ProcessType": "Background",
        "AssociatedBundleIdentifiers": ["local.phd-studio"],  # Login Items lists it under PhD Studio
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=20)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def status() -> dict[str, Any]:
    p = plist_path()
    found = find_app() if sys.platform == "darwin" else None
    out: dict[str, Any] = {
        "supported": sys.platform == "darwin", "installed": p.exists(), "loaded": False, "path": str(p),
        "times": [f"{h:02d}:{m:02d}" for h, m in CHECK_TIMES], "log": str(LOG),
        "runner": "app" if found else "python", "app": str(found[0]) if found else None,
    }
    if p.exists():
        try:
            args = plistlib.loads(p.read_bytes()).get("ProgramArguments") or []
        except (OSError, plistlib.InvalidFileException):
            args = []
        out["program"] = args[0] if args else None
        out["program_ok"] = bool(args) and Path(args[0]).exists()
        out["current"] = args == definition(found)["ProgramArguments"]  # points at this checkout's app / venv
        out["loaded"] = _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0
    return out


def install() -> dict[str, Any]:
    if sys.platform != "darwin":
        raise RuntimeError("后台提醒只支持 macOS")
    d = definition()
    if not Path(d["ProgramArguments"][0]).exists():
        raise RuntimeError(f"找不到 {d['ProgramArguments'][0]}：先在项目里运行 uv sync")
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    _launchctl("bootout", f"{_domain()}/{LABEL}")  # an older copy, if loaded; fails harmlessly otherwise
    plist_path().write_bytes(plistlib.dumps(d))
    r = _launchctl("bootstrap", _domain(), str(plist_path()))
    if r.returncode:
        raise RuntimeError(f"launchctl bootstrap 失败：{(r.stderr or r.stdout).strip()[:300]}")
    return status()


def uninstall() -> dict[str, Any]:
    if sys.platform == "darwin":
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    plist_path().unlink(missing_ok=True)
    return status()
