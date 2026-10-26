"""The LaunchAgent that runs the reminder check every hour, with or without the workbench open.

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
  With the project on the Desktop, macOS may stop that Python from reading it; status() reports
  launchd's run count and last exit code, and the checker its own last outcome, so Settings shows it.

Nothing from the installing process's environment goes into the plist, and only a workbench running
on the project's own studio.toml may install: one started with another STUDIO_CONFIG (a sandbox, a
test) would otherwise repoint the one agent at that config.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import i18n
from .config import CONFIG_FILE, STUDIO_DIR

LABEL = "local.phd-studio.reminders"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LOG = Path.home() / "Library" / "Logs" / "PhD Studio Reminders.log"
# StartCalendarInterval below runs it on the hour (reminders.CHECK_EVERY assumes it); status() words that as "every".
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
        "StartCalendarInterval": [{"Minute": 0}],  # hourly; launchd runs a missed one on wake
        "RunAtLoad": True,  # also at login
        "ProcessType": "Background",
        "AssociatedBundleIdentifiers": ["local.phd-studio"],  # Login Items lists it under PhD Studio
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=20)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _job() -> dict[str, Any]:
    """launchd's view of the job: loaded, how many runs, last exit code (None = never exited)."""
    r = _launchctl("print", f"{_domain()}/{LABEL}")
    if r.returncode:
        return {"loaded": False}
    runs = re.search(r"^\s*runs = (\d+)", r.stdout, re.M)
    code = re.search(r"^\s*last exit code = (-?\d+)", r.stdout, re.M)
    return {"loaded": True, "runs": int(runs.group(1)) if runs else None, "last_exit": int(code.group(1)) if code else None}


def official_config() -> bool:
    """This workbench runs on the project's own studio.toml (not a STUDIO_CONFIG sandbox)."""
    return CONFIG_FILE.resolve() == (STUDIO_DIR / "studio.toml").resolve()


def status() -> dict[str, Any]:
    p = plist_path()
    found = find_app() if sys.platform == "darwin" else None
    out: dict[str, Any] = {
        "supported": sys.platform == "darwin", "installed": p.exists(), "loaded": False, "path": str(p),
        "every": i18n.t("reminders.every"), "log": str(LOG), "can_install": official_config(),
        "runner": "app" if found else "python", "app": str(found[0]) if found else None,
    }
    if p.exists():
        try:
            installed = plistlib.loads(p.read_bytes())
        except (OSError, plistlib.InvalidFileException):
            installed = {}
        args = installed.get("ProgramArguments") or []
        out["program"] = args[0] if args else None
        out["program_ok"] = bool(args) and Path(args[0]).exists()
        out["installed_at"] = p.stat().st_mtime
        out["current"] = installed == definition(found)  # this checkout's app / venv, same settings
        out.update(_job())
    return out


def install() -> dict[str, Any]:
    if sys.platform != "darwin":
        raise RuntimeError(i18n.t("reminders.agent.mac_only"))
    if not official_config():
        raise RuntimeError(i18n.t("reminders.agent.other_config", config=CONFIG_FILE))
    d = definition()
    if not Path(d["ProgramArguments"][0]).exists():
        raise RuntimeError(i18n.t("reminders.agent.not_found", path=d["ProgramArguments"][0]))
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    _launchctl("bootout", f"{_domain()}/{LABEL}")  # an older copy, if loaded; fails harmlessly otherwise
    plist_path().write_bytes(plistlib.dumps(d))
    r = _launchctl("bootstrap", _domain(), str(plist_path()))
    if r.returncode:
        raise RuntimeError(i18n.t("reminders.agent.bootstrap_failed", error=(r.stderr or r.stdout).strip()[:300]))
    # RunAtLoad alone may be deferred for a Background job ("speculative" spawn): check once right now.
    _launchctl("kickstart", f"{_domain()}/{LABEL}")
    return status()


def uninstall() -> dict[str, Any]:
    if sys.platform == "darwin":
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    plist_path().unlink(missing_ok=True)
    return status()
