"""Native window for PhD Studio (macOS WebKit via pywebview).

Runs the server in a background thread and shows the UI in a real app window:
closing the window (or ⌘Q, or the power button in the sidebar) stops everything.
If the server is already running, the existing window is brought to the front.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Callable

import httpx
import uvicorn

from . import i18n
from .config import STUDIO_DIR

PORT = int(os.environ.get("STUDIO_PORT") or 8770)  # another port only for a second, development window
URL = f"http://127.0.0.1:{PORT}"
ICON = STUDIO_DIR / "launcher" / "icon.png"


def _up() -> bool:
    try:
        return httpx.get(f"{URL}/api/status", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


def _brand_process() -> None:
    """Dock icon and app-menu name, instead of the generic Python ones."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle

        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "PhD Studio"
        if ICON.exists():
            NSApplication.sharedApplication().setApplicationIconImage_(
                NSImage.alloc().initWithContentsOfFile_(str(ICON)))
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def _native_menus() -> None:
    """pywebview's default menu lacks Undo/Redo and a Window menu; add them (⌘Z, ⇧⌘Z, ⌘W, ⌘M).

    The titles follow the interface language the app was launched with: the native menu is built once,
    at startup, so changing the language in Settings shows here after the next launch."""
    try:
        from AppKit import NSApplication, NSEventModifierFlagCommand, NSEventModifierFlagShift, NSMenu, NSMenuItem
        from PyObjCTools import AppHelper
    except Exception:  # noqa: BLE001
        return

    def add() -> None:
        main = NSApplication.sharedApplication().mainMenu()
        if main is None:
            return
        edit = next((main.itemAtIndex_(i).submenu() for i in range(main.numberOfItems())
                     if main.itemAtIndex_(i).submenu() is not None
                     and main.itemAtIndex_(i).submenu().indexOfItemWithTarget_andAction_(None, "copy:") >= 0), None)
        if edit is not None and edit.indexOfItemWithTarget_andAction_(None, "undo:") < 0:
            redo = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(i18n.t("desktop.menu.redo"), "redo:", "z")
            redo.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand | NSEventModifierFlagShift)
            edit.insertItem_atIndex_(redo, 0)
            undo = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(i18n.t("desktop.menu.undo"), "undo:", "z")
            edit.insertItem_atIndex_(undo, 0)
            edit.insertItem_atIndex_(NSMenuItem.separatorItem(), 2)
        window_title = i18n.t("desktop.menu.window")
        win = NSMenu.alloc().initWithTitle_(window_title)
        win.addItemWithTitle_action_keyEquivalent_(i18n.t("desktop.menu.minimize"), "performMiniaturize:", "m")
        win.addItemWithTitle_action_keyEquivalent_(i18n.t("desktop.menu.close"), "performClose:", "w")
        holder = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(window_title, None, "")
        holder.setSubmenu_(win)
        main.addItem_(holder)
        NSApplication.sharedApplication().setWindowsMenu_(win)

    AppHelper.callAfter(add)


def _task(task: str) -> int:
    """PHD_STUDIO_TASK: the reminders LaunchAgent runs this app's executable (the stub ignores
    arguments) so the check runs as PhD Studio; see remind_agent.py. Only `remind …` is accepted."""
    import shlex

    from .cli import main as cli

    args = shlex.split(task)
    if args[:1] != ["remind"]:
        print(f"PHD_STUDIO_TASK: not allowed: {task}", file=sys.stderr)
        return 2
    return cli(args)


def _open_target(window: Any, focus: Callable[[], None]) -> Callable[[dict[str, Any]], None]:
    """A clicked reminder: bring the window forward and show its event (the page may still be loading)."""
    import json

    def run(target: dict[str, Any]) -> None:
        def go() -> None:
            focus()
            detail = json.dumps(target, ensure_ascii=False)
            window.evaluate_js(f"(window.studioOpen || (d => {{ window.studioOpenPending = d; }}))({detail})")
        threading.Thread(target=go, daemon=True).start()
    return run


def main() -> int:
    task = os.environ.pop("PHD_STUDIO_TASK", "")
    if task:
        return _task(task)
    if _up():
        # Already running: ask that instance to show its window.
        try:
            httpx.post(f"{URL}/api/focus", timeout=2)
        except httpx.HTTPError:
            pass
        return 0

    import webview

    from .main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if _up():
            break
        time.sleep(0.1)

    _brand_process()
    window = webview.create_window("PhD Studio", URL, width=1440, height=900, min_size=(960, 600),
                                   text_select=True)

    def focus() -> None:
        window.restore()
        window.show()
        try:
            from AppKit import NSApplication
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except Exception:  # noqa: BLE001
            pass

    app.state.focus = focus
    try:
        from . import notify
        notify.watch_clicks(_open_target(window, focus))  # before start(): a click may be what launched us
    except Exception as e:  # noqa: BLE001 - reminders still post; clicks just open the app
        print(f"notifications: {e}", file=sys.stderr)
    if os.environ.get("STUDIO_DEBUG") == "1":
        # Test hook: lets a local test script run JS inside this WebKit window. Off by default.
        app.state.eval_js = window.evaluate_js
    app.state.quit = window.destroy  # the sidebar power button closes the window
    webview.start(_native_menus, private_mode=False)

    server.should_exit = True
    thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
