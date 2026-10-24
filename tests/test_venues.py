"""app/venues.py: deadline / timezone parsing, acronym matching, catalog build and search ranking. No network."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import venues


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---------- keys and aliases ----------


@pytest.mark.parametrize("acronym,key", [("KDD", "kdd"), ("SIGKDD", "sigkdd"), ("Web Conf", "webconf"),
                                         ("IEEE/ACM TASLP", "ieeeacmtaslp"), ("NAACL-HLT '27", "naaclhlt27")])
def test_key_is_lowercase_alphanumerics(acronym, key):
    assert venues._key(acronym) == key


CAT = {k: {} for k in ("kdd", "icde", "sigir", "mm", "security", "sigmod", "ir")}


@pytest.mark.parametrize("raw,expected", [
    ("kdd", "kdd"),                   # already a CORE acronym
    ("sigkdd", "kdd"),                # ccfddl title SIGKDD -> CORE KDD
    ("ieeeicde", "icde"),
    ("acmmm", "mm"),
    ("ieeeacmmm", "mm"),
    ("usenixsecurity", "security"),
    ("sigopsmm", "mm"),
    ("brandnew", "brandnew"),         # unknown stays as is
    ("sigfoo", "sigfoo"),             # prefix alone is not enough: the rest must be a known acronym
    ("acm", "acm"),
])
def test_match_maps_prefixed_titles_onto_core_acronyms(raw, expected):
    assert venues._match(raw, CAT) == expected


def test_match_prefers_an_exact_acronym_over_stripping_a_prefix():
    # SIGIR is its own CORE entry even though stripping "sig" would give "ir"
    assert venues._match("sigir", CAT) == "sigir"
    assert venues._match("sigmod", CAT) == "sigmod"


# ---------- timezones ----------


@pytest.mark.parametrize("name,hours", [
    (None, -12), ("", -12), ("AoE", -12), ("aoe", -12),
    ("UTC", 0), ("GMT", 0), ("utc", 0),
    ("UTC+8", 8), ("UTC-8", -8), ("UTC+08", 8), ("GMT+1", 1), ("UTC +9", 9), ("UTC+0", 0),
    ("UTC+5:30", 5.5), ("UTC-3:30", -3.5), ("UTC+0530", 5.5),
    ("Europe/Paris", -12),   # unknown names fall back to AoE, the most lenient choice
])
def test_tz_offsets(name, hours):
    assert venues._tz(name).utcoffset(None) == timedelta(hours=hours)


# ---------- _when ----------


def test_when_converts_wall_clock_in_the_conference_timezone_to_utc():
    assert venues._when("2027-01-14 23:59:59", venues._tz("AoE")) == "2027-01-15T11:59:59+00:00"
    assert venues._when("2027-01-14 23:59:59", venues._tz("UTC+8")) == "2027-01-14T15:59:59+00:00"
    assert venues._when("2027-01-14 00:30:00", venues._tz("UTC-8")) == "2027-01-14T08:30:00+00:00"
    assert venues._when("2027-01-14 12:00:00", venues._tz("UTC")) == "2027-01-14T12:00:00+00:00"


def test_when_date_only_means_end_of_that_day():
    assert venues._when("2027-01-14", venues._tz("UTC")) == "2027-01-14T23:59:59+00:00"
    assert venues._when("  2027-01-14  ", venues._tz("UTC")) == "2027-01-14T23:59:59+00:00"


def test_when_ignores_a_trailing_zone_name_after_the_time():
    assert venues._when("2027-01-14 23:59:59 AoE", venues._tz("UTC")) == "2027-01-14T23:59:59+00:00"


@pytest.mark.parametrize("value", [None, "", "TBD", "tbd - announced later", "soon", "2027-13-45", 20270114, ["2027-01-14"]])
def test_when_returns_none_for_tbd_and_garbage(value):
    assert venues._when(value, venues._tz("UTC")) is None


def test_when_crosses_year_and_month_boundaries():
    assert venues._when("2026-12-31 23:59:59", venues._tz("AoE")) == "2027-01-01T11:59:59+00:00"
    assert venues._when("2027-02-28", venues._tz("AoE")) == "2027-03-01T11:59:59+00:00"


# ---------- _parse_official (dates as written on a CFP page) ----------


@pytest.mark.parametrize("text,expected", [
    ("2027-01-22 23:59 AoE", "2027-01-23T11:59:59+00:00"),
    ("2027-01-22 AoE", "2027-01-23T11:59:59+00:00"),
    ("2027-01-22", "2027-01-23T11:59:59+00:00"),                 # no zone: AoE
    ("2027-01-22 (AoE)", "2027-01-23T11:59:59+00:00"),
    ("2027-01-22 11:59 PT", "2027-01-22T19:59:59+00:00"),
    ("2027-01-22 11:59 PST", "2027-01-22T19:59:59+00:00"),
    ("2027-07-22 11:59 PDT", "2027-07-22T18:59:59+00:00"),
    ("2027-01-22 23:59 UTC+8", "2027-01-22T15:59:59+00:00"),
    ("2027-01-22 23:59 UTC + 8", "2027-01-22T15:59:59+00:00"),
    ("2027-01-22T23:59 UTC", "2027-01-22T23:59:59+00:00"),
    ("2027-01-22 9:30 GMT+1", "2027-01-22T08:30:59+00:00"),
    ("Jan 22 (PROPOSED) 2027-01-22 AoE", "2027-01-23T11:59:59+00:00"),
])
def test_parse_official(text, expected):
    assert venues._parse_official(text) == expected


@pytest.mark.parametrize("text", [None, "", "TBA", "January 22", "to be announced"])
def test_parse_official_none_without_an_iso_date(text):
    assert venues._parse_official(text) is None


# ---------- _official_edition ----------


def detail(**kw):
    base = {"key": "ACL", "status": "confirmed", "edition": "ACL 2027", "cfp_url": "https://cfp.example",
            "homepage": "https://home.example", "dates": "Aug 1-6, 2027", "location": "Vienna",
            "deadlines": [{"track": "Main conference", "abstract": "2027-02-14 AoE", "paper": "2027-02-21 AoE"}]}
    return {**base, **kw}


def test_official_edition_builds_a_timeline_from_confirmed_details():
    ed = venues._official_edition(detail())
    assert ed["year"] == 2027 and ed["link"] == "https://cfp.example" and ed["place"] == "Vienna"
    assert ed["timeline"] == [{"abstract": "2027-02-15T11:59:59+00:00", "deadline": "2027-02-22T11:59:59+00:00",
                               "comment": "Main conference（官网）"}]


@pytest.mark.parametrize("d", [None, {}, detail(status="previous-edition"), detail(status="planned"), detail(deadlines=[]),
                               detail(deadlines=[{"track": "Main", "abstract": None, "paper": None}])])
def test_official_edition_only_for_confirmed_editions_with_dates(d):
    assert venues._official_edition(d) is None


def test_official_edition_skips_non_paper_tracks():
    rows = [{"track": "Workshop proposals", "paper": "2027-01-01 AoE"},
            {"track": "Tutorial proposals", "paper": "2027-01-02 AoE"},
            {"track": "Doctoral Consortium", "paper": "2027-01-03 AoE"},
            {"track": "Camera-ready", "paper": "2027-01-04 AoE"},
            {"track": "Research track", "paper": "2027-01-05 AoE"},
            {"track": None, "paper": "2027-01-06 AoE"}]
    ed = venues._official_edition(detail(deadlines=rows))
    assert [t["comment"] for t in ed["timeline"]] == ["Research track（官网）", "主会（官网）"]


def test_official_edition_falls_back_to_homepage_and_no_year():
    ed = venues._official_edition(detail(cfp_url=None, edition="next year"))
    assert ed["link"] == "https://home.example" and ed["year"] is None


def test_real_venue_details_file_is_well_formed():
    """data/venue_details.json ships with the repo; every confirmed deadline must parse, or it is silently dropped."""
    real = venues.STUDIO_DIR / "data" / "venue_details.json"
    data = json.loads(real.read_text(encoding="utf-8"))
    keys = [v["key"] for v in data["venues"]]
    assert len(keys) == len(set(keys))
    for v in data["venues"]:
        assert v["status"] in ("confirmed", "previous-edition"), v["key"]
        if v["status"] != "confirmed":
            continue
        for row in v["deadlines"]:
            for field in ("abstract", "paper"):
                if row.get(field):
                    assert venues._parse_official(row[field]), f"{v['key']}: {row[field]!r}"


# ---------- catalog ----------


def test_catalog_without_data_or_network_is_empty():
    assert venues.catalog() == {}  # refresh() is blocked in tests; the failure is swallowed


def test_refresh_is_skipped_while_the_cache_is_fresh(venue_data):
    assert venues.refresh() == {"ok": True, "fetched_at": venues.fetched_at(), "skipped": True}


def test_refresh_goes_to_the_network_when_the_cache_is_a_week_old(venue_data):
    old = time.time() - 8 * 86400
    os.utime(venues.STAMP, (old, old))
    with pytest.raises(RuntimeError, match="network access"):
        venues.refresh()


def test_catalog_merges_ccfddl_titles_onto_core_acronyms(venue_data):
    cat = venues.catalog()
    assert "sigkdd" not in cat
    kdd = cat["kdd"]
    assert (kdd["acronym"], kdd["core"], kdd["ccf"]) == ("KDD", "A*", "A")
    assert kdd["title"] == "ACM SIGKDD Conference on Knowledge Discovery and Data Mining"
    assert kdd["field"] == "数据库/数据挖掘/信息检索"
    assert kdd["dblp"] == "kdd"
    assert [e["year"] for e in kdd["editions"]] == sorted(e["year"] for e in kdd["editions"])
    assert len(kdd["editions"]) == 2


def test_catalog_keeps_core_only_and_ccf_only_venues(venue_data):
    cat = venues.catalog()
    assert cat["pakdd"]["core"] == "B" and cat["pakdd"]["ccf"] is None and cat["pakdd"]["editions"] == []
    fake = cat["fakeconf"]
    assert fake["acronym"] == "FAKECONF" and fake["ccf"] == "C" and fake["core"] is None
    assert fake["title"] == "A conference only ccfddl knows"
    assert cat["cikm"]["core"] == "A" and cat["cikm"]["ccf"] == "B"


def test_catalog_converts_deadlines_to_utc_using_each_editions_timezone(venue_data):
    kdd = venues.catalog()["kdd"]
    row = kdd["editions"][-1]["timeline"][0]
    assert row["abstract"] == venue_data.kdd_abstract.isoformat()
    assert row["deadline"] == venue_data.kdd_paper.isoformat()
    assert row["comment"] == "Research Track"
    assert kdd["editions"][-1]["timezone"] == "UTC+8"
    cikm = venues.catalog()["cikm"]["editions"][0]["timeline"][0]
    assert cikm["deadline"] == venue_data.cikm_paper.isoformat() and cikm["abstract"] is None


def test_catalog_tbd_deadlines_become_none(venue_data):
    acl = venues.catalog()["acl"]
    tbd = acl["editions"][-1]["timeline"][0]
    assert tbd["abstract"] is None and tbd["deadline"] is None


def test_catalog_accept_rates_skip_missing_values(venue_data):
    rates = venues.catalog()["kdd"]["accept_rates"]
    assert [(r["year"], r["rate"]) for r in rates] == [(2023, 0.19), (2024, 0.22)]
    assert venues.catalog()["cikm"]["accept_rates"] == []


def test_catalog_is_cached_until_the_stamp_changes(venue_data):
    assert venues.catalog() is venues.catalog()


def test_catalog_uses_official_dates_when_ccfddl_has_nothing_upcoming(venue_data, tmp_path, monkeypatch):
    details = tmp_path / "details.json"
    details.write_text(json.dumps({"venues": [
        detail(key="ACL", deadlines=[{"track": "Main", "abstract": "2099-01-14 AoE", "paper": "2099-01-21 AoE"}]),
        detail(key="KDD", deadlines=[{"track": "Main", "paper": "2099-01-21 AoE"}]),
        detail(key="CIKM", status="previous-edition"),
    ]}), encoding="utf-8")
    monkeypatch.setattr(venues, "DETAILS_FILE", details)
    venues._catalog.cache_clear()
    cat = venues.catalog()
    acl = cat["acl"]
    assert acl["detail"]["edition"] == "ACL 2027"
    assert acl["editions"][-1]["timezone"] == "官网"
    assert acl["editions"][-1]["timeline"][0]["deadline"] == "2099-01-22T11:59:59+00:00"
    assert len(cat["kdd"]["editions"]) == 2          # ccfddl already has an upcoming deadline: no official edition added
    assert len(cat["cikm"]["editions"]) == 1         # previous-edition details are not used for deadlines


# ---------- next_deadline / summary ----------


def test_next_deadline_picks_the_nearest_upcoming_date(venue_data):
    nxt = venues.next_deadline(venues.catalog()["kdd"])
    assert nxt["kind"] == "abstract" and nxt["at"] == venue_data.kdd_abstract.isoformat()
    assert nxt["paper_at"] == venue_data.kdd_paper.isoformat()
    assert nxt["days"] in (19, 20)
    assert nxt["link"] == "https://kdd.example/next"
    assert "past" not in nxt


def test_next_deadline_without_abstract_deadline(venue_data):
    nxt = venues.next_deadline(venues.catalog()["cikm"])
    assert nxt["kind"] == "deadline" and nxt["paper_at"] == nxt["at"]


def test_next_deadline_after_everything_passed_returns_the_latest_past_one(venue_data):
    nxt = venues.next_deadline(venues.catalog()["acl"])
    assert nxt["past"] is True and nxt["kind"] == "deadline"
    assert nxt["at"] == venue_data.past.isoformat()


def test_next_deadline_for_a_venue_without_dates_is_none(venue_data):
    assert venues.next_deadline(venues.catalog()["fakeconf"]) is None
    assert venues.next_deadline(venues.catalog()["pakdd"]) is None


def test_next_deadline_honours_an_explicit_now(venue_data):
    cat = venues.catalog()
    later = venue_data.kdd_abstract + timedelta(days=1)
    nxt = venues.next_deadline(cat["kdd"], now=later)
    assert nxt["kind"] == "deadline" and nxt["at"] == venue_data.kdd_paper.isoformat()
    way_later = venue_data.kdd_paper + timedelta(days=1)
    assert venues.next_deadline(cat["kdd"], now=way_later)["past"] is True


def test_summary_carries_ranks_next_deadline_and_latest_accept_rate(venue_data):
    s = venues.summary(venues.catalog()["kdd"])
    assert (s["acronym"], s["core"], s["ccf"]) == ("KDD", "A*", "A")
    assert s["next"]["kind"] == "abstract"
    assert s["accept_rate"]["year"] == 2024
    assert s["has_detail"] is False
    assert venues.summary(venues.catalog()["cikm"])["accept_rate"] is None


# ---------- search ----------


def acronyms(rows):
    return [r["acronym"] for r in rows]


def test_search_ranks_exact_then_prefix_then_substring(venue_data):
    assert acronyms(venues.search("kdd")) == ["KDD", "KDDM", "PAKDD"]


def test_search_is_case_insensitive_and_trims(venue_data):
    assert acronyms(venues.search("  KdD ")) == ["KDD", "KDDM", "PAKDD"]


def test_search_also_matches_titles(venue_data):
    assert acronyms(venues.search("computational linguistics")) == ["ACL"]
    assert "CIKM" in acronyms(venues.search("information and knowledge"))


def test_search_title_only_hits_fall_back_to_rank_order(venue_data):
    # "data mining" is in several titles and in no acronym, so ordering falls back to rank
    got = acronyms(venues.search("data mining"))
    assert got == ["KDD", "PAKDD", "KDDM"]  # A* before B before C


def test_search_without_query_orders_by_best_rank_then_acronym(venue_data):
    assert acronyms(venues.search()) == ["ACL", "KDD", "CIKM", "PAKDD", "FAKECONF", "KDDM"]


def test_search_filters_by_ccf_and_core(venue_data):
    assert acronyms(venues.search(ccf="A")) == ["ACL", "KDD"]
    assert acronyms(venues.search(ccf="C")) == ["FAKECONF"]
    assert acronyms(venues.search(core="A*")) == ["ACL", "KDD"]
    assert acronyms(venues.search(core="B")) == ["PAKDD"]
    assert acronyms(venues.search("kdd", ccf="A")) == ["KDD"]
    assert venues.search(ccf="A", core="C") == []


def test_search_limit_and_unknown_query(venue_data):
    assert acronyms(venues.search(limit=2)) == ["ACL", "KDD"]
    assert venues.search("zzzz") == []


def test_get_returns_detail_with_links_and_only_the_last_three_editions(venue_data):
    g = venues.get("KDD")
    assert g["acronym"] == "KDD" and g["core_source"] == "ICORE2026"
    assert g["core_url"] == "https://portal.core.edu.au/conf-ranks/101/"
    assert g["dblp_url"] == "https://dblp.org/db/conf/kdd/"
    assert len(g["editions"]) == 2 and [r["year"] for r in g["accept_rates"]] == [2023, 2024]
    assert venues.get("kdd")["acronym"] == "KDD"
    assert venues.get("FakeConf")["core_url"] is None and venues.get("FakeConf")["dblp_url"] is None
    assert venues.get("nope") is None


def test_get_trims_to_three_editions(venue_data):
    cat = venues.catalog()
    cat["kdd"]["editions"] = [{"year": y, "timeline": []} for y in range(2020, 2026)]
    assert [e["year"] for e in venues.get("kdd")["editions"]] == [2023, 2024, 2025]


def test_stats_counts_ranked_venues(venue_data):
    s = venues.stats()
    assert s["total"] == 6 and s["core"] == 5 and s["ccf"] == 4
    assert s["core_source"] == "ICORE2026" and s["fetched_at"]
