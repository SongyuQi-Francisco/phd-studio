"""Answer only requests that are addressed to this machine (#64).

The server listens on 127.0.0.1 and has no login, so the browser is the one way in for a web page
the user did not mean to talk to. Two checks close it:

- Host. DNS rebinding points an attacker's domain at 127.0.0.1, so the page is "same origin" with
  the API, but the request still says `Host: evil.example`. Every request, whatever the method,
  must name 127.0.0.1, localhost or [::1], with any port (STUDIO_PORT, `--port`).
- Origin. For writes and WebSocket handshakes (a WebSocket is not covered by the browser's
  cross-origin rules at all), a request that carries an Origin must carry a local one. A request
  without one is a client that is not a browser (the CLI, httpx, curl): browsers always send Origin
  on those.

The desktop window loads http://127.0.0.1:<port>, so both checks pass as they are.
"""
from __future__ import annotations

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import i18n

_LOCAL = r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?"
_HOST = re.compile(_LOCAL, re.IGNORECASE)
_ORIGIN = re.compile(rf"https?://{_LOCAL}", re.IGNORECASE)
_READ_ONLY = {"GET", "HEAD", "OPTIONS"}


def _values(scope: Scope, name: bytes) -> list[str]:
    return [v.decode("latin-1") for k, v in scope["headers"] if k == name]


def _refusal(scope: Scope) -> str | None:
    """Why this request is refused, in the interface language, or None to let it through."""
    hosts = _values(scope, b"host")
    if len(hosts) != 1 or not _HOST.fullmatch(hosts[0]):
        return i18n.t("api.bad_host")
    if scope["type"] == "websocket" or scope["method"] not in _READ_ONLY:
        origins = _values(scope, b"origin")
        if origins and (len(origins) > 1 or not _ORIGIN.fullmatch(origins[0])):
            return i18n.t("api.bad_origin")
    return None


class LocalOnlyMiddleware:
    """Plain ASGI rather than `@app.middleware("http")`, which never sees WebSocket handshakes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or (reason := _refusal(scope)) is None:
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await receive()  # websocket.connect; closing before accepting makes the server answer 403
            await send({"type": "websocket.close", "code": 1008})
        else:
            await JSONResponse({"detail": reason}, status_code=403)(scope, receive, send)
