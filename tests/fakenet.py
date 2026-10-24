"""A stand-in for httpx.get that serves recorded responses (tests/fixtures/sources) by URL.

An unrouted URL fails like a dead connection, so a test also covers "this source is down" by
simply not routing it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "sources"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> Any:
    return json.loads(fixture(name))


class FakeNet:
    def __init__(self) -> None:
        self.routes: list[tuple[str, dict[str, str], Any, int]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def add(self, needle: str, body: Any, status: int = 200, **params: str) -> None:
        """Serve `body` (text, or a dict/list as JSON) for URLs containing `needle` whose query
        params include `params`. The first route added that matches wins."""
        self.routes.append((needle, params, body, status))

    def calls_to(self, needle: str) -> list[dict[str, Any]]:
        return [p for u, p in self.calls if needle in u]

    def __call__(self, url: str, params: dict[str, Any] | None = None, **_: Any) -> httpx.Response:
        params = dict(params or {})
        self.calls.append((url, params))
        for needle, want, body, status in self.routes:
            if needle in url and all(str(params.get(k)) == v for k, v in want.items()):
                req = httpx.Request("GET", url)
                if isinstance(body, (dict, list)):
                    return httpx.Response(status, json=body, request=req)
                return httpx.Response(status, text=body, request=req)
        raise httpx.ConnectError("no route in the test", request=httpx.Request("GET", url))


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> FakeNet:
    n = FakeNet()
    monkeypatch.setattr("httpx.get", n)
    monkeypatch.setattr(time, "sleep", lambda s: None)  # request pacing waits for nothing
    return n
