"""When a venue's hand-verified details (tracks, page limits, review rules) may be out of date.

A catalog entry (see `venues._catalog`) counts as stale when any of these holds:
- edition: the details describe an older edition than the upcoming one in ccfddl, or no edition is recorded;
- age: `verified_at` is `months` or more months ago (config.VENUE_STALE_MONTHS), or missing;
- link / dates: ccfddl shows a link or deadlines for the details' edition that the details do not have.
Once a check was confirmed in the app, `override.ccfddl_seen` remembers what ccfddl showed then (for the
details' edition and for every newer one), and only a change since counts: a newer edition that was already
looked at (say a placeholder page with no CFP yet) stays quiet until it gets a link or deadlines, and a
difference that was already looked at does not keep the warning on.

Pure functions over the catalog entry; no I/O. The reasons are worded in this Mac's interface language
(`i18n.t`), each with a short label for the chip (`short`).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import config, i18n

TOLERANCE = timedelta(hours=36)  # AoE / UTC / PT renderings of the same deadline differ by up to about a day
RECENT = timedelta(days=30)      # without a baseline, ccfddl dates older than this are not compared


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


def _newer(entry: dict[str, Any], year: int | None) -> list[dict[str, Any]]:
    return [e for e in entry.get("ccfddl_editions") or [] if year and e.get("year") and e["year"] > year]


def _shown(ed: dict[str, Any]) -> dict[str, Any]:
    return {"link": norm_url(ed.get("link")), "dates": _instants(ed)}


def fingerprint(entry: dict[str, Any], year: int | None, now: datetime | None = None) -> dict[str, Any]:
    """What ccfddl shows now: the link and deadline instants (ISO UTC) of the `year` edition and of every newer
    one, and which edition is upcoming."""
    return {"year": year, **_shown(_edition(entry, year) or {}), "upcoming": upcoming_year(entry, now or datetime.now(timezone.utc)),
            "newer": {str(e["year"]): _shown(e) for e in _newer(entry, year)}}


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


def _changes(now_shown: dict[str, Any], was: dict[str, Any]) -> tuple[str | None, str | None]:
    """How what ccfddl shows differs from what it showed at the last check: (link change, date change) as text."""
    link = f"{was.get('link') or i18n.t('venue.fresh.no_link')} → {now_shown['link']}" if now_shown["link"] and now_shown["link"] != was.get("link") else None
    new = sorted(set(now_shown["dates"]) - set(was.get("dates") or []))
    gone = sorted(set(was.get("dates") or []) - set(now_shown["dates"]))
    items = i18n.t("venue.sep.items")
    parts = ([i18n.t("venue.fresh.added", days=items.join(map(_day, new)))] if new else []) \
        + ([i18n.t("venue.fresh.removed", days=items.join(map(_day, gone)))] if gone else [])
    return link, i18n.t("venue.sep.clauses").join(parts) or None


def _newer_reasons(entry: dict[str, Any], year: int, seen: dict[str, Any]) -> list[dict[str, str]]:
    """A newer edition ccfddl already listed at the last check has since got a link or changed deadlines."""
    out = []
    for e in _newer(entry, year):
        was = (seen.get("newer") or {}).get(str(e["year"]))
        if was is None:
            continue                    # not listed then: the edition rule covers a new one
        link, dates = _changes(_shown(e), was)
        if link or dates:
            what = (i18n.t("venue.fresh.part_link", link=link) if link else None, i18n.t("venue.fresh.part_dates", dates=dates) if dates else None)
            out.append({"code": "edition", "text": i18n.t("venue.fresh.newer_info", year=e["year"],
                                                          what=i18n.t("venue.sep.clauses").join(x for x in what if x))})
    return out


def _ccfddl_reasons(entry: dict[str, Any], detail: dict[str, Any], year: int, now: datetime) -> list[dict[str, str]]:
    if not _edition(entry, year):
        return []
    fp = fingerprint(entry, year, now)
    seen = (entry.get("override") or {}).get("ccfddl_seen")
    out = []
    if seen and seen.get("year") == year:
        link, dates = _changes(fp, seen)
        if link:
            out.append({"code": "link", "text": i18n.t("venue.fresh.link_changed", year=year, link=link)})
        if dates:
            out.append({"code": "dates", "text": i18n.t("venue.fresh.dates_changed", year=year, dates=dates)})
        return out
    known = _known_links(detail)
    if fp["link"] and fp["link"] not in known and not any(k.startswith(fp["link"] + "/") for k in known):
        out.append({"code": "link", "text": i18n.t("venue.fresh.link_unknown", year=year, link=fp["link"])})
    mine = _detail_instants(detail)
    new = [i for i in fp["dates"] if datetime.fromisoformat(i) >= now - RECENT
           and not any(abs(datetime.fromisoformat(i) - m) <= TOLERANCE for m in mine)]
    if new:
        out.append({"code": "dates", "text": i18n.t("venue.fresh.dates_new", year=year, dates=i18n.t("venue.sep.items").join(map(_day, new)))})
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
    seen = (entry.get("override") or {}).get("ccfddl_seen") or {}
    reasons: list[dict[str, str]] = []
    if year is None:
        reasons.append({"code": "edition", "text": i18n.t("venue.fresh.no_edition")})
    elif upcoming and year < upcoming:
        if upcoming > (seen.get("upcoming") or 0):
            reasons.append({"code": "edition", "text": i18n.t("venue.fresh.older_edition", year=year, upcoming=upcoming)})
        else:
            reasons += _newer_reasons(entry, year, seen)
    since = None
    if verified is None:
        reasons.append({"code": "age", "text": i18n.t("venue.fresh.no_verified")})
    else:
        since = months_between(verified, now.date())
        if since >= months:
            reasons.append({"code": "age", "text": i18n.t("venue.fresh.age", date=verified.isoformat(), n=since, limit=months)})
    if year:
        reasons += _ccfddl_reasons(entry, detail, year, now)
    for r in reasons:
        r["short"] = i18n.t(f"venue.short.{r['code']}")  # i18n-keys: venue.short.{edition,age,link,dates}
    return {"stale": bool(reasons), "reasons": reasons, "edition_year": year, "ccfddl_year": upcoming,
            "verified_at": verified.isoformat() if verified else None, "months_since": since, "limit_months": months}
