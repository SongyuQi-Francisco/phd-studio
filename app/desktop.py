"""Native window for PhD Studio (macOS WebKit via pywebview).

Runs the server in a background thread and shows the UI in a real app window:
closing the window (or ⌘Q, or the power button in the sidebar) stops everything.
If the server is already running, the existing window is brought to the front.
"""
from __future__ import annotations

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
    app.state.quit = window.destroy  # the sidebar power button closes the window
    webview.start(private_mode=False)

    server.should_exit = True
    thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
