"""app/venue_fresh.py: when a venue's hand-verified details count as out of date. Pure functions, no I/O."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app import config, venue_fresh as vf

NOW = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)


def ed(year, link=None, *dates):
    """A ccfddl edition whose paper deadlines are the given ISO instants."""
    return {"year": year, "link": link, "timeline": [{"abstract": None, "deadline": d, "comment": None} for d in dates]}


def detail(**kw):
    base = {"key": "ACL", "status": "confirmed", "edition": "ACL 2027", "edition_year": 2027, "verified_at": "2026-10-24",
            "homepage": "https://2027.example.org/", "cfp_url": "https://2027.example.org/calls/main/",
            "sources": ["https://2027.example.org/calls/main/"],
            "deadlines": [{"track": "Main", "abstract": None, "paper": "2027-01-05 AoE"}]}
    return {**base, **kw}


def entry(d="default", eds=(), override=None):
    return {"detail": detail() if d == "default" else d, "ccfddl_editions": list(eds), "override": override}


def codes(f):
    return [r["code"] for r in f["reasons"]]


# ---------- small helpers ----------


@pytest.mark.parametrize("d,year", [
    ({"edition_year": 2027, "edition": "ACL 2026"}, 2027),   # explicit wins
    ({"edition": "SIGIR 2027"}, 2027),
    ({"edition": "AAAI-27"}, None),                           # no four-digit year and no explicit one
    ({"edition_year": True, "edition": "X 2026"}, 2026),      # a bool is not a year
    ({}, None), (None, None),
])
def test_edition_year(d, year):
    assert vf.edition_year(d) == year


@pytest.mark.parametrize("v,expected", [
    ("2026-10-24", date(2026, 10, 24)), ("2026-10-24T08:00:00", date(2026, 10, 24)),
    (date(2026, 10, 24), date(2026, 10, 24)), (datetime(2026, 10, 24, 9), date(2026, 10, 24)),
    ("last week", None), (None, None), ("", None),
])
def test_verified_on(v, expected):
    assert vf.verified_on({"verified_at": v}) == expected


@pytest.mark.parametrize("a,b,months", [
    (date(2026, 4, 25), date(2026, 10, 25), 6),
    (date(2026, 4, 26), date(2026, 10, 25), 5),       # one day short of six months
    (date(2025, 12, 31), date(2026, 6, 30), 5),
    (date(2025, 10, 25), date(2026, 10, 25), 12),
    (date(2026, 10, 25), date(2026, 10, 25), 0),
    (date(2026, 11, 1), date(2026, 10, 25), 0),       # verified "in the future" is never negative
])
def test_months_between(a, b, months):
    assert vf.months_between(a, b) == months


@pytest.mark.parametrize("u,n", [
    ("https://www.Example.org/path/", "example.org/path"), ("http://example.org", "example.org"),
    ("https://example.org/Conferences/2027/index.html", "example.org/conferences/2027"),
    ("https://example.org/x#frag", "example.org/x"), (None, ""), ("", ""),
])
def test_norm_url(u, n):
    assert vf.norm_url(u) == n


def test_fingerprint_is_the_link_and_sorted_unique_dates_of_one_edition_and_the_upcoming_year():
    e = entry(eds=[ed(2026, "https://a/"), ed(2027, "https://www.B.org/", "2027-02-02T00:00:00+00:00", "2027-01-01T00:00:00+00:00",
                                                "2027-01-01T00:00:00+00:00")])
    assert vf.fingerprint(e, 2027, NOW) == {"year": 2027, "link": "b.org", "upcoming": 2027, "newer": {},
                                            "dates": ["2027-01-01T00:00:00+00:00", "2027-02-02T00:00:00+00:00"]}
    assert vf.fingerprint(e, 2030, NOW) == {"year": 2030, "link": "", "dates": [], "upcoming": 2027, "newer": {}}
    assert vf.fingerprint(entry(), None, NOW)["upcoming"] is None


def test_fingerprint_also_keeps_what_ccfddl_shows_for_every_newer_edition():
    e = entry(eds=[ed(2026, "https://a/", "2026-01-01T00:00:00+00:00"), ed(2027), ed(2028, "https://c.org", "2028-03-03T00:00:00+00:00")])
    fp = vf.fingerprint(e, 2026, NOW)
    assert fp["newer"] == {"2027": {"link": "", "dates": []}, "2028": {"link": "c.org", "dates": ["2028-03-03T00:00:00+00:00"]}}
    assert vf.fingerprint(e, None, NOW)["newer"] == {}


# ---------- upcoming edition ----------


def test_upcoming_year_is_the_earliest_edition_with_a_deadline_ahead():
    e = entry(eds=[ed(2026, None, "2026-05-01T00:00:00+00:00"), ed(2027, None, "2027-01-05T00:00:00+00:00"), ed(2028)])
    assert vf.upcoming_year(e, NOW) == 2027


def test_upcoming_year_falls_back_to_the_latest_listed_when_nothing_is_ahead():
    e = entry(eds=[ed(2026, None, "2026-05-01T00:00:00+00:00"), ed(2027)])
    assert vf.upcoming_year(e, NOW) == 2027
    assert vf.upcoming_year(entry(), NOW) is None


# ---------- rule 1: edition ----------


def test_older_details_than_the_upcoming_ccfddl_edition_are_stale():
    f = vf.check(entry(detail(edition="RecSys 2026", edition_year=2026), [ed(2026, None, "2026-04-01T00:00:00+00:00"), ed(2027)]), NOW)
    assert f["stale"] and codes(f) == ["edition"]
    assert "2026" in f["reasons"][0]["text"] and "2027" in f["reasons"][0]["text"]
    assert f["reasons"][0]["short"] == "有新一届"
    assert (f["edition_year"], f["ccfddl_year"]) == (2026, 2027)


def test_details_for_the_edition_ccfddl_is_collecting_are_fresh():
    f = vf.check(entry(eds=[ed(2026, None, "2026-04-01T00:00:00+00:00"), ed(2027, "https://2027.example.org/", "2027-01-05T11:59:59+00:00")]), NOW)
    assert not f["stale"] and f["reasons"] == []


def test_details_of_the_latest_edition_ccfddl_lists_are_not_stale_by_edition():
    d = detail(edition="CIKM 2026", edition_year=2026, status="previous-edition")
    assert not vf.check(entry(d, [ed(2025), ed(2026)]), NOW)["stale"]


def test_a_later_edition_without_dates_still_makes_details_stale_once_the_current_one_is_over():
    d = detail(edition="ICLR 2027", edition_year=2027)
    f = vf.check(entry(d, [ed(2027, None, "2026-09-25T00:00:00+00:00"), ed(2028)]), NOW)
    assert codes(f) == ["edition"]


def test_while_the_current_edition_still_has_a_deadline_ahead_the_next_one_does_not_count():
    f = vf.check(entry(eds=[ed(2027, "https://2027.example.org/", "2027-01-05T11:59:59+00:00"), ed(2028)]), NOW)
    assert not f["stale"]


def test_no_ccfddl_data_and_unknown_edition():
    assert not vf.check(entry(eds=[]), NOW)["stale"]
    f = vf.check(entry(detail(edition="next year", edition_year=None)), NOW)
    assert codes(f) == ["edition"] and "哪一届" in f["reasons"][0]["text"]


def test_no_details_no_verdict():
    assert vf.check(entry(None), NOW) is None
    assert vf.check({"detail": None}, NOW) is None


# ---------- rule 2: age ----------


@pytest.mark.parametrize("verified,stale", [("2026-04-25", True), ("2026-04-26", False), ("2025-01-01", True), ("2026-10-25", False)])
def test_details_go_stale_after_the_configured_number_of_months(verified, stale):
    f = vf.check(entry(detail(verified_at=verified)), NOW, months=6)
    assert f["stale"] is stale
    assert codes(f) == (["age"] if stale else [])
    assert f["limit_months"] == 6


def test_age_reason_says_when_and_how_long():
    r = vf.check(entry(detail(verified_at="2026-01-10")), NOW, months=6)["reasons"][0]
    assert "2026-01-10" in r["text"] and "9 个月" in r["text"] and "6 个月" in r["text"] and r["short"] == "核对已久"


def test_missing_or_unreadable_verification_date_is_stale():
    for v in (None, "someday"):
        f = vf.check(entry(detail(verified_at=v)), NOW)
        assert codes(f) == ["age"] and f["verified_at"] is None and f["months_since"] is None


def test_the_limit_comes_from_config_unless_given(monkeypatch):
    e = entry(detail(verified_at="2026-07-25"))
    assert not vf.check(e, NOW)["stale"]                       # three months, default limit is six
    monkeypatch.setattr(config, "VENUE_STALE_MONTHS", 3)
    f = vf.check(e, NOW)
    assert f["stale"] and f["limit_months"] == 3


def test_now_may_be_a_date():
    assert vf.check(entry(detail(verified_at="2026-01-01")), date(2026, 10, 25))["stale"]


# ---------- rule 3, no earlier check: ccfddl against the details ----------


def test_ccfddl_link_that_is_the_homepage_or_cfp_page_is_known():
    for link in ("https://www.2027.example.org", "https://2027.example.org/calls/main", "http://2027.example.org/index.html"):
        f = vf.check(entry(eds=[ed(2027, link, "2027-01-05T11:59:59+00:00")]), NOW)
        assert not f["stale"], link


def test_ccfddl_link_above_a_known_page_is_known():
    f = vf.check(entry(eds=[ed(2027, "https://2027.example.org/calls", "2027-01-05T11:59:59+00:00")]), NOW)
    assert not f["stale"]


@pytest.mark.parametrize("link", ["https://other.example.com/", "https://2027.example.org/new-cfp-page"])
def test_a_ccfddl_link_the_details_do_not_have_is_flagged(link):
    f = vf.check(entry(eds=[ed(2027, link, "2027-01-05T11:59:59+00:00")]), NOW)
    assert codes(f) == ["link"] and f["reasons"][0]["short"] == "链接有变"


def test_ccfddl_deadline_the_details_do_not_have_is_flagged():
    # details say 2027-01-05 AoE (= 2027-01-06T11:59:59Z); ccfddl moved it a week
    f = vf.check(entry(eds=[ed(2027, "https://2027.example.org/", "2027-01-13T11:59:59+00:00")]), NOW)
    assert codes(f) == ["dates"] and "2027-01-13" in f["reasons"][0]["text"] and f["reasons"][0]["short"] == "Deadline 有变"


@pytest.mark.parametrize("iso", ["2027-01-06T11:59:59+00:00", "2027-01-06T11:59:00+00:00", "2027-01-05T23:59:59+00:00", "2027-01-07T11:59:59+00:00"])
def test_deadlines_within_a_day_and_a_half_are_the_same_deadline(iso):
    assert not vf.check(entry(eds=[ed(2027, "https://2027.example.org/", iso)]), NOW)["stale"]


def test_old_ccfddl_dates_the_details_dropped_are_not_flagged():
    f = vf.check(entry(eds=[ed(2027, "https://2027.example.org/", "2026-07-20T11:59:59+00:00", "2027-01-06T11:59:59+00:00")]), NOW)
    assert not f["stale"]


def test_details_without_deadlines_are_flagged_against_a_ccfddl_date():
    d = detail(deadlines=[])
    assert codes(vf.check(entry(d, [ed(2027, "https://2027.example.org/", "2027-01-06T11:59:59+00:00")]), NOW)) == ["dates"]


def test_only_the_details_own_edition_is_compared():
    # ccfddl's 2027 edition has a different link and date, but the details are about 2026
    d = detail(edition="CIKM 2026", edition_year=2026)
    f = vf.check(entry(d, [ed(2026, "https://2027.example.org/", "2026-01-06T11:59:59+00:00"), ed(2027, "https://x.example.com/", "2027-05-01T00:00:00+00:00")]), NOW)
    assert codes(f) == ["edition"]


# ---------- rule 3, after a check: only changes since then ----------


def seen(**kw):
    return {"year": 2027, "link": "other.example.com", "dates": ["2027-01-13T11:59:59+00:00"], **kw}


def test_a_difference_that_was_already_checked_stays_quiet():
    e = entry(eds=[ed(2027, "https://other.example.com/", "2027-01-13T11:59:59+00:00")], override={"ccfddl_seen": seen()})
    assert not vf.check(e, NOW)["stale"]


def test_a_new_ccfddl_link_after_the_check_is_flagged():
    e = entry(eds=[ed(2027, "https://newer.example.com/", "2027-01-13T11:59:59+00:00")], override={"ccfddl_seen": seen()})
    f = vf.check(e, NOW)
    assert codes(f) == ["link"] and "other.example.com" in f["reasons"][0]["text"] and "newer.example.com" in f["reasons"][0]["text"]


def test_moved_deadlines_after_the_check_are_flagged_with_what_was_added_and_removed():
    e = entry(eds=[ed(2027, "https://other.example.com/", "2027-01-20T11:59:59+00:00")], override={"ccfddl_seen": seen()})
    f = vf.check(e, NOW)
    assert codes(f) == ["dates"]
    assert "新增 2027-01-20" in f["reasons"][0]["text"] and "移除 2027-01-13" in f["reasons"][0]["text"]


def test_a_check_of_another_edition_is_no_baseline():
    e = entry(eds=[ed(2027, "https://other.example.com/", "2027-01-13T11:59:59+00:00")], override={"ccfddl_seen": seen(year=2026)})
    assert set(codes(vf.check(e, NOW))) == {"link", "dates"}


def test_a_newer_edition_that_was_already_looked_at_does_not_keep_the_warning_on():
    d = detail(edition="RecSys 2026", edition_year=2026)
    eds = [ed(2026, None, "2026-04-01T00:00:00+00:00"), ed(2027)]
    assert codes(vf.check(entry(d, eds), NOW)) == ["edition"]
    looked_at = vf.fingerprint(entry(d, eds), 2026, NOW)
    assert codes(vf.check(entry(d, eds, override={"ccfddl_seen": looked_at}), NOW)) == []


def test_an_even_newer_edition_after_the_check_is_flagged_again():
    d = detail(edition="RecSys 2026", edition_year=2026)
    eds = [ed(2026, None, "2026-04-01T00:00:00+00:00"), ed(2027)]
    looked_at = vf.fingerprint(entry(d, eds), 2026, NOW)
    f = vf.check(entry(d, [*eds, ed(2028)], override={"ccfddl_seen": looked_at}), NOW)
    assert codes(f) == ["edition"] and "2028" in f["reasons"][0]["text"]


RECSYS = dict(edition="RecSys 2026", edition_year=2026)


def looked_at(eds, d=None, **kw):
    """The override a confirmed check leaves behind for these ccfddl editions."""
    return {"ccfddl_seen": {**vf.fingerprint(entry(d or detail(**RECSYS), eds), 2026, NOW), **kw}}


def test_a_placeholder_edition_that_later_gets_deadlines_is_flagged_again():
    placeholder = [ed(2026, None, "2026-04-01T00:00:00+00:00"), ed(2027)]
    d = detail(**RECSYS)
    override = looked_at(placeholder)
    assert vf.check(entry(d, placeholder, override=override), NOW)["stale"] is False
    published = [placeholder[0], ed(2027, "https://recsys.example/27/", "2027-04-14T11:59:59+00:00")]
    f = vf.check(entry(d, published, override=override), NOW)
    assert codes(f) == ["edition"] and f["stale"]
    assert "2027" in f["reasons"][0]["text"] and "2027-04-14" in f["reasons"][0]["text"] and "recsys.example/27" in f["reasons"][0]["text"]
    assert f["reasons"][0]["short"] == "有新一届"


def test_a_placeholder_edition_that_only_gets_a_link_is_flagged_too():
    placeholder = [ed(2026, None, "2026-04-01T00:00:00+00:00"), ed(2027)]
    f = vf.check(entry(detail(**RECSYS), [placeholder[0], ed(2027, "https://recsys.example/27/")], override=looked_at(placeholder)), NOW)
    assert codes(f) == ["edition"] and "链接" in f["reasons"][0]["text"]


def test_a_later_edition_with_dates_is_noticed_even_when_the_seen_upcoming_was_a_later_placeholder():
    # at the check ccfddl listed 2027 and 2028 without dates (upcoming = the latest listed, 2028); now 2027 has dates
    before = [ed(2026, None, "2026-04-01T00:00:00+00:00"), ed(2027), ed(2028)]
    after = [before[0], ed(2027, None, "2027-04-14T11:59:59+00:00"), ed(2028)]
    f = vf.check(entry(detail(**RECSYS), after, override=looked_at(before)), NOW)
    assert codes(f) == ["edition"] and "2027-04-14" in f["reasons"][0]["text"]


def test_a_newer_edition_nothing_changed_for_stays_quiet():
    eds = [ed(2026, None, "2026-04-01T00:00:00+00:00"), ed(2027, "https://recsys.example/27/", "2027-04-14T11:59:59+00:00")]
    assert not vf.check(entry(detail(**RECSYS), eds, override=looked_at(eds)), NOW)["stale"]


def test_a_missing_upcoming_in_the_remembered_check_never_raises():
    # a venue confirmed while ccfddl had no edition at all stores upcoming: None (yaml null)
    e = entry(detail(**RECSYS), [ed(2026), ed(2027)], override={"ccfddl_seen": {"year": 2026, "link": "", "dates": [], "upcoming": None, "newer": {}}})
    assert codes(vf.check(e, NOW)) == ["edition"]
    e = entry(detail(**RECSYS), [ed(2026), ed(2027)], override={"ccfddl_seen": {"year": None, "upcoming": None}})
    assert codes(vf.check(e, NOW)) == ["edition"]
    assert vf.check(entry(eds=[]), NOW) is not None


def test_age_and_edition_still_apply_after_a_check():
    e = entry(detail(verified_at="2025-01-01"), [ed(2027, "https://other.example.com/", "2027-01-13T11:59:59+00:00")], override={"ccfddl_seen": seen()})
    assert codes(vf.check(e, NOW)) == ["age"]


def test_several_reasons_are_all_reported():
    d = detail(edition="RecSys 2026", edition_year=2026, verified_at="2025-01-01")
    f = vf.check(entry(d, [ed(2026), ed(2027)]), NOW)
    assert codes(f) == ["edition", "age"] and all(r["short"] for r in f["reasons"])


# ---------- the configured limit ----------


@pytest.mark.parametrize("value,expected", [(3, 3), (12, 12), (0, 6), (-2, 6), ("6", 6), (True, 6), (None, 6), (2.5, 6)])
def test_the_stale_months_setting_must_be_a_positive_whole_number(value, expected):
    assert config._months(value, 6) == expected


def test_the_sandbox_uses_the_default_limit():
    assert config.VENUE_STALE_MONTHS == 6
