"""macOS notifications.

Two ways to post one, tried in order:

1. UNUserNotificationCenter, when this process is PhD Studio.app: the desktop window runs the server
   inside the app's native stub, and the background check runs the stub too (see remind_agent.py).
   The notification then carries the app's name and icon, and clicking it opens or focuses the
   workbench, on the event when the window is up (watch_clicks).
2. `osascript -e 'display notification …'`, from anything else (a server started with start.sh, a
   bare `studio remind`). macOS shows these as Script Editor, and a click opens Script Editor.

UNUserNotificationCenter needs a bundle: in a bare Python process it raises an NSException that
aborts the whole process, so it is only touched when the main bundle is PhD Studio's. PyObjC ships
no UserNotifications binding here (pywebview pulls in Cocoa/WebKit only), so the framework is loaded
by path and the block signatures it needs are registered by hand.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import uuid
from types import SimpleNamespace
from typing import Any, Callable

BUNDLE_PREFIX = "local.phd-studio"
THREAD = "phd-studio-reminders"
_un: SimpleNamespace | None = None
_delegate: Any = None
_on_open: Callable[[dict[str, Any]], None] | None = None


def bundle_id() -> str:
    """This process's main bundle id ("" for a bare Python or off macOS)."""
    if sys.platform != "darwin":
        return ""
    try:
        from Foundation import NSBundle
    except ImportError:
        return ""
    return str(NSBundle.mainBundle().bundleIdentifier() or "")


def in_app() -> bool:
    return bundle_id().startswith(BUNDLE_PREFIX)


def method() -> str:
    """How send() would post right now: "app", "osascript" or "none"."""
    if in_app():
        return "app"
    if sys.platform == "darwin" and shutil.which("osascript"):
        return "osascript"
    return "none"


def send(title: str, body: str = "", subtitle: str = "", open_: dict[str, Any] | None = None,
         ident: str | None = None, wait: float = 20.0, ask: bool = False) -> dict[str, Any]:
    """Post one notification. Returns {"ok", "via", "error"?, "denied"?}; never raises.

    ask: may show macOS's "allow notifications?" prompt. Only for a process that stays up while the user
    answers (the test button): a prompt left open by a background check that then exits ended up as
    "denied" on macOS 26. "denied" in the result means further sends in this run are pointless.
    """
    via = method()
    try:
        if via == "app":
            return _send_un(title, body, subtitle, open_, ident or uuid.uuid4().hex, wait, ask)
        if via == "osascript":
            return _send_osascript(title, body, subtitle)
    except Exception as e:  # noqa: BLE001 - a failed notification must not break the caller
        return {"ok": False, "via": via, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "via": "none", "error": "这台机器不能发系统通知（只支持 macOS）"}


# ---------- osascript ----------

def _send_osascript(title: str, body: str, subtitle: str) -> dict[str, Any]:
    script = ["on run argv",
              "display notification (item 2 of argv) with title (item 1 of argv) subtitle (item 3 of argv) "
              'sound name "default"',
              "end run"]
    args = ["osascript"] + [a for line in script for a in ("-e", line)] + [title, body, subtitle]
    r = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if r.returncode:
        return {"ok": False, "via": "osascript", "error": (r.stderr or r.stdout).strip()[:300]}
    return {"ok": True, "via": "osascript"}


# ---------- UNUserNotificationCenter ----------

AUTH = {0: "not_determined", 1: "denied", 2: "authorized", 3: "provisional", 4: "ephemeral"}
_ALERT_SOUND = 4 | 2  # UNAuthorizationOptionAlert | UNAuthorizationOptionSound
_PRESENT = 16 | 8 | 2  # UNNotificationPresentationOptionBanner | List | Sound


def _load() -> SimpleNamespace:
    global _un
    if _un is None:
        import objc
        from Foundation import NSBundle

        NSBundle.bundleWithPath_("/System/Library/Frameworks/UserNotifications.framework").load()

        def block(cls: bytes, sel: bytes, index: int, *args: bytes) -> None:
            objc.registerMetaDataForSelector(cls, sel, {"arguments": {index: {"callable": {
                "retval": {"type": b"v"},
                "arguments": {0: {"type": b"^v"}, **{i + 1: {"type": t} for i, t in enumerate(args)}}}}}})

        block(b"UNUserNotificationCenter", b"getNotificationSettingsWithCompletionHandler:", 2, b"@")
        block(b"UNUserNotificationCenter", b"requestAuthorizationWithOptions:completionHandler:", 3, b"Z", b"@")
        block(b"UNUserNotificationCenter", b"addNotificationRequest:withCompletionHandler:", 3, b"@")
        block(b"NSObject", b"userNotificationCenter:willPresentNotification:withCompletionHandler:", 4, b"Q")
        block(b"NSObject", b"userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:", 4)
        look = objc.lookUpClass
        _un = SimpleNamespace(objc=objc, center=look("UNUserNotificationCenter").currentNotificationCenter(),
                              Content=look("UNMutableNotificationContent"), Request=look("UNNotificationRequest"),
                              Sound=look("UNNotificationSound"))
    return _un


def _wait(done: threading.Event, seconds: float) -> bool:
    """Wait for a completion handler; on the main thread keep its run loop turning meanwhile."""
    if threading.current_thread() is not threading.main_thread():
        return done.wait(seconds)
    from Foundation import NSDate, NSRunLoop

    loop = NSRunLoop.currentRunLoop()
    for _ in range(int(seconds * 10)):
        if done.is_set():
            return True
        loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
    return done.is_set()


def authorization(wait: float = 5.0) -> str:
    """This app's notification permission: authorized, denied, not_determined, … ("" if unknown)."""
    if not in_app():
        return ""
    un = _load()
    out: dict[str, int] = {}
    done = threading.Event()

    def got(settings: Any) -> None:
        out["status"] = int(settings.authorizationStatus())
        done.set()

    un.center.getNotificationSettingsWithCompletionHandler_(got)
    return AUTH.get(out["status"], "") if _wait(done, wait) else ""


def _send_un(title: str, body: str, subtitle: str, open_: dict[str, Any] | None, ident: str,
             wait: float, ask: bool) -> dict[str, Any]:
    status = authorization()
    if status == "not_determined" and not ask:
        return {"ok": False, "via": "app", "denied": True, "auth": status,
                "error": "还没允许 PhD Studio 发通知：在设置 → 截稿提醒里点「发一条测试通知」，再在系统提示里允许"}
    if status == "not_determined":  # first time: macOS asks the user
        un = _load()
        done = threading.Event()
        answer: dict[str, Any] = {}

        def asked(granted: bool, error: Any) -> None:
            answer.update(granted=bool(granted), error=error)
            done.set()

        un.center.requestAuthorizationWithOptions_completionHandler_(_ALERT_SOUND, asked)
        if not _wait(done, max(wait, 60.0)):
            return {"ok": False, "via": "app", "error": "还在等你在系统弹窗里允许 PhD Studio 发通知"}
        if not answer["granted"]:
            why = answer["error"].localizedDescription() if answer["error"] is not None else "没有给出原因"
            # "Notifications are not allowed for this application" with the status still undetermined:
            # macOS could not validate the app, e.g. a copy run from a temporary folder.
            return {"ok": False, "via": "app", "denied": True, "auth": authorization(),
                    "error": f"macOS 没有允许 PhD Studio 发通知（{why}）"}
        status = "authorized"
    if status == "denied":
        return {"ok": False, "via": "app", "denied": True, "auth": status,
                "error": "PhD Studio 的通知被关掉了：系统设置 → 通知 → PhD Studio，打开「允许通知」"}

    un = _load()
    content = un.Content.alloc().init()
    content.setTitle_(title)
    if subtitle:
        content.setSubtitle_(subtitle)
    content.setBody_(body)
    content.setSound_(un.Sound.defaultSound())
    content.setThreadIdentifier_(THREAD)
    if open_:
        content.setUserInfo_({"open": json.dumps(open_, ensure_ascii=False)})
    request = un.Request.requestWithIdentifier_content_trigger_(ident, content, None)
    done = threading.Event()
    result: dict[str, Any] = {}

    def added(error: Any) -> None:
        result["error"] = error
        done.set()

    un.center.addNotificationRequest_withCompletionHandler_(request, added)
    if not _wait(done, wait):
        return {"ok": False, "via": "app", "error": "通知中心没有回应"}
    if result.get("error") is not None:
        return {"ok": False, "via": "app", "error": str(result["error"].localizedDescription())}
    return {"ok": True, "via": "app"}


def open_settings() -> bool:
    """System Settings → Notifications, at this app when macOS supports the deep link."""
    if sys.platform != "darwin":
        return False
    url = "x-apple.systempreferences:com.apple.Notifications-Settings.extension"
    app = bundle_id() or BUNDLE_PREFIX
    return subprocess.run(["open", f"{url}?id={app}"], capture_output=True, timeout=10).returncode == 0


def _open_from(response: Any) -> dict[str, Any] | None:
    info = response.notification().request().content().userInfo()
    raw = info.get("open") if info is not None else None
    return json.loads(str(raw)) if raw else None


def watch_clicks(on_open: Callable[[dict[str, Any]], None]) -> bool:
    """In the desktop app: show banners while the window is in front, and hand clicks to on_open.

    Must run before the app finishes launching, so a click that launched the app is delivered too.
    """
    global _delegate, _on_open
    if not in_app():
        return False
    un = _load()
    objc = un.objc
    _on_open = on_open
    if _delegate is None:
        from Foundation import NSObject

        # The protocol is not registered by name until something links against it, so the method
        # types are given here instead ("@?" is the completion block, typed by block() above).
        class StudioNotificationDelegate(NSObject):
            @objc.typedSelector(b"v@:@@@?")
            def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, center, note, handler):
                try:
                    handler(_PRESENT)
                except Exception as e:  # noqa: BLE001 - never raise into Objective-C
                    print(f"notify: willPresent failed: {e}", file=sys.stderr)

            @objc.typedSelector(b"v@:@@@?")
            def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, center, response,
                                                                                             handler):
                try:
                    target = _open_from(response)
                    if target is not None and _on_open is not None:
                        _on_open(target)
                except Exception as e:  # noqa: BLE001
                    print(f"notify: click failed: {e}", file=sys.stderr)
                try:
                    handler()
                except Exception:  # noqa: BLE001
                    pass

        _delegate = StudioNotificationDelegate.alloc().init()
    un.center.setDelegate_(_delegate)  # the center keeps a weak reference; _delegate keeps it alive
    return True
