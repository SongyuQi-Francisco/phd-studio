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

import httpx
import uvicorn

from .config import STUDIO_DIR

PORT = 8770
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
    """pywebview's default menu lacks Undo/Redo and a Window menu; add them (⌘Z, ⇧⌘Z, ⌘W, ⌘M)."""
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
            redo = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("重做", "redo:", "z")
            redo.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand | NSEventModifierFlagShift)
            edit.insertItem_atIndex_(redo, 0)
            edit.insertItem_atIndex_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("撤销", "undo:", "z"), 0)
            edit.insertItem_atIndex_(NSMenuItem.separatorItem(), 2)
        win = NSMenu.alloc().initWithTitle_("窗口")
        win.addItemWithTitle_action_keyEquivalent_("最小化", "performMiniaturize:", "m")
        win.addItemWithTitle_action_keyEquivalent_("关闭窗口", "performClose:", "w")
        holder = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("窗口", None, "")
        holder.setSubmenu_(win)
        main.addItem_(holder)
        NSApplication.sharedApplication().setWindowsMenu_(win)

    AppHelper.callAfter(add)


def main() -> int:
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
