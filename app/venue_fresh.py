"""When a venue's hand-verified details (tracks, page limits, review rules) may be out of date.

A catalog entry (see `venues._catalog`) counts as stale when any of these holds:
- edition: the details describe an older edition than the upcoming one in ccfddl, or no edition is recorded;
- age: `verified_at` is `months` or more months ago (config.VENUE_STALE_MONTHS), or missing;
- link / dates: ccfddl shows a link or deadlines for the details' edition that the details do not have.
Once a check was confirmed in the app, `override.ccfddl_seen` remembers what ccfddl showed then, and only a
change since counts: a newer edition that was already looked at (say a placeholder page with no CFP yet), or
a link / date difference that was already looked at, does not keep the warning on.

Pure functions over the catalog entry; no I/O.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import config

TOLERANCE = timedelta(hours=36)  # AoE / UTC / PT renderings of the same deadline differ by up to about a day
RECENT = timedelta(days=30)      # without a baseline, ccfddl dates older than this are not compared

SHORT = {"edition": "有新一届", "age": "核对已久", "link": "链接有变", "dates": "截稿有变"}


def edition_year(detail: dict[str, Any] | None) -> int | None:
    """The year the details describe: the explicit `edition_year`, else the year in the `edition` name."""
    d = detail or {}
    if isinstance(d.get("edition_year"), int) and not isinstance(d["edition_year"], bool):
        return d["edition_year"]
    m = re.search(r"(20\d{2})", str(d.get("edition") or ""))
    return int(m.group(1)) if m else None


def verified_on(detail: dict[str, Any] | None) -> date | None:
    v = (detail or {}).get("verified_at")
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def months_between(a: date, b: date) -> int:
    """Whole months from a to b (0 when b is not a month later yet)."""
    return max(0, (b.year - a.year) * 12 + b.month - a.month - (1 if b.day < a.day else 0))


def norm_url(url: str | None) -> str:
    u = re.sub(r"^https?://(www\.)?", "", (url or "").strip().lower())
    return re.sub(r"(/index\.html?)?/*$", "", u.split("#")[0])


def _instants(ed: dict[str, Any]) -> list[str]:
    return sorted({t[k] for t in ed.get("timeline") or [] for k in ("abstract", "deadline") if t.get(k)})


def _edition(entry: dict[str, Any], year: int | None) -> dict[str, Any] | None:
    return next((e for e in reversed(entry.get("ccfddl_editions") or []) if year and e.get("year") == year), None)


def fingerprint(entry: dict[str, Any], year: int | None, now: datetime | None = None) -> dict[str, Any]:
    """What ccfddl shows now: the link and deadline instants (ISO UTC) of the `year` edition, and which edition is upcoming."""
    ed = _edition(entry, year) or {}
    return {"year": year, "link": norm_url(ed.get("link")), "dates": _instants(ed),
            "upcoming": upcoming_year(entry, now or datetime.now(timezone.utc))}


def upcoming_year(entry: dict[str, Any], now: datetime) -> int | None:
    """The edition ccfddl is collecting papers for: the earliest with a deadline ahead, else the latest listed."""
    eds = [e for e in entry.get("ccfddl_editions") or [] if e.get("year")]
    ahead = [e["year"] for e in eds if any(datetime.fromisoformat(i) >= now for i in _instants(e))]
    if ahead:
        return min(ahead)
    return max((e["year"] for e in eds), default=None)


def _known_links(detail: dict[str, Any]) -> set[str]:
    return {n for n in (norm_url(u) for u in [detail.get("homepage"), detail.get("cfp_url"), *(detail.get("sources") or [])]) if n}


def _detail_instants(detail: dict[str, Any]) -> list[datetime]:
    from .venues import _parse_official

    out = []
    for row in detail.get("deadlines") or []:
        for f in ("abstract", "paper"):
            iso = _parse_official(row.get(f))
            if iso:
                out.append(datetime.fromisoformat(iso))
    return out


def _day(iso: str) -> str:
    return iso[:10]


def _ccfddl_reasons(entry: dict[str, Any], detail: dict[str, Any], year: int, now: datetime) -> list[dict[str, str]]:
    if not _edition(entry, year):
        return []
    fp = fingerprint(entry, year, now)
    seen = (entry.get("override") or {}).get("ccfddl_seen")
    out = []
    if seen and seen.get("year") == year:
        if fp["link"] and fp["link"] != seen.get("link"):
            out.append({"code": "link", "text": f"ccfddl 的 {year} 届链接变了：{seen.get('link') or '（无）'} → {fp['link']}"})
        new, gone = sorted(set(fp["dates"]) - set(seen.get("dates") or [])), sorted(set(seen.get("dates") or []) - set(fp["dates"]))
        if new or gone:
            parts = ([f"新增 {'、'.join(map(_day, new))}"] if new else []) + ([f"移除 {'、'.join(map(_day, gone))}"] if gone else [])
            out.append({"code": "dates", "text": f"ccfddl 的 {year} 届截稿日期有变化：{'；'.join(parts)}"})
        return out
    known = _known_links(detail)
    if fp["link"] and fp["link"] not in known and not any(k.startswith(fp["link"] + "/") for k in known):
        out.append({"code": "link", "text": f"ccfddl 的 {year} 届链接 {fp['link']} 不在详情记录的官网 / CFP / 来源里"})
    mine = _detail_instants(detail)
    new = [i for i in fp["dates"] if datetime.fromisoformat(i) >= now - RECENT
           and not any(abs(datetime.fromisoformat(i) - m) <= TOLERANCE for m in mine)]
    if new:
        out.append({"code": "dates", "text": f"ccfddl 的 {year} 届有详情里没有的截稿日期：{'、'.join(map(_day, new))}"})
    return out


def check(entry: dict[str, Any], now: datetime | date | None = None, months: int | None = None) -> dict[str, Any] | None:
    """Freshness of one catalog entry's details; None when the venue has no details."""
    detail = entry.get("detail")
    if not detail:
        return None
    months = months or config.VENUE_STALE_MONTHS
    if isinstance(now, date) and not isinstance(now, datetime):
        now = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    year, upcoming, verified = edition_year(detail), upcoming_year(entry, now), verified_on(detail)
    reasons: list[dict[str, str]] = []
    if year is None:
        reasons.append({"code": "edition", "text": "没有记录这些详情属于哪一届"})
    elif upcoming and year < upcoming and upcoming > ((entry.get("override") or {}).get("ccfddl_seen") or {}).get("upcoming", 0):
        reasons.append({"code": "edition", "text": f"详情是 {year} 届的，ccfddl 已有 {upcoming} 届"})
    since = None
    if verified is None:
        reasons.append({"code": "age", "text": "没有记录上次核对的日期"})
    else:
        since = months_between(verified, now.date())
        if since >= months:
            reasons.append({"code": "age", "text": f"上次核对是 {verified.isoformat()}，已过 {since} 个月（超过 {months} 个月）"})
    if year:
        reasons += _ccfddl_reasons(entry, detail, year, now)
    for r in reasons:
        r["short"] = SHORT[r["code"]]
    return {"stale": bool(reasons), "reasons": reasons, "edition_year": year, "ccfddl_year": upcoming,
            "verified_at": verified.isoformat() if verified else None, "months_since": since, "limit_months": months}
