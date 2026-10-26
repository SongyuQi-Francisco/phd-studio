"""Interface language on the server (#52): providers, the model picker, reminders, the desktop menu, the
bilingual queue and Zotero come in both languages.

Every message is asked for under each interface language (`config.save_settings({"ui_language": ...})`,
as tests/test_i18n.py does) and must be real text in that language: non-empty, different from the other
language, never a bare key, Chinese in zh and none in en. conftest pins STUDIO_LANG=zh, so the existing
tests keep reading the Chinese.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from subprocess import CompletedProcess
from typing import Any, Callable

import httpx
import pytest

from app import (bilingual, bilingual_queue as bq, claude_chat, config, i18n, model_chat, notify, providers,
                 remind_agent, reminders, zotero_push)

CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
MODULES = ("providers", "model", "reminders", "desktop", "bilingual", "zotero")
REAL_SEND = notify.send  # the autouse fixture below replaces notify.send in every test
SAME_IN_BOTH = {"reminders.kind.gate"}  # a proper noun: the same word in both languages


def my_keys() -> list[str]:
    return sorted(k for k in i18n.load_tree(i18n.APP_LOCALES)["zh"] if k.split(".")[0] in MODULES)


def in_both(call: Callable[[], str]) -> tuple[str, str]:
    """What a call gives under the Chinese and under the English interface."""
    out = []
    for lang in ("zh", "en"):
        config.save_settings({"ui_language": lang})
        out.append(call())
    return out[0], out[1]


def problems(calls: dict[str, Callable[[], str]]) -> list[str]:
    bad, keys = [], my_keys()
    for name, call in calls.items():
        zh, en = in_both(call)
        if not (zh.strip() and en.strip()):
            bad.append(f"{name}: empty ({zh!r}, {en!r})")
        elif zh == en:
            bad.append(f"{name}: the same in both languages ({zh!r})")
        elif not CJK.search(zh) or CJK.search(en):
            bad.append(f"{name}: zh needs Chinese and en none ({zh!r}, {en!r})")
        elif any(k in text for text in (zh, en) for k in keys):
            bad.append(f"{name}: a message key shows ({zh!r}, {en!r})")
    return bad


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch):
    """No CLI, key, Claude login or system notification is touched."""
    for k in ("DEEPSEEK_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY", "GLM_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(providers.Codex, "binary", lambda self: None)
    monkeypatch.setattr(providers.Gemini, "binary", lambda self: None)
    monkeypatch.setattr(providers.Codex, "_catalog", lambda self: [])
    monkeypatch.setattr(providers.Codex, "_configured_model", lambda self: "")
    monkeypatch.setattr(claude_chat, "claude_status", lambda: {"ok": False, "method": "", "hint": "run claude setup-token"})
    monkeypatch.setattr(remind_agent, "find_app", lambda: None)
    monkeypatch.setattr(notify, "send", lambda *a, **k: pytest.fail("notify.send called in a test"))


def test_every_message_of_these_modules_has_both_languages():
    """Each key rendered with made-up values: Chinese in zh, none in en, different from each other."""
    tables, bad = i18n.load_tree(i18n.APP_LOCALES), []
    assert len(my_keys()) > 150
    for key in my_keys():
        for lang in i18n.LANGS:
            raw = tables[lang][key]
            params = {p: "x" for s in (raw.values() if isinstance(raw, dict) else [raw]) for p in re.findall(r"\{(\w+)\}", s)}
            text = i18n.tl(lang, key, **{**params, **({"n": 2} if "n" in params else {})})
            if (lang == "zh") != bool(CJK.search(text)) and key not in SAME_IN_BOTH:
                bad.append(f"{lang} {key}: {text!r}")
            if text == key or not text.strip():
                bad.append(f"{lang} {key}: shows the key or nothing")
    assert not bad, bad


# ---------- providers and the model picker ----------


def test_provider_status_models_and_friendly_errors(monkeypatch):
    claude, codex, gemini, glm, deepseek = (providers.PROVIDERS[k] for k in ("claude", "codex", "gemini", "glm", "deepseek"))
    cli = {"installed": False, "login": "Logged in using ChatGPT"}
    monkeypatch.setattr(providers.Codex, "binary", lambda self: "/bin/fake" if cli["installed"] else None)
    monkeypatch.setattr(providers.Gemini, "binary", lambda self: "/bin/fake" if cli["installed"] else None)
    monkeypatch.setattr(providers, "run_quick", lambda args, timeout=15: CompletedProcess(args, 0, cli["login"], ""))
    monkeypatch.setattr(providers.Gemini, "_api_key", lambda self: "")

    def line(p: providers.Provider, installed: bool = False, login: str = "") -> Callable[[], str]:
        def run() -> str:
            cli.update(installed=installed, login=login or cli["login"])
            return " | ".join(p.status()[k] for k in ("detail", "fix"))
        return run

    def claude_connected() -> str:
        monkeypatch.setattr(claude_chat, "claude_status", lambda: {"ok": True, "method": "token"})
        return claude.status()["detail"]

    def with_key() -> str:
        monkeypatch.setattr(providers.ApiProvider, "key", lambda self: "k")
        return deepseek.status()["detail"]

    calls: dict[str, Callable[[], str]] = {
        "claude logged out": line(claude),
        "claude connected": claude_connected,
        "claude models": lambda: " ".join(m["label"] for m in claude.models()),
        "codex missing": line(codex),
        "codex fallback model": lambda: codex.models()[0]["label"],
        "codex logged in (ChatGPT)": line(codex, True, "Logged in using ChatGPT"),
        "codex logged in (key)": line(codex, True, "Logged in using an API key"),
        "codex logged out": line(codex, True, "Not logged in"),
        "gemini missing": line(gemini),
        "gemini logged out": line(gemini, True),
        "gemini models": lambda: " ".join(m["label"] for m in gemini.models()),
        "gemini alias": lambda: providers.gemini_name("auto"),
        "key missing": line(deepseek),
        "glm label": lambda: glm.label,
        "glm models": lambda: " ".join(m["label"] for m in glm.models()),
        "deepseek models": lambda: " ".join(m["label"] for m in deepseek.models()),
        "context too long": lambda: providers.too_long("Codex", "context_length_exceeded") or "",
        "no output": lambda: providers.Run(code=2).detail(),
        "default model": lambda: providers.display("claude", ""),
        "served by many": lambda: providers.served_by({m: {"output_tokens": 9 - i} for i, m in enumerate("abc")}),
        "full text prefix": lambda: providers._short_cmd(f"cat {config.TEXT_DIR}/p.md"),
        "unavailable (cli)": lambda: asyncio.run(first_event(codex.stream(turn())))["message"],
        "unavailable (no key)": lambda: asyncio.run(first_event(deepseek.stream(turn())))["message"],
    }
    for i, detail in enumerate(("no rollout found", "not logged in", "usage limit", "model not supported", "boom")):
        calls[f"codex friendly {i}"] = lambda d=detail: codex.friendly(d)
    for i, detail in enumerate(("unknown argument --x", "login required", "quota exceeded", "model not found", "boom")):
        calls[f"gemini friendly {i}"] = lambda d=detail: gemini.friendly(d)
    for status, body in ((401, "{}"), (402, "{}"), (429, "{}"), (400, '{"error": {"message": "Model Not Exist"}}'), (503, "{}"), (418, "{}")):
        calls[f"api friendly {status}"] = lambda s=status, b=body: deepseek.friendly(s, b, "m-1")
    calls["key set"] = with_key  # last: it keeps the key for the rest of the test
    assert not problems(calls)
    # the labels are made when asked for, not when the module loads
    config.save_settings({"ui_language": "en"})
    assert glm.label == "Zhipu GLM" and claude.models()[0]["label"].startswith("Default")


def turn() -> providers.Turn:
    return providers.Turn(text="q", instructions="i", model="m-1")


async def first_event(gen: Any) -> dict[str, Any]:
    return [e async for e in gen][0]


def test_the_model_picker_api_words_labels_and_errors_per_language(client, monkeypatch):
    def labels() -> str:
        found = {p["id"]: p for p in client.get("/api/providers").json()["providers"]}
        return " | ".join([found["glm"]["label"], found["claude"]["models"][0]["label"], found["claude"]["detail"] or "-",
                           found["deepseek"]["detail"], found["deepseek"]["models"][0]["label"]])

    def detail(path: str, body: dict[str, Any]) -> Callable[[], str]:
        return lambda: client.post(path, json=body).json()["detail"]

    deepseek = providers.PROVIDERS["deepseek"]
    chat = type("Chat", (), {"generation": 0, "other": None, "stem": "studio", "log": lambda self, e: None})()
    sent: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        sent.append(payload)
    chat.emit = emit

    def gone() -> str:
        sent.clear()
        asyncio.run(model_chat.run_turn(chat, {"provider": "gone", "model": "", "thread": None}, "hi", []))
        return sent[0]["message"]

    calls = {
        "/api/providers": labels,
        "no such chat": detail("/api/chat/no-such-paper/model", {"provider": "claude"}),
        "no such provider": detail("/api/chat/studio/model", {"provider": "nope"}),
        "no such model": detail("/api/chat/studio/model", {"provider": "deepseek", "model": "zzz"}),
        "unavailable, no fallback": lambda: model_chat.unavailable(deepseek, deepseek.status()),
        "no vision (none can)": lambda: model_chat.no_vision(deepseek, "deepseek-chat"),
        "no vision (some can)": lambda: model_chat.no_vision(providers.PROVIDERS["glm"], "glm-4.7"),
        "provider gone": gone,
    }
    assert not problems(calls)


# ---------- reminders ----------


def test_reminder_notifications_in_both_languages(monkeypatch):
    at = datetime.now(timezone.utc) + timedelta(days=3)
    base = {"title": "Gate 2", "at": at, "line": "Project A", "days_left": 3}
    texts: dict[str, Callable[[], str]] = {}
    for kind in reminders.KINDS:
        for days in (0, 1, 3):
            texts[f"milestone {kind} {days}"] = lambda k=kind, d=days: " | ".join(
                reminders.message({**base, "type": "milestone", "kind": k, "days_left": d}).values())
    for days in (0, 1, 5):
        texts[f"target {days}"] = lambda d=days: " | ".join(reminders.message(
            {**base, "type": "target", "days_left": d, "targets": [{"title": "Paper A"}, {"title": "Paper B"}]}).values())
        texts[f"venue {days}"] = lambda d=days: " | ".join(reminders.message(
            {**base, "type": "venue", "days_left": d, "ccf": "A", "core": "A*"}).values())
    texts["venue without ranks"] = lambda: reminders.message({**base, "type": "venue"})["subtitle"]
    assert not problems(texts)

    sender = Sender()

    def digest() -> str:
        sender.sent.clear()
        reminders.LOCAL_FILE.unlink(missing_ok=True)  # forget what was sent: the second language sends it all again
        with reminders.store.editing() as data:
            data["sent"] = []
        soon = datetime.now(timezone.utc) + timedelta(days=2)
        monkeypatch.setattr(reminders, "events", lambda: [
            {"key": f"m:{i}", "type": "milestone", "title": f"M{i}", "at": soon + timedelta(minutes=i), "kind": "gate",
             "line": None, "open": {"view": "plan"}} for i in range(8)])
        reminders.save_settings({"enabled": True})
        reminders.check(send=sender)
        return " | ".join(sender.sent[-1][k] for k in ("title", "body", "subtitle"))

    def test_notification() -> str:
        sender.sent.clear()
        monkeypatch.setattr(notify, "send", sender)
        reminders.test_notification()
        return " | ".join(sender.sent[0][k] for k in ("title", "body", "subtitle"))

    assert not problems({"digest": digest, "test notification": test_notification})


def test_reminder_api_errors_and_the_background_check_in_both_languages(client, monkeypatch):
    def put(patch: dict[str, Any]) -> Callable[[], str]:
        return lambda: client.put("/api/reminders/settings", json=patch).json()["detail"]

    def no_notifications() -> str:
        monkeypatch.setattr(notify, "method", lambda: "none")
        return REAL_SEND("t")["error"]

    def blocked(status: str) -> Callable[[], str]:
        def run() -> str:
            monkeypatch.setattr(notify, "authorization", lambda wait=5.0: status)
            return notify._send_un("t", "b", "s", None, "id", 1.0, False)["error"]
        return run

    calls = {
        "unknown setting": put({"nope": 1}),
        "not a bool": put({"enabled": "yes"}),
        "no lead time": put({"leads": []}),
        "no kind of event": put({"types": {"target": False, "milestone": False, "venue": False}}),
        "install refused": lambda: client.post("/api/reminders/agent", json={"install": True}).json()["detail"],
        "agent status": lambda: remind_agent.status()["every"],
        "no system notifications": no_notifications,
        "not asked yet": blocked("not_determined"),
        "turned off": blocked("denied"),
    }
    assert not problems(calls)


def test_weekday_and_date_wording_follows_the_language():
    d = datetime(2027, 1, 22, 12, 0)  # a Friday
    assert in_both(lambda: reminders._when(d)) == ("1月22日 周五", "Fri, Jan 22")
    assert in_both(lambda: reminders.kind_label("kill")) == ("Kill 检查点", "Kill check")
    assert in_both(lambda: reminders.kind_label("something else")) == ("里程碑", "Milestone")
    assert in_both(lambda: reminders._left(1, False)) == ("明天截止", "due tomorrow")
    assert in_both(lambda: reminders._left(5, True)) == ("还有 5 天", "in 5 days")


class Sender:
    """Records notifications instead of posting them."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def __call__(self, title: str, body: str = "", subtitle: str = "", open_: Any = None, ident: Any = None,
                 ask: bool = False) -> dict[str, Any]:
        self.sent.append({"title": title, "body": body, "subtitle": subtitle})
        return {"ok": True, "via": "fake"}


# ---------- bilingual queue ----------


def test_the_bilingual_queue_is_worded_when_it_is_read(client):
    job = {"title": "P", "added": "t", "key": ""}
    bq._save({"paused": True, "jobs": [
        {**job, "key": "A", "state": "queued", "reason": "@enter T1"},
        {**job, "key": "B", "state": "queued", "reason": "@backfill T2"},
        {**job, "key": "C", "state": "queued", "reason": "@retry"},
        {**job, "key": "D", "state": "error", "error": "@failed"},
        {**job, "key": "E", "state": "skipped", "error": "@made"},
        {**job, "key": "F", "state": "skipped", "error": "@left T1/T2"},
    ]})

    def queue() -> str:
        q = client.get("/api/bilingual-queue").json()
        return " | ".join([j["reason"] for j in q["queued"]] + [j["error"] for j in q["recent"]])

    def start_without_a_pdf() -> str:
        bilingual._jobs.clear()
        with pytest.raises(Exception):
            bilingual.start("no-such-paper")
        err = bilingual._jobs.pop("no-such-paper")["error"]
        return err

    calls = {
        "queue reasons and errors": queue,
        "not queued": lambda: client.delete("/api/bilingual-queue/nope").json()["detail"],
        "cannot retry": lambda: client.post("/api/bilingual-queue/nope/retry").json()["detail"],
        "bad days": lambda: client.post("/api/bilingual-queue/cleanup", json={"days": 7}).json()["detail"],
        "no pdf": start_without_a_pdf,
    }
    assert not problems(calls)
    # what the file keeps is a code, and what older versions wrote in words is served as it was
    stored = json.loads(bq.QUEUE_FILE.read_text())["jobs"]
    assert [j.get("reason") for j in stored[:3]] == ["@enter T1", "@backfill T2", "@retry"]
    bq._save({"paused": True, "jobs": [{**job, "key": "G", "state": "queued", "reason": "进入 T3"}]})
    config.save_settings({"ui_language": "en"})
    assert client.get("/api/bilingual-queue").json()["queued"][0]["reason"] == "进入 T3"


# ---------- Zotero ----------


class Resp:
    def __init__(self, status: int, text: str = "") -> None:
        self.status_code, self.text = status, text

    def raise_for_status(self) -> None:
        pass


def test_zotero_errors_and_warnings_in_both_languages(client, make_note, monkeypatch):
    make_note("Plain", title="Plain")  # no DOI, arXiv number or link
    make_note("Linked", title="Linked", zotero="zotero://select/library/items/ZZZZ9999")
    key = "ABCD1234"

    def fails(call: Callable[[], Any]) -> Callable[[], str]:
        def run() -> str:
            with pytest.raises((zotero_push.ZoteroError, LookupError, ValueError)) as e:
                call()
            return str(e.value)
        return run

    def http(get: Callable[..., Any] | None = None, post: Callable[..., Any] | None = None) -> None:
        if get:
            monkeypatch.setattr(httpx, "get", get)
        if post:
            monkeypatch.setattr(httpx, "post", post)

    def answers(status: int, text: str = "") -> Callable[[], str]:
        def run() -> str:
            http(get=lambda *a, **k: Resp(status, text))
            return fails(lambda: zotero_push._web("GET", "/x", "k"))()
        return run

    def unreachable() -> str:
        def boom(*a: Any, **k: Any) -> None:
            raise httpx.ConnectError("boom")
        http(get=boom, post=boom)
        return fails(lambda: zotero_push._connector("/x"))() + " | " + fails(lambda: zotero_push._web("GET", "/x", "k"))()

    def desktop(up: bool) -> Callable[[], str]:
        def run() -> str:
            monkeypatch.setattr(zotero_push, "desktop", lambda: {"up": up, "prefs": {}})
            return fails(lambda: zotero_push.push("arxiv:2501.00001"))()
        return run

    def lookups() -> str:
        http(get=lambda *a, **k: Resp(404, "<feed></feed>"))
        return fails(lambda: zotero_push._arxiv_fetch("2501.00001"))() + " | " + fails(lambda: zotero_push._crossref_fetch("10.1/x"))()

    def warnings() -> str:
        def boom(*a: Any, **k: Any) -> None:
            raise ValueError("boom")
        http(get=boom)
        monkeypatch.setattr(zotero_push.sources, "acl_meta", boom)
        monkeypatch.setattr(zotero_push.sources, "openreview_meta", boom)
        meta = {**zotero_push.paper_meta("arxiv:2501.00001"), "doi": "10.1234/x", "acl": "2024.acl-long.1", "openreview": "abc"}
        zotero_push._meta_failed.clear()
        return " | ".join(zotero_push.enrich(meta))

    def missing_collection() -> str:
        config.save_settings({"zotero_collection": "XXXXXXXX"})
        return zotero_push._target_collection()[1] or ""

    def link_error(stem: str, base: str | None) -> Callable[[], str]:
        return fails(lambda: zotero_push.write_link(stem, key, base or zotero_push.link_preview(stem, key)["base"]))

    calls = {
        "no such paper": lambda: client.get("/api/zotero/paper/nosuch").json()["detail"],
        "no such note": lambda: client.post("/api/zotero/link/nosuch", json={"key": key, "base": "x"}).json()["detail"],
        "bad key (link)": lambda: client.post("/api/zotero/link/Plain", json={"key": "bad", "base": "x"}).json()["detail"],
        "bad key (open)": lambda: client.post("/api/zotero/open", json={"key": "bad"}).json()["detail"],
        "note changed": link_error("Plain", "stale"),
        "another link": link_error("Linked", None),
        "nothing to dedupe by": fails(lambda: zotero_push.push("Plain")),
        "desktop not running": desktop(False),
        "database not found": desktop(True),
        "web service answers": answers(500),
        "web key forbidden": answers(403),
        "web service busy": answers(503),
        "cannot connect": unreachable,
        "arXiv and Crossref lookups": lookups,
        "metadata warnings": warnings,
        "collection is gone": missing_collection,
    }
    assert not problems(calls)
