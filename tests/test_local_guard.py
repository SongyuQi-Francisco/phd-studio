"""The server answers only requests addressed to this machine (#64): Host, Origin, WebSocket handshakes."""
from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.local_guard import LocalOnlyMiddleware

PNG = {"png_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()}  # the smallest write /api/crops accepts

LOCAL_HOSTS = ["127.0.0.1:8770", "localhost:8770", "127.0.0.1:8771", "localhost:5173", "LocalHost:8770", "127.0.0.1",
               "[::1]:8770"]
FOREIGN_HOSTS = ["evil.example", "evil.example:8770", "127.0.0.1.evil.example:8770", "localhost.evil.example",
                 "evil.example/127.0.0.1:8770", "127.0.0.1:8770.evil.example", "127.0.0.1:99999999", "192.168.1.20:8770",
                 "0.0.0.0:8770", "testserver", "foo.localhost:8770", ""]
LOCAL_ORIGINS = ["http://127.0.0.1:8770", "http://localhost:8770", "http://localhost:5173", "http://[::1]:8770",
                 "https://localhost:8443"]
FOREIGN_ORIGINS = ["https://evil.example", "http://evil.example:8770", "http://127.0.0.1.evil.example",
                   "http://localhost.evil.example:8770", "http://127.0.0.1:8770.evil.example", "null",
                   "chrome-extension://abcdefgh", "file://", "http://192.168.1.20:8770", ""]


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)  # Host: 127.0.0.1:8770, as in the desktop window


@pytest.fixture
def chats(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Which chats the WebSocket handler asked for. It reports every stem as unknown, so nothing real starts."""
    asked: list[str] = []

    def get(stem: str) -> Any:
        asked.append(stem)
        raise FileNotFoundError(stem)

    monkeypatch.setattr(main.registry, "get", get)
    return asked


def at(host: str) -> dict[str, str]:
    return {"host": host}


# ---------- Host ----------


@pytest.mark.parametrize("host", LOCAL_HOSTS)
def test_a_local_host_is_served(client: TestClient, host: str):
    assert client.get("/api/papers", headers=at(host)).status_code == 200


@pytest.mark.parametrize("host", FOREIGN_HOSTS)
def test_a_foreign_host_is_refused(client: TestClient, host: str):
    r = client.get("/api/papers", headers=at(host))
    assert r.status_code == 403
    assert r.json() == {"detail": "已拒绝：Host 不是本机（127.0.0.1 或 localhost）"}  # STUDIO_LANG=zh in the tests


def test_a_foreign_host_is_refused_on_every_path(client: TestClient):
    for path in ("/", "/index.html", "/api/papers", "/api/nothing-here", "/vendor/x.js"):
        assert client.get(path, headers=at("evil.example:8770")).status_code == 403, path


def test_a_foreign_host_cannot_write_either(client: TestClient):
    r = client.post("/api/crops", json=PNG, headers=at("evil.example:8770"))
    assert r.status_code == 403


def test_a_request_with_no_host_or_two_hosts_is_refused():
    reached: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["path"])

    guard = LocalOnlyMiddleware(inner)
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    def call(headers: list[tuple[bytes, bytes]]) -> None:
        scope = {"type": "http", "method": "GET", "path": "/api/papers", "headers": headers}
        asyncio.run(guard(scope, receive, send))

    call([])
    call([(b"host", b"127.0.0.1:8770"), (b"host", b"evil.example")])
    assert reached == []
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [403, 403]
    call([(b"host", b"127.0.0.1:8770")])
    assert reached == ["/api/papers"]


def test_the_lifespan_is_not_checked():
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    asyncio.run(LocalOnlyMiddleware(inner)({"type": "lifespan"}, None, None))  # type: ignore[arg-type]
    assert seen == ["lifespan"]


# ---------- Origin ----------


@pytest.mark.parametrize("origin", [None, *LOCAL_ORIGINS])
def test_a_write_from_a_local_page_or_a_plain_client_goes_through(client: TestClient, origin: str | None):
    headers = {"origin": origin} if origin else {}  # no Origin: the CLI, httpx, curl
    r = client.post("/api/crops", json=PNG, headers=headers)
    assert r.status_code == 200 and r.json()["name"].endswith(".png")


@pytest.mark.parametrize("origin", FOREIGN_ORIGINS)
def test_a_write_from_a_foreign_page_is_refused(client: TestClient, origin: str):
    r = client.post("/api/crops", json=PNG, headers={"origin": origin})
    assert r.status_code == 403
    assert r.json() == {"detail": "已拒绝：请求来自不在本机的网页"}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_kind_of_write_checks_the_origin(client: TestClient, method: str):
    r = client.request(method, "/api/crops/whatever", json=PNG, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_a_write_to_a_real_endpoint_is_not_applied_when_refused(client: TestClient):
    client.post("/api/settings", json={"ui_language": "en"}, headers={"origin": "https://evil.example"})
    assert main.load_settings().get("ui_language") != "en"


def test_a_two_origin_request_is_refused(client: TestClient):
    r = client.post("/api/crops", json=PNG, headers=[("origin", "http://127.0.0.1:8770"), ("origin", "https://evil.example")])
    assert r.status_code == 403


def test_a_read_from_a_foreign_page_is_not_this_guards_business(client: TestClient):
    """Reads are protected by the browser (no CORS headers are sent), not by Origin; only the Host decides."""
    assert client.get("/api/papers", headers={"origin": "https://evil.example"}).status_code == 200


def test_a_foreign_host_with_a_local_origin_is_still_refused(client: TestClient):
    r = client.post("/api/crops", json=PNG, headers={"host": "evil.example:8770", "origin": "http://127.0.0.1:8770"})
    assert r.status_code == 403


# ---------- WebSocket ----------


def test_a_local_websocket_handshake_is_accepted(client: TestClient, chats: list[str]):
    with client.websocket_connect("/ws/chat/NoSuchPaper") as ws:  # no Origin: not a browser
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4404  # the handler ran: it accepted, looked the chat up, closed
    assert chats == ["NoSuchPaper"]


@pytest.mark.parametrize("origin", LOCAL_ORIGINS)
def test_a_websocket_from_a_local_page_is_accepted(client: TestClient, chats: list[str], origin: str):
    with client.websocket_connect("/ws/chat/NoSuchPaper", headers={"origin": origin}) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4404
    assert chats == ["NoSuchPaper"]


@pytest.mark.parametrize("origin", FOREIGN_ORIGINS)
def test_a_websocket_from_a_foreign_page_is_rejected(client: TestClient, chats: list[str], origin: str):
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws/chat/NoSuchPaper", headers={"origin": origin}):
            pass
    assert refused.value.code == 1008
    assert chats == []  # the handler never ran


@pytest.mark.parametrize("host", FOREIGN_HOSTS)
def test_a_websocket_to_a_foreign_host_is_rejected(client: TestClient, chats: list[str], host: str):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/chat/NoSuchPaper", headers=at(host)):
            pass
    assert chats == []


def test_a_websocket_to_another_local_port_is_accepted(chats: list[str]):
    """A second window (STUDIO_PORT) or a dev server on any other port."""
    c = TestClient(main.app, base_url="http://localhost:8799")
    with c.websocket_connect("/ws/chat/NoSuchPaper", headers={"origin": "http://localhost:8799"}) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4404
