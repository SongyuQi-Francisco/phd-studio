"""Deadline and milestone reminders as macOS notifications.

Events are plan.timeline()'s: watched-venue deadlines (shown in local time), submission targets and
milestones/Gates. At each lead time (default 14, 7, 3 and 1 days before, and on the day) one
notification. A check (`studio remind --check`) sends what has come due; a LaunchAgent the user
installs from Settings runs it a few times a day, so it works with the workbench closed.

Never twice for the same event and lead time:
- Settings and the log of sent reminders live in the vault next to plan.yaml (reminders.yaml, a
  sync-safe Store), so both Macs see the same ones. Only the designated device sends, and the shared
  log keeps a newly designated device from repeating what the old one already sent.
- state/<device>/reminders.json keeps this device's own record of what it sent and its last check, so
  a lost Syncthing conflict or a failed vault write cannot cause a repeat on this Mac.
- An event's key includes its date: a moved deadline or milestone starts its reminders again.
- When several lead times are due at once (the Mac was off), only the nearest one is sent.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Callable, Iterator

from . import notify, plan
from .config import DATA_DIR, DEVICE, PLAN_FILE, STATE_DIR
from .store import Store

FILE = PLAN_FILE.with_name("reminders.yaml")
LOCAL_FILE = STATE_DIR / "reminders.json"
LOCK = DATA_DIR / "reminders-check.lock"

TYPES = ("target", "milestone", "venue")
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "leads": [14, 7, 3, 1, 0],  # days before; 0 = on the day
    "types": {t: True for t in TYPES},
    "quiet": False,
    "quiet_start": "22:00",
    "quiet_end": "08:00",
    "device": "",  # the one Mac that sends; set to this Mac when reminders are turned on
}
MAX_LEAD = 90
MAX_PER_RUN = 4  # more than this at once: the most urgent ones, then one summary
KEEP_SENT_DAYS = 30  # sent-log entries are dropped this long after their event
HEARTBEAT = timedelta(hours=6)

store = Store(FILE, ("settings", "devices", "sent"),
              "# Deadline reminders, managed by PhD Studio (Settings → 截稿提醒). Shared by your Macs.\n")

KIND_LABEL = {"milestone": "里程碑", "gate": "Gate", "kill": "Kill 点", "deadline": "截止"}
WEEKDAYS = "一二三四五六日"


# ---------- settings ----------

def settings() -> dict[str, Any]:
    rec = next((r for r in store.load()["settings"] if r["id"] == "settings"), {})
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k in DEFAULTS:
        if k in rec:
            out[k] = {**DEFAULTS["types"], **rec[k]} if k == "types" else rec[k]
    out["leads"] = sorted({int(x) for x in out["leads"]}, reverse=True)
    return out


def _hhmm(value: Any) -> str:
    t = time.fromisoformat(str(value))
    return f"{t.hour:02d}:{t.minute:02d}"


def _validate(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in patch.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting {k}")
        if k in ("enabled", "quiet"):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
            out[k] = v
        elif k == "leads":
            leads = sorted({int(x) for x in v}, reverse=True)
            if not leads or any(x < 0 or x > MAX_LEAD for x in leads):
                raise ValueError(f"lead times must be 0–{MAX_LEAD} days, at least one")
            out[k] = leads
        elif k == "types":
            types = {t: bool(v.get(t, True)) for t in TYPES} if isinstance(v, dict) else None
            if not types or not any(types.values()):
                raise ValueError("remind about at least one kind of event")
            out[k] = types
        elif k in ("quiet_start", "quiet_end"):
            out[k] = _hhmm(v)
        else:
            out[k] = str(v or "").strip()
    return out


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    clean = _validate(patch)
    with store.editing() as data:
        rec = next((r for r in data["settings"] if r["id"] == "settings"), None)
        if rec is None:
            rec = {"id": "settings", "created_at": _stamp()}
            data["settings"].append(rec)
        rec.update(clean)
        if rec.get("enabled") and not rec.get("device"):
            rec["device"] = DEVICE
        rec["updated_at"] = _stamp()
    return settings()


# ---------- events and what is due ----------

def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone().isoformat(timespec="seconds")


def _local(dt: datetime, tz: tzinfo | None) -> datetime:
    """Local wall time; tz=None is this Mac's zone (DST-aware)."""
    return dt.astimezone(tz) if tz else dt.astimezone()


def events() -> list[dict[str, Any]]:
    """The timeline's upcoming events, normalised for reminders (key, type, title, at, open, …)."""
    tl = plan.timeline(days_back=1, days_ahead=MAX_LEAD + 400)
    targets = {t["id"]: t for t in tl["targets"]}
    dates = {m["id"]: m.get("date") for m in tl["milestones"]}
    out: list[dict[str, Any]] = []
    for e in tl["events"]:
        at = datetime.fromisoformat(e["at"])
        if e["type"] == "milestone":
            if e.get("status") == "done":
                continue
            out.append({"key": f"m:{e['id']}:{dates.get(e['id'])}", "type": "milestone", "title": e["title"],
                        "at": at, "kind": e.get("kind") or "milestone", "line": e.get("line"),
                        "open": {"view": "plan", "milestone": e["id"]}})
        else:
            tgt = e.get("target")
            if tgt and (targets.get(tgt["id"]) or {}).get("status") in ("submitted", "accepted"):
                continue  # already sent in: no deadline reminders for it
            out.append({"key": f"v:{e['venue']}:{e['title']}:{e['at']}", "type": "target" if tgt else "venue",
                        "title": e["title"], "at": at, "venue": e["venue"], "target": tgt,
                        "ccf": e.get("ccf"), "core": e.get("core"),
                        "open": {"view": "timeline", "venue": e["venue"]}})
    return out


def sent_id(key: str, lead: int) -> str:
    return "s" + hashlib.sha1(f"{key}#{lead}".encode()).hexdigest()[:12]


def _lead_for(days_left: int, leads: list[int]) -> int | None:
    """The nearest lead time that has come due, or None when the event is further off than all."""
    due = [x for x in leads if x >= days_left]
    return min(due) if due else None


def in_quiet(cfg: dict[str, Any], now: datetime, tz: tzinfo | None = None) -> bool:
    if not cfg.get("quiet"):
        return False
    t = _local(now, tz).time()
    start, end = time.fromisoformat(cfg["quiet_start"]), time.fromisoformat(cfg["quiet_end"])
    if start == end:
        return False
    return start <= t < end if start < end else (t >= start or t < end)


def due(evs: list[dict[str, Any]], cfg: dict[str, Any], now: datetime, sent: set[str],
        tz: tzinfo | None = None) -> list[dict[str, Any]]:
    """Reminders to send now, most urgent first. Pure: no clock, no files."""
    today = _local(now, tz).date()
    out = []
    for e in evs:
        if not cfg["types"].get(e["type"], True) or e["at"] <= now:
            continue
        days_left = (_local(e["at"], tz).date() - today).days
        lead = _lead_for(days_left, cfg["leads"])
        if lead is None or sent_id(e["key"], lead) in sent:
            continue
        out.append({**e, "lead": lead, "days_left": days_left, "id": sent_id(e["key"], lead)})
    out.sort(key=lambda r: r["at"])
    return out


def schedule(evs: list[dict[str, Any]], cfg: dict[str, Any], now: datetime, sent: set[str],
             tz: tzinfo | None = None, days: int = 30) -> list[dict[str, Any]]:
    """Upcoming reminders within `days`, by local date: what is due now, then each later lead time."""
    today = _local(now, tz).date()
    out = [{**r, "on": today.isoformat()} for r in due(evs, cfg, now, sent, tz)]
    for e in evs:
        if not cfg["types"].get(e["type"], True) or e["at"] <= now:
            continue
        day = _local(e["at"], tz).date()
        for lead in cfg["leads"]:
            on = day - timedelta(days=lead)
            if today < on <= today + timedelta(days=days) and sent_id(e["key"], lead) not in sent:
                out.append({**e, "lead": lead, "days_left": lead, "id": sent_id(e["key"], lead), "on": on.isoformat()})
    out.sort(key=lambda r: (r["on"], r["at"]))
    return out


# ---------- message text ----------

def _when(d: datetime) -> str:
    return f"{d.month}月{d.day}日 周{WEEKDAYS[d.weekday()]}"


def _left(days_left: int, milestone: bool) -> str:
    verb = "到期" if milestone else "截止"
    return {0: f"今天{verb}", 1: f"明天{verb}"}.get(days_left, f"还有 {days_left} 天")


def message(r: dict[str, Any], tz: tzinfo | None = None) -> dict[str, str]:
    """Title, subtitle and body of one reminder's notification."""
    at = _local(r["at"], tz)
    if r["type"] == "milestone":
        sub = " · ".join(x for x in (KIND_LABEL.get(r.get("kind"), "里程碑"), r.get("line")) if x)
        return {"title": f"{r['title']} · {_left(r['days_left'], True)}", "subtitle": sub, "body": f"{_when(at)} 到期"}
    if r["type"] == "target":
        tgt = r.get("target") or {}
        sub = "投稿目标 · " + (tgt.get("title") or "")
    else:
        ranks = " / ".join(x for x in (r.get("ccf") and f"CCF {r['ccf']}", r.get("core") and f"CORE {r['core']}") if x)
        sub = "关注的会议" + (f" · {ranks}" if ranks else "")
    return {"title": f"{r['title']} · {_left(r['days_left'], False)}", "subtitle": sub,
            "body": f"{_when(at)} {at:%H:%M} 截止（本地时间）"}


# ---------- state ----------

def _read_local() -> dict[str, Any]:
    try:
        return json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_local(state: dict[str, Any]) -> None:
    LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOCAL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LOCAL_FILE)


def sent_ids() -> set[str]:
    """Everything already sent, by any device (vault log) or by this one (local record)."""
    return {r["id"] for r in store.load()["sent"]} | set(_read_local().get("sent", {}))


@contextmanager
def _check_lock() -> Iterator[None]:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def mark_device(agent: bool | None = None, now: datetime | None = None, force: bool = False) -> None:
    """Note in the shared file that this Mac is around (at most every few hours, or when told)."""
    now = now or datetime.now(timezone.utc)
    rec = next((r for r in store.load()["devices"] if r["id"] == DEVICE), None)
    fresh = rec and datetime.fromisoformat(rec.get("seen_at", "1970-01-01T00:00:00+00:00")) > now - HEARTBEAT
    if rec and fresh and not force and (agent is None or rec.get("agent") == agent):
        return
    with store.editing() as data:
        rec = next((r for r in data["devices"] if r["id"] == DEVICE), None)
        if rec is None:
            rec = {"id": DEVICE, "created_at": _stamp(now)}
            data["devices"].append(rec)
        rec["seen_at"] = _stamp(now)
        if agent is not None:
            rec["agent"] = agent
        rec["updated_at"] = _stamp(now)


def _after(value: Any, when: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(value)) > when
    except ValueError:
        return False


def _record(sent: list[dict[str, Any]], now: datetime) -> None:
    local = _read_local()
    log = local.setdefault("sent", {})
    for r in sent:
        log[r["id"]] = _stamp(now)
    # A reminder goes out at most MAX_LEAD days before its event, so older entries can never match again.
    keep = (now - timedelta(days=KEEP_SENT_DAYS + MAX_LEAD)).date().isoformat()
    local["sent"] = {k: v for k, v in log.items() if v[:10] >= keep}
    _write_local(local)
    old = now - timedelta(days=KEEP_SENT_DAYS)
    with store.editing() as data:
        have = {r["id"] for r in data["sent"]}
        data["sent"] = [r for r in data["sent"] if _after(r.get("at"), old)]
        for r in sent:
            if r["id"] not in have:
                data["sent"].append({"id": r["id"], "event": r["key"], "lead": r["lead"], "title": r["title"],
                                     "at": r["at"].isoformat(), "device": DEVICE, "sent_at": _stamp(now),
                                     "created_at": _stamp(now), "updated_at": _stamp(now)})


# ---------- the check ----------

Sender = Callable[..., dict[str, Any]]


def check(now: datetime | None = None, dry_run: bool = False, send: Sender | None = None,
          tz: tzinfo | None = None) -> dict[str, Any]:
    """Send the reminders that are due. Returns what happened; dry_run sends and writes nothing."""
    now = now or datetime.now(timezone.utc)
    send = send or notify.send
    with _check_lock():
        cfg = settings()
        res: dict[str, Any] = {"at": _stamp(now), "device": DEVICE, "sent": [], "failed": [], "due": []}
        if not cfg["enabled"]:
            res["skipped"] = "off"
        elif cfg["device"] and cfg["device"] != DEVICE:
            res.update(skipped="other_device", designated=cfg["device"])
        elif in_quiet(cfg, now, tz):
            res["skipped"] = "quiet"
        if not dry_run and cfg["enabled"]:
            mark_device(now=now)
        if res.get("skipped") and not dry_run:
            return _finish(res)
        todo = due(events(), cfg, now, sent_ids(), tz)
        res["due"] = [{**message(r, tz), "id": r["id"], "lead": r["lead"]} for r in todo]
        if dry_run:
            return res
        batch = todo if len(todo) <= MAX_PER_RUN else todo[:MAX_PER_RUN - 1]
        done: list[dict[str, Any]] = []
        for r in batch:
            m = message(r, tz)
            out = send(m["title"], m["body"], subtitle=m["subtitle"], open_=r["open"], ident=r["id"])
            res["via"] = out.get("via")
            if out.get("ok"):
                done.append(r)
            else:
                res["failed"].append({"title": m["title"], "error": out.get("error")})
                if out.get("denied"):
                    break
        rest = todo[len(batch):]
        if rest and len(done) == len(batch):
            names = "、".join(r["title"] for r in rest[:3]) + ("等" if len(rest) > 3 else "")
            out = send(f"另有 {len(rest)} 项临近", names, subtitle="PhD Studio 截稿提醒", open_={"view": "timeline"},
                       ident=f"summary-{now:%Y%m%d%H%M}")
            if out.get("ok"):
                done += rest
        res["sent"] = [message(r, tz)["title"] for r in done]
        if done:
            _record(done, now)
        return _finish(res)


def _finish(res: dict[str, Any]) -> dict[str, Any]:
    local = _read_local()
    local["last"] = {k: res.get(k) for k in ("at", "sent", "failed", "skipped", "via")}
    _write_local(local)
    return res


def test_notification() -> dict[str, Any]:
    out = notify.send("PhD Studio 截稿提醒", "测试通知：之后截稿和里程碑临近时会这样提醒你。",
                      subtitle="这是一条测试通知", open_={"view": "timeline"}, ask=True)
    local = _read_local()
    local["last_test"] = {"at": _stamp(), **{k: out.get(k) for k in ("ok", "via", "error")}}
    _write_local(local)
    return out


def status(now: datetime | None = None) -> dict[str, Any]:
    """Everything the Settings section shows."""
    from . import remind_agent

    now = now or datetime.now(timezone.utc)
    cfg = settings()
    data = store.load()
    devices = sorted(data["devices"], key=lambda d: d.get("seen_at", ""), reverse=True)
    if not any(d["id"] == DEVICE for d in devices):
        devices.insert(0, {"id": DEVICE})
    upcoming = schedule(events(), cfg, now, sent_ids()) if cfg["enabled"] else []
    local = _read_local()
    return {
        "settings": cfg, "device": DEVICE, "designated": cfg["device"] or "",
        "is_designated": not cfg["device"] or cfg["device"] == DEVICE,
        "devices": [{k: d.get(k) for k in ("id", "seen_at", "agent")} for d in devices],
        "agent": remind_agent.status(),
        "notify": {"via": notify.method(), "in_app": notify.in_app(), "auth": notify.authorization()},
        "last_check": local.get("last"), "last_test": local.get("last_test"),
        "upcoming": [{**message(r), "on": r["on"], "lead": r["lead"], "type": r["type"],
                      "days": (date.fromisoformat(r["on"]) - _local(now, None).date()).days} for r in upcoming[:12]],
        "upcoming_total": len(upcoming),
    }
