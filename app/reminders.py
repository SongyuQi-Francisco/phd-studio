"""Deadline and milestone reminders as macOS notifications.

Events are plan.timeline()'s: watched-venue deadlines (shown in local time), submission targets and
milestones/Gates. At each lead time (default 14, 7, 3 and 1 days before, and on the day) one
notification. A check (`studio remind --check`) sends what has come due; a LaunchAgent the user
installs from Settings runs it every hour, so it works with the workbench closed.

When a reminder comes due (due_times): at the daily reminder time (default 09:00) on its day; the
day-of one also at least DAY_OF_MARGIN before the deadline. One that would land in quiet hours
waits until they end, unless the deadline passes first: then it moves to just before they start.

Never twice for the same event and lead time:
- Settings and the log of sent reminders live in the vault next to plan.yaml (reminders.yaml, a
  sync-safe Store), so both Macs see the same ones. Only the designated Mac sends, and the shared
  log keeps a newly designated Mac from repeating what the old one already sent. Macs are told apart
  by a generated id kept in the data dir, not by the hostname, which changes with networks.
- The data dir (per machine, not synced) keeps this Mac's own record of what it sent and of its
  last check, so a lost Syncthing conflict or a failed vault write cannot cause a repeat here.
- A venue deadline's key is venue + abstract/paper + date, not its title (the title of the same
  deadline changes when ccfddl replaces the official-site fallback). A milestone's key has its date.
  A moved deadline or milestone starts its reminders again.
- When several lead times are due at once (the Mac was off), only the nearest one is sent.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import secrets
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Callable, Iterator

from . import notify, plan
from .config import DATA_DIR, DEVICE, PLAN_FILE
from .store import Store

FILE = PLAN_FILE.with_name("reminders.yaml")
LOCAL_FILE = DATA_DIR / "reminders.json"
MACHINE_FILE = DATA_DIR / "machine-id"
LOCK = DATA_DIR / "reminders-check.lock"

TYPES = ("target", "milestone", "venue")
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "leads": [14, 7, 3, 1, 0],  # days before; 0 = on the day
    "types": {t: True for t in TYPES},
    "quiet": False,
    "quiet_start": "22:00",
    "quiet_end": "08:00",
    "time": "09:00",  # when a day's reminders come due
    "device": "",  # machine_id() of the one Mac that sends; this Mac when reminders are turned on
}
OPEN_TARGET = ("planning", "writing")  # a submitted / accepted target no longer needs its deadlines
DAY_OF_MARGIN = timedelta(hours=3)
CHECK_EVERY = timedelta(hours=1)  # the LaunchAgent's interval (remind_agent.py)
MAX_LEAD = 90
MAX_PER_RUN = 4  # more than this at once: the most urgent ones, then one summary
KEEP_SENT_DAYS = 30  # sent-log entries are dropped this long after their event
HEARTBEAT = timedelta(hours=6)

store = Store(FILE, ("settings", "devices", "sent"),
              "# Deadline reminders, managed by PhD Studio (Settings → 截稿提醒). Shared by your Macs.\n")

KIND_LABEL = {"milestone": "里程碑", "gate": "Gate", "kill": "Kill 点", "deadline": "截止"}
WEEKDAYS = "一二三四五六日"


# ---------- this Mac ----------

def machine_id() -> str:
    """A stable id for this Mac, made once and kept in its (unsynced) data dir."""
    try:
        mid = MACHINE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        mid = ""
    if not re.fullmatch(r"[0-9a-f]{12}", mid):
        mid = secrets.token_hex(6)
        MACHINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MACHINE_FILE.write_text(mid + "\n", encoding="utf-8")
    return mid


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
        elif k in ("quiet_start", "quiet_end", "time"):
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
            rec["device"] = machine_id()
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
    open_targets: dict[str, list[dict[str, Any]]] = {}
    for t in tl["targets"]:  # every target, not just the one the timeline shows per venue
        if t.get("status", "planning") in OPEN_TARGET:
            open_targets.setdefault(t["venue"], []).append(t)
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
            # A submitted or accepted target does not silence the venue (next year's edition, another
            # paper aimed there): its deadlines stay reminders for a watched venue.
            tgts = open_targets.get(e["venue"], [])
            stage = e.get("stage") or ("abstract" if "摘要" in e["title"] else "deadline")
            out.append({"key": f"v:{e['venue']}:{stage}:{at.astimezone(timezone.utc).date()}",
                        "type": "target" if tgts else "venue", "title": e["title"], "at": at, "venue": e["venue"],
                        "targets": [{"id": t["id"], "title": t["title"], "line": t.get("line")} for t in tgts],
                        "ccf": e.get("ccf"), "core": e.get("core"),
                        "open": {"view": "timeline", "venue": e["venue"]}})
    return out


def sent_id(key: str, lead: int) -> str:
    return "s" + hashlib.sha1(f"{key}#{lead}".encode()).hexdigest()[:12]


def in_quiet(cfg: dict[str, Any], now: datetime, tz: tzinfo | None = None) -> bool:
    if not cfg.get("quiet"):
        return False
    t = _local(now, tz).time()
    start, end = time.fromisoformat(cfg["quiet_start"]), time.fromisoformat(cfg["quiet_end"])
    if start == end:
        return False
    return start <= t < end if start < end else (t >= start or t < end)


def _wall(day: date, hhmm: str, tz: tzinfo | None) -> datetime:
    """That local wall-clock time on that day."""
    naive = datetime.combine(day, time.fromisoformat(hhmm))
    return naive.replace(tzinfo=tz) if tz else naive.astimezone()


def _quiet_period(cfg: dict[str, Any], t: datetime, tz: tzinfo | None) -> tuple[datetime, datetime] | None:
    """(start, end) of the quiet hours t falls in, or None."""
    if not in_quiet(cfg, t, tz):
        return None
    lt = _local(t, tz)
    day, qs, qe = lt.date(), cfg["quiet_start"], cfg["quiet_end"]
    if time.fromisoformat(qs) < time.fromisoformat(qe):
        return _wall(day, qs, tz), _wall(day, qe, tz)
    if lt.time() >= time.fromisoformat(qs):
        return _wall(day, qs, tz), _wall(day + timedelta(days=1), qe, tz)
    return _wall(day - timedelta(days=1), qs, tz), _wall(day, qe, tz)


def due_times(e: dict[str, Any], cfg: dict[str, Any], tz: tzinfo | None = None) -> dict[int, datetime]:
    """When each lead time's reminder for the event comes due."""
    at = e["at"]
    day = _local(at, tz).date()
    out = {}
    for lead in cfg["leads"]:
        t = _wall(day - timedelta(days=lead), cfg["time"], tz)
        if lead == 0:  # on the day, and before the deadline even when it is early in the morning
            t = max(min(t, at - DAY_OF_MARGIN), _wall(day, "00:00", tz))
        quiet = _quiet_period(cfg, t, tz)
        if quiet:  # after quiet hours; before them when the deadline would pass meanwhile
            t = quiet[1] if quiet[1] < at else quiet[0] - CHECK_EVERY
        out[lead] = t
    return out


def due(evs: list[dict[str, Any]], cfg: dict[str, Any], now: datetime, sent: set[str],
        tz: tzinfo | None = None) -> list[dict[str, Any]]:
    """Reminders to send now, most urgent first: per event, the nearest lead time that has come due.
    Pure: no clock, no files."""
    today = _local(now, tz).date()
    out, seen = [], set()
    for e in evs:
        if not cfg["types"].get(e["type"], True) or e["at"] <= now:
            continue
        reached = [lead for lead, t in due_times(e, cfg, tz).items() if t <= now]
        if not reached:
            continue
        lead = min(reached)
        sid = sent_id(e["key"], lead)
        if sid in sent or sid in seen:  # seen: two tracks with the same deadline are one reminder
            continue
        seen.add(sid)
        out.append({**e, "lead": lead, "days_left": (_local(e["at"], tz).date() - today).days, "id": sid})
    out.sort(key=lambda r: r["at"])
    return out


def schedule(evs: list[dict[str, Any]], cfg: dict[str, Any], now: datetime, sent: set[str],
             tz: tzinfo | None = None, days: int = 30) -> list[dict[str, Any]]:
    """Reminders coming within `days`, in order: what is due now, then each later lead time."""
    out = [{**r, "due": now} for r in due(evs, cfg, now, sent, tz)]
    seen = {r["id"] for r in out}
    for e in evs:
        if not cfg["types"].get(e["type"], True) or e["at"] <= now:
            continue
        times = due_times(e, cfg, tz)
        reached = [lead for lead, t in times.items() if t <= now]
        for lead, t in times.items():
            sid = sent_id(e["key"], lead)
            if now < t <= now + timedelta(days=days) and (not reached or lead < min(reached)) \
                    and sid not in sent and sid not in seen:
                seen.add(sid)
                left = (_local(e["at"], tz).date() - _local(t, tz).date()).days
                out.append({**e, "lead": lead, "days_left": left, "id": sid, "due": t})
    out.sort(key=lambda r: (r["due"], r["at"]))
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
        sub = "投稿目标 · " + "、".join(t["title"] for t in r.get("targets") or [])
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
    mid = machine_id()
    rec = next((r for r in store.load()["devices"] if r["id"] == mid), None)
    fresh = rec and datetime.fromisoformat(rec.get("seen_at", "1970-01-01T00:00:00+00:00")) > now - HEARTBEAT
    if rec and fresh and not force and rec.get("name") == DEVICE and (agent is None or rec.get("agent") == agent):
        return
    with store.editing() as data:
        rec = next((r for r in data["devices"] if r["id"] == mid), None)
        if rec is None:
            rec = {"id": mid, "created_at": _stamp(now)}
            data["devices"].append(rec)
        rec["name"] = DEVICE  # the hostname, for display only
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
    """Send the reminders that are due. Returns what happened; dry_run sends and writes nothing.

    Every real run leaves its outcome (or its error) in the data dir for Settings to show."""
    now = now or datetime.now(timezone.utc)
    try:
        return _check(now, dry_run, send or notify.send, tz)
    except Exception as e:
        if not dry_run:
            _finish({"at": _stamp(now), "error": f"{type(e).__name__}: {e}"[:400]})
        raise


def _check(now: datetime, dry_run: bool, send: Sender, tz: tzinfo | None) -> dict[str, Any]:
    with _check_lock():
        cfg = settings()
        res: dict[str, Any] = {"at": _stamp(now), "device": DEVICE, "sent": [], "failed": [], "due": []}
        if not cfg["enabled"]:
            res["skipped"] = "off"
        elif cfg["device"] and cfg["device"] != machine_id():
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
    local["last"] = {k: res[k] for k in ("at", "sent", "failed", "skipped", "via", "error") if res.get(k)}
    local["last"]["ok"] = not res.get("error") and not res.get("failed")
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
    mid = machine_id()
    devices = sorted(store.load()["devices"], key=lambda d: d.get("seen_at", ""), reverse=True)
    if not any(d["id"] == mid for d in devices):
        devices.insert(0, {"id": mid, "name": DEVICE})
    upcoming = schedule(events(), cfg, now, sent_ids()) if cfg["enabled"] else []
    local = _read_local()
    today = _local(now, None).date()
    return {
        "settings": cfg, "device": mid, "device_name": DEVICE, "designated": cfg["device"] or "",
        "is_designated": not cfg["device"] or cfg["device"] == mid,
        "devices": [{"id": d["id"], "name": d.get("name") or d["id"], "seen_at": d.get("seen_at"),
                     "agent": d.get("agent")} for d in devices],
        "agent": remind_agent.status(),
        "notify": {"via": notify.method(), "in_app": notify.in_app(), "auth": notify.authorization()},
        "last_check": local.get("last"), "last_test": local.get("last_test"),
        "upcoming": [{**message(r), "on": _local(r["due"], None).date().isoformat(), "lead": r["lead"],
                      "type": r["type"], "days": (_local(r["due"], None).date() - today).days} for r in upcoming[:12]],
        "upcoming_total": len(upcoming),
    }
