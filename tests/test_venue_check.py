"""app/venue_check.py, app/venue_api.py and the studio tool: the assistant proposes, only the user's confirm writes."""
from __future__ import annotations

import asyncio
import json
import re

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, plan, studio_tools, venue_api, venue_check as vc, venue_fresh, venue_overrides, venues

from .venue_helpers import TODAY, acl, own_details_file, shipped  # noqa: F401  (autouse fixture)

SRC = "https://2028.example.org/cfp"


def stale_acl(**kw):
    """ACL details last verified long ago, so the age rule fires."""
    return acl(verified_at="2025-01-01", **kw)


def override_file_exists():
    return config.VENUE_OVERRIDES_FILE.exists()


# ---------- validating a proposal ----------


def test_a_proposal_must_cite_sources():
    with pytest.raises(ValueError, match="官方网址"):
        vc.clean_patch({"edition": "ACL 2028"}, [])
    with pytest.raises(ValueError, match="http"):
        vc.clean_patch({}, ["acl2028.org"])


def test_sources_inside_details_count_and_are_merged_without_duplicates():
    p = vc.clean_patch({"sources": [SRC]}, [SRC, "https://x.example/"])
    assert p == {"_sources": [SRC, "https://x.example/"]}


@pytest.mark.parametrize("details", [{"verified_at": "2026-01-01"}, {"key": "X"}, {"name": "X"}, {"bogus": 1}])
def test_fields_the_assistant_may_not_set_are_refused(details):
    with pytest.raises(ValueError, match="不能修改"):
        vc.clean_patch(details, [SRC])


@pytest.mark.parametrize("details,msg", [
    ({"status": "planned"}, "status"), ({"edition_year": "2028"}, "edition_year"), ({"edition_year": 1999}, "edition_year"),
    ({"homepage": "ftp://x"}, "http"), ({"deadlines": {"a": 1}}, "对象列表"), ({"deadlines": [{"track": "m", "date": "x"}]}, "只能有"),
    ({"tracks": ["Long"]}, "对象列表"), ({"format": {"bogus": 1}}, "format"), ({"format": "x"}, "format"),
    ({"edition": ["ACL"]}, "文字"),
])
def test_malformed_fields_are_refused(details, msg):
    with pytest.raises(ValueError, match=msg):
        vc.clean_patch(details, [SRC])


def test_a_new_edition_name_brings_its_year_or_must_be_given_one():
    assert vc.clean_patch({"edition": "ACL 2028"}, [SRC])["edition_year"] == 2028
    assert vc.clean_patch({"edition": "ACL 2028", "edition_year": 2029}, [SRC])["edition_year"] == 2029
    with pytest.raises(ValueError, match="edition_year"):
        vc.clean_patch({"edition": "AAAI-28"}, [SRC])


def test_clean_patch_normalises_values():
    p = vc.clean_patch({"edition": " ACL 2028 ", "dates": "", "notes": ["a", " ", "b"], "location": None,
                        "deadlines": [{"track": "Main", "paper": " 2028-02-01 AoE "}],
                        "format": {"notable_rules": "One rule", "review_model": None, "template": "T"}}, [SRC])
    assert p["edition"] == "ACL 2028" and "dates" not in p and "location" not in p
    assert p["notes"] == ["a", "b"]
    assert p["deadlines"] == [{"track": "Main", "abstract": None, "paper": "2028-02-01 AoE"}]
    assert p["format"] == {"notable_rules": ["One rule"], "template": "T"}


# ---------- diff ----------


def test_diff_shows_changed_scalars_and_list_rows_as_removed_and_added_lines():
    before = acl()
    after = vc.venue_overrides.merge_detail(before, {
        "edition": "ACL 2028", "deadlines": [{"track": "Main", "abstract": "2028-01-01 AoE", "paper": "2028-01-08 AoE"}],
        "tracks": [{"name": "Long", "page_limit": "8 pages", "notes": None}, {"name": "Short", "page_limit": "4 pages", "notes": "no appendix"}],
        "format": {"notable_rules": ["Anonymous", "Reviewers must register"]}})
    diff = {d["field"]: d for d in vc.diff_detail(before, after)}
    assert list(diff) == ["edition", "deadlines", "tracks", "format.notable_rules"]       # in display order
    assert diff["edition"]["lines"] == [{"op": "-", "text": "ACL 2027"}, {"op": "+", "text": "ACL 2028"}]
    assert diff["deadlines"]["lines"] == [{"op": "-", "text": "Main：全文 2099-01-05 AoE"},
                                          {"op": "+", "text": "Main：摘要 2028-01-01 AoE · 全文 2028-01-08 AoE"}]
    assert diff["tracks"]["lines"] == [{"op": "+", "text": "Short：4 pages（no appendix）"}]     # unchanged rows are not repeated
    assert diff["format.notable_rules"]["lines"] == [{"op": "+", "text": "Reviewers must register"}]
    assert diff["edition"]["label"] == "届次"


def test_diff_of_equal_details_is_empty_and_ignores_the_check_date():
    assert vc.diff_detail(acl(), vc.venue_overrides.merge_detail(acl(), {"verified_at": "2030-01-01"})) == []


def test_diff_for_a_venue_without_details_lists_everything_as_added():
    diff = vc.diff_detail({}, acl())
    assert all(l["op"] == "+" for d in diff for l in d["lines"]) and {"status", "edition"} <= {d["field"] for d in diff}


# ---------- propose: nothing is written ----------


def test_propose_records_a_pending_proposal_and_writes_nothing_to_the_vault(venue_data):
    shipped(stale_acl())
    before = venues.catalog()["acl"]["detail"]
    prop = vc.propose("ACL", {"deadlines": [{"track": "Main", "paper": "2099-02-01 AoE"}]}, [SRC], "moved a week")
    assert prop["acronym"] == "ACL" and prop["summary"] == "moved a week" and prop["changes"] == 2      # deadlines + the new source
    assert {d["field"] for d in prop["diff"]} == {"deadlines", "sources"}
    assert prop["sources"] == [SRC] and prop["outdated"] is False
    assert not override_file_exists()
    assert venues.catalog()["acl"]["detail"] == before and not venues.catalog()["acl"].get("override")
    assert vc.proposal("acl")["id"] == prop["id"]
    assert vc.pending()["acl"]["details"]["_sources"] == [SRC]


def test_a_new_proposal_replaces_the_pending_one(venue_data):
    shipped(stale_acl())
    first = vc.propose("ACL", {}, [SRC])
    second = vc.propose("ACL", {"dates": "Aug 2028"}, [SRC])
    assert first["id"] != second["id"] and vc.proposal("acl")["id"] == second["id"] and len(vc.pending()) == 1


def test_a_proposal_without_changes_still_records_the_sources_it_was_checked_against(venue_data):
    shipped(stale_acl())
    prop = vc.propose("ACL", {}, ["https://2027.example.org/cfp"])
    assert prop["changes"] == 0 and prop["diff"] == []                       # that source is already listed


def test_propose_refuses_unknown_venues_and_bad_input(venue_data):
    shipped(stale_acl())
    with pytest.raises(ValueError, match="unknown venue"):
        vc.propose("NOPE", {}, [SRC])
    with pytest.raises(ValueError, match="官方网址"):
        vc.propose("ACL", {"edition": "ACL 2028"}, [])
    assert vc.pending() == {}


def test_a_venue_without_details_needs_a_status_in_its_first_proposal(venue_data):
    shipped(acl())
    with pytest.raises(ValueError, match="status"):
        vc.propose("KDD", {"edition": "KDD 2028"}, [SRC])
    prop = vc.propose("KDD", {"status": "confirmed", "edition": "KDD 2028"}, [SRC])
    assert prop["changes"] >= 3


def test_a_proposal_goes_outdated_when_the_details_change_after_it(venue_data):
    shipped(stale_acl())
    vc.propose("ACL", {"dates": "Aug 2028"}, [SRC])
    venue_overrides.save("acl", {"location": "Elsewhere"}, None, name="ACL")          # e.g. synced from the other Mac
    assert vc.proposal("acl")["outdated"] is True


def test_marking_a_venue_verified_does_not_outdate_a_pending_proposal(venue_data):
    shipped(stale_acl())
    vc.propose("ACL", {"dates": "Aug 2028"}, [SRC])
    vc.mark_verified("ACL")
    assert vc.proposal("acl")["outdated"] is False


# ---------- apply: only on confirm ----------


def test_apply_writes_the_override_dates_it_today_and_merges_the_sources(venue_data):
    shipped(stale_acl())
    prop = vc.propose("ACL", {"edition": "ACL 2028", "deadlines": [{"track": "Main", "paper": "2099-02-01 AoE"}],
                              "format": {"review_model": "single-blind"}}, [SRC])
    res = vc.apply("acl", prop["id"])
    assert res == {"ok": True, "venue": "acl", "verified_at": TODAY}
    rec = yaml.safe_load(config.VENUE_OVERRIDES_FILE.read_text(encoding="utf-8"))["venues"][0]
    assert rec["id"] == "acl" and rec["details"]["verified_at"] == TODAY and rec["details"]["edition_year"] == 2028
    assert rec["details"]["sources"] == ["https://2027.example.org/cfp", SRC]
    assert "_sources" not in rec["details"]
    d = venues.catalog()["acl"]["detail"]
    assert d["edition"] == "ACL 2028" and d["format"] == {"template": "ACL style", "review_model": "single-blind", "notable_rules": ["Anonymous"]}
    assert d["homepage"] == "https://2027.example.org/" and d["verified_at"] == TODAY
    assert vc.pending() == {} and venue_fresh.check(venues.catalog()["acl"])["stale"] is False
    shipped_json = json.loads(venues.DETAILS_FILE.read_text(encoding="utf-8"))
    assert shipped_json["venues"][0]["edition"] == "ACL 2027"                 # the shipped file is never written


def test_apply_with_no_changes_only_updates_the_check_date(venue_data):
    shipped(stale_acl())
    assert venue_fresh.check(venues.catalog()["acl"])["stale"]
    vc.apply("acl", vc.propose("ACL", {}, ["https://2027.example.org/cfp"])["id"])
    assert vc.proposal("acl") is None
    assert venue_fresh.check(venues.catalog()["acl"])["stale"] is False
    assert venue_overrides.load()["acl"]["details"] == {"verified_at": TODAY, "sources": ["https://2027.example.org/cfp"]}


def test_apply_rejects_a_wrong_or_missing_proposal_and_a_changed_baseline(venue_data):
    shipped(stale_acl())
    with pytest.raises(LookupError):
        vc.apply("acl", "pnone")
    prop = vc.propose("ACL", {"dates": "Aug 2028"}, [SRC])
    with pytest.raises(LookupError):
        vc.apply("acl", "pother")
    venue_overrides.save("acl", {"location": "Elsewhere"}, None, name="ACL")
    with pytest.raises(vc.Conflict):
        vc.apply("acl", prop["id"])
    assert venue_overrides.load()["acl"]["details"] == {"location": "Elsewhere"}      # nothing of the proposal got in
    assert vc.proposal("acl") is not None


def test_applied_proposals_cannot_be_applied_twice(venue_data):
    shipped(stale_acl())
    prop = vc.propose("ACL", {"dates": "Aug 2028"}, [SRC])
    vc.apply("acl", prop["id"])
    with pytest.raises(LookupError):
        vc.apply("acl", prop["id"])


def set_ccfddl_link(venue_data, link):
    """Give ACL's next edition (the fixture's TBD one) a link in ccfddl."""
    f = venues.CCFDDL_DIR / "conference" / "AI" / "acl.yml"
    year = venue_data.now.year + 1
    text = f.read_text(encoding="utf-8")
    text = re.sub(rf"(    - year: {year}\n)(      link: .*\n)?", rf"\1      link: {link}\n", text)
    f.write_text(text, encoding="utf-8")
    venues._catalog.cache_clear()


def test_confirming_remembers_what_ccfddl_showed_so_only_later_changes_count(venue_data):
    shipped(acl(verified_at=TODAY, edition="ACL next", edition_year=venue_data.now.year + 1))
    assert not venue_fresh.check(venues.catalog()["acl"])["stale"]
    set_ccfddl_link(venue_data, "https://new-site.example.net/")           # a link the details do not know
    f = venue_fresh.check(venues.catalog()["acl"])
    assert [r["code"] for r in f["reasons"]] == ["link"]
    vc.mark_verified("ACL")                                                # looked at: fine
    assert venue_fresh.check(venues.catalog()["acl"])["stale"] is False
    set_ccfddl_link(venue_data, "https://newer-site.example.net/")         # ccfddl changed it again
    f = venue_fresh.check(venues.catalog()["acl"])
    assert [r["code"] for r in f["reasons"]] == ["link"] and "newer-site.example.net" in f["reasons"][0]["text"]


def test_confirming_a_check_silences_a_newer_edition_that_has_no_call_yet(venue_data):
    shipped(acl(verified_at=TODAY, edition="ACL previous", edition_year=venue_data.past.year - 1))
    f = venue_fresh.check(venues.catalog()["acl"])
    assert [r["code"] for r in f["reasons"]] == ["edition"]
    vc.apply("acl", vc.propose("ACL", {}, [SRC])["id"])
    assert venue_fresh.check(venues.catalog()["acl"])["stale"] is False


# ---------- dismiss, verified, reset ----------


def test_dismiss_drops_the_proposal_and_changes_nothing(venue_data):
    shipped(stale_acl())
    prop = vc.propose("ACL", {"dates": "Aug 2028"}, [SRC])
    with pytest.raises(LookupError):
        vc.dismiss("acl", "pwrong")
    assert vc.dismiss("acl", prop["id"]) == {"ok": True, "venue": "acl"}
    assert vc.pending() == {} and not override_file_exists()


def test_mark_verified_records_today_without_touching_the_content(venue_data):
    shipped(stale_acl())
    assert vc.mark_verified("ACL") == {"ok": True, "venue": "acl", "verified_at": TODAY}
    assert venue_overrides.load()["acl"]["details"] == {"verified_at": TODAY}
    assert venues.catalog()["acl"]["detail"]["edition"] == "ACL 2027"


def test_mark_verified_needs_details_to_verify(venue_data):
    shipped(acl())
    with pytest.raises(ValueError, match="还没有详情"):
        vc.mark_verified("KDD")
    with pytest.raises(ValueError, match="unknown venue"):
        vc.mark_verified("NOPE")


def test_reset_goes_back_to_the_shipped_details(venue_data):
    shipped(stale_acl())
    vc.mark_verified("ACL")
    assert vc.reset("ACL") == {"ok": True, "venue": "acl"}
    assert venues.catalog()["acl"]["detail"]["verified_at"] == "2025-01-01"
    assert vc.reset("ACL")["ok"] is False


# ---------- what the UI reads ----------


def test_stale_watched_lists_only_watched_venues_that_are_stale_or_have_a_proposal(venue_data):
    fmt = "%Y-%m-%d %H:%M UTC"
    kdd = acl(key="KDD", edition=f"KDD {venue_data.kdd_paper.year + 1}", edition_year=venue_data.kdd_paper.year + 1,
              homepage="https://kdd.example/next", cfp_url="https://kdd.example/next/cfp", sources=["https://kdd.example/next"],
              deadlines=[{"track": "Research", "abstract": venue_data.kdd_abstract.strftime(fmt), "paper": venue_data.kdd_paper.strftime(fmt)}],
              verified_at=TODAY)
    shipped(stale_acl(), kdd)                                                 # KDD agrees with ccfddl and was checked today
    plan.watched_keys()                                                       # first use watches the profile's venues
    plan.watch("acl", False)
    plan.watch("kdd", False)
    assert vc.stale_watched() == []                                           # nothing watched now
    plan.watch("acl")
    plan.watch("kdd")
    [row] = vc.stale_watched()
    assert (row["key"], row["acronym"], row["pending"]) == ("acl", "ACL", False) and row["reasons"][0]["code"] == "age"
    assert row["verified_at"] == "2025-01-01" and row["edition_year"] == 2027
    vc.propose("KDD", {"dates": "Aug 2028"}, [SRC])
    assert [(r["key"], r["pending"]) for r in vc.stale_watched()] == [("kdd", True), ("acl", False)]    # waiting proposals first


def test_stale_watched_skips_unwatched_venues(venue_data):
    shipped(stale_acl())
    plan.watched_keys()
    plan.watch("acl", False)
    plan.watch("kdd")
    assert vc.stale_watched() == []


def test_status_has_the_prompt_the_proposal_and_where_the_confirmed_data_goes(venue_data):
    shipped(stale_acl())
    vc.propose("ACL", {"dates": "Aug 2028"}, [SRC])
    s = vc.status("acl")
    assert s["freshness"]["stale"] and s["proposal"]["diff"][0]["field"] == "dates" and s["override"] is None
    assert s["store"] == str(config.VENUE_OVERRIDES_FILE.relative_to(config.VAULT_DIR))
    vc.mark_verified("ACL")
    assert vc.status("acl")["override"]["updated_at"]


def test_check_prompt_names_the_sources_the_reasons_and_the_propose_only_tool(venue_data):
    shipped(stale_acl())
    p = vc.check_prompt("acl")
    assert "ACL" in p and "https://2027.example.org/cfp" in p and "venue_details_propose" in p and "venue_search" in p
    assert "2025-01-01" in p and "个月" in p                                  # why it was triggered
    assert "不会改任何数据" in p


def test_check_prompt_for_a_venue_without_details(venue_data):
    shipped(acl())
    assert "还没有这个会议的详情记录" in vc.check_prompt("kdd")


# ---------- HTTP ----------


@pytest.fixture
def client(venue_data):
    app = FastAPI()
    app.include_router(venue_api.router)
    return TestClient(app)


def test_api_stale_and_status(client):
    shipped(stale_acl())
    plan.watched_keys()
    plan.watch("kdd", False)
    r = client.get("/api/venue-check/stale").json()
    assert [v["key"] for v in r["venues"]] == ["acl"] and r["months"] == config.VENUE_STALE_MONTHS
    s = client.get("/api/venue-check/acl").json()
    assert s["freshness"]["stale"] is True and s["proposal"] is None and "venue_details_propose" in s["prompt"]
    assert client.get("/api/venue-check/nope").status_code == 400


def test_api_apply_needs_the_matching_proposal_id(client):
    shipped(stale_acl())
    prop = vc.propose("ACL", {"dates": "Aug 2028"}, [SRC])
    assert client.post("/api/venue-check/acl/apply", json={}).status_code == 404
    assert client.post("/api/venue-check/acl/apply", json={"proposal_id": "pnope"}).status_code == 404
    assert not override_file_exists()
    ok = client.post("/api/venue-check/acl/apply", json={"proposal_id": prop["id"]})
    assert ok.status_code == 200 and ok.json()["verified_at"] == TODAY and override_file_exists()
    assert client.get("/api/venue-check/acl").json()["proposal"] is None


def test_api_apply_conflicts_when_the_details_moved_on(client):
    shipped(stale_acl())
    prop = vc.propose("ACL", {"dates": "Aug 2028"}, [SRC])
    venue_overrides.save("acl", {"location": "Elsewhere"}, None, name="ACL")
    r = client.post("/api/venue-check/acl/apply", json={"proposal_id": prop["id"]})
    assert r.status_code == 409 and "重新核对" in r.json()["detail"]


def test_api_dismiss_verified_and_reset(client):
    shipped(stale_acl())
    prop = vc.propose("ACL", {"dates": "Aug 2028"}, [SRC])
    assert client.post("/api/venue-check/acl/dismiss", json={"proposal_id": prop["id"]}).json() == {"ok": True, "venue": "acl"}
    assert client.post("/api/venue-check/acl/dismiss", json={"proposal_id": prop["id"]}).status_code == 404
    assert client.post("/api/venue-check/acl/verified").json()["verified_at"] == TODAY
    assert client.get("/api/venue-check/acl").json()["override"] is not None
    assert client.post("/api/venue-check/acl/reset").json()["ok"] is True
    assert client.get("/api/venue-check/acl").json()["override"] is None


def test_the_api_has_no_route_that_proposes(client):
    # proposals come only from the assistant's tool
    assert client.post("/api/venue-check/acl/propose", json={}).status_code in (404, 405)
    assert {r.path for r in venue_api.router.routes} == {"/api/venue-check/stale", "/api/venue-check/{key}", "/api/venue-check/{key}/apply",
                                                         "/api/venue-check/{key}/dismiss", "/api/venue-check/{key}/verified",
                                                         "/api/venue-check/{key}/reset"}


# ---------- the assistant's tool ----------


@pytest.fixture
def tools(monkeypatch):
    events = []

    async def emit(e):
        events.append(e)

    monkeypatch.setattr(studio_tools, "create_sdk_mcp_server", lambda name, tools: tools)
    built, names = studio_tools.build(emit)
    return {t.name: t for t in built}, names, events


def call(tool, args):
    return asyncio.run(tool.handler(args))


def test_the_tool_is_offered_to_the_assistant(tools):
    by_name, names, _ = tools
    assert "mcp__studio__venue_details_propose" in names and "venue_details_propose" in by_name
    assert "只提交提案" in by_name["venue_details_propose"].description


def test_the_tool_only_proposes(tools, venue_data):
    by_name, _, events = tools
    shipped(stale_acl())
    res = call(by_name["venue_details_propose"], {"venue": "ACL", "details": {"dates": "Aug 2028"}, "sources": [SRC], "summary": "dates"})
    out = json.loads(res["content"][0]["text"])
    assert not res.get("is_error") and out["proposal"].startswith("p") and out["changes"][0]["field"] == "dates"
    assert "不能应用" in out["note"]
    assert not override_file_exists() and venues.catalog()["acl"]["detail"]["verified_at"] == "2025-01-01"
    assert [(e["type"], e["area"]) for e in events] == [("studio_changed", "venues")]
    assert "待你确认" in events[0]["summary"] and events[0]["record"]["acronym"] == "ACL"
    assert vc.proposal("acl")["id"] == out["proposal"]


def test_the_tool_reports_bad_input_as_an_error(tools, venue_data):
    by_name, _, events = tools
    shipped(stale_acl())
    for args in ({"venue": "NOPE", "sources": [SRC]}, {"venue": "ACL", "sources": []}, {"venue": "ACL", "details": {"verified_at": "2030-01-01"}, "sources": [SRC]}):
        res = call(by_name["venue_details_propose"], args)
        assert res["is_error"] is True and res["content"][0]["text"].startswith("错误：")
    assert events == [] and vc.pending() == {}


def test_no_studio_tool_applies_a_proposal(tools):
    by_name, _, _ = tools
    assert not [n for n in by_name if "apply" in n or "verified" in n or "override" in n]


def test_venue_search_tells_the_assistant_whether_details_may_be_stale(tools, venue_data):
    by_name, _, _ = tools
    shipped(stale_acl())
    out = json.loads(call(by_name["venue_search"], {"query": "ACL"})["content"][0]["text"])
    assert out["freshness"]["stale"] is True and out["freshness"]["reasons"][0]["code"] == "age"


# ---------- addresses must be openable ----------


@pytest.mark.parametrize("bad", ["https://<cfp>", "https://", "http://exa mple.com", "javascript:alert(1)"])
def test_unopenable_addresses_are_refused_in_sources_and_links(bad):
    with pytest.raises(ValueError, match="http"):
        vc.clean_patch({}, [bad])
    with pytest.raises(ValueError, match="http"):
        vc.clean_patch({"cfp_url": bad}, [SRC])
    with pytest.raises(ValueError, match="http"):
        vc.clean_patch({"sources": [bad]}, [SRC])


# ---------- removing a field ----------


def test_a_proposal_can_remove_fields_the_new_edition_no_longer_has(venue_data):
    shipped(stale_acl(location="Vienna", dates="Aug 2027"))
    prop = vc.propose("ACL", {"edition": "ACL 2028"}, [SRC], "next year", remove=["location", "cfp_url", "format.template"])
    fields = {d["field"]: d["lines"] for d in prop["diff"]}
    assert fields["location"] == [{"op": "-", "text": "Vienna"}]
    assert fields["cfp_url"] == [{"op": "-", "text": "https://2027.example.org/cfp"}]
    assert fields["format.template"] == [{"op": "-", "text": "ACL style"}]
    assert "dates" not in fields                                           # not asked to go
    assert not override_file_exists()
    vc.apply("acl", prop["id"])
    d = venues.catalog()["acl"]["detail"]
    assert "location" not in d and "cfp_url" not in d and "template" not in d["format"]
    assert d["dates"] == "Aug 2027" and d["format"]["review_model"] == "double-anonymous" and d["edition"] == "ACL 2028"
    text = config.VENUE_OVERRIDES_FILE.read_text(encoding="utf-8")
    assert "location: null" in text and "cfp_url: null" in text and "template: null" in text
    assert json.loads(venues.DETAILS_FILE.read_text(encoding="utf-8"))["venues"][0]["cfp_url"] == "https://2027.example.org/cfp"


def test_a_removal_survives_the_next_check_and_goes_away_with_the_override(venue_data):
    shipped(stale_acl(location="Vienna"))
    vc.apply("acl", vc.propose("ACL", {}, [SRC], remove=["location"])["id"])
    vc.mark_verified("ACL")
    assert "location" not in venues.catalog()["acl"]["detail"]
    vc.reset("ACL")
    assert venues.catalog()["acl"]["detail"]["location"] == "Vienna"


def test_only_removable_fields_can_go_and_not_those_the_proposal_changes(venue_data):
    shipped(stale_acl())
    for bad in (["status"], ["edition"], ["sources"], ["bogus"], ["format"]):
        with pytest.raises(ValueError, match="不能删除"):
            vc.propose("ACL", {}, [SRC], remove=bad)
    with pytest.raises(ValueError, match="既修改又删除"):
        vc.propose("ACL", {"cfp_url": "https://x.example/cfp"}, [SRC], remove=["cfp_url"])
    with pytest.raises(ValueError, match="既修改又删除"):
        vc.propose("ACL", {"format": {"template": "T"}}, [SRC], remove=["format.template"])
    vc.propose("ACL", {"format": {"template": "T"}}, [SRC], remove=["format.review_model"])         # a different sub-field is fine
    assert vc.pending()["acl"]["details"]["_remove"] == ["format.review_model"]


def test_the_studio_tool_passes_remove_on(tools, venue_data):
    by_name, _, _ = tools
    shipped(stale_acl(location="Vienna"))
    res = call(by_name["venue_details_propose"], {"venue": "ACL", "sources": [SRC], "remove": ["location"]})
    out = json.loads(res["content"][0]["text"])
    assert not res.get("is_error") and out["changes"][0]["field"] == "location"
    assert "remove" in by_name["venue_details_propose"].description


# ---------- a proposal for a venue that has no details yet ----------


def test_a_first_entry_for_a_venue_without_details_can_be_proposed_seen_and_applied(client):
    shipped(acl(verified_at=TODAY))                           # KDD has no details
    plan.watched_keys()
    plan.watch("kdd")
    prop = vc.propose("KDD", {"status": "confirmed", "edition": "KDD 2028", "homepage": "https://kdd2028.example.org/"}, [SRC], "first entry")
    s = client.get("/api/venue-check/kdd").json()
    assert s["freshness"] is None and s["proposal"]["id"] == prop["id"]          # what the sheet needs to show the card
    assert {d["field"] for d in s["proposal"]["diff"]} >= {"status", "edition", "homepage", "sources"}
    assert [(v["key"], v["pending"], v["reasons"]) for v in client.get("/api/venue-check/stale").json()["venues"]] == [("kdd", True, [])]
    assert client.post("/api/venue-check/kdd/apply", json={"proposal_id": prop["id"]}).status_code == 200
    d = venues.catalog()["kdd"]["detail"]
    assert d["edition"] == "KDD 2028" and d["verified_at"] == TODAY and d["key"] == "KDD"
    assert client.get("/api/venue-check/kdd").json()["proposal"] is None


# ---------- an override that the shipped data has moved past ----------


def test_status_says_when_the_override_is_older_than_the_shipped_details(venue_data):
    shipped(acl(verified_at="2026-10-24"))
    vc.mark_verified("ACL")
    assert vc.status("acl")["override"]["outdated"] is False
    shipped(acl(edition="ACL 2028", edition_year=2028, verified_at="2026-10-20"))
    s = vc.status("acl")
    assert s["override"]["outdated"] is True and s["override"]["updated_at"]
    assert venues.catalog()["acl"]["detail"]["edition"] == "ACL 2028"
    assert vc.reset("ACL")["ok"] is True and vc.status("acl")["override"] is None


def test_confirming_over_an_outdated_override_keeps_none_of_its_old_fields(venue_data):
    shipped(acl(verified_at="2026-10-24"))
    vc.apply("acl", vc.propose("ACL", {"location": "Old place", "dates": "Old dates"}, [SRC])["id"])
    shipped(acl(edition="ACL 2028", edition_year=2028, verified_at="2026-10-20"))
    prop = vc.propose("ACL", {"dates": "New dates"}, [SRC])
    assert {d["field"] for d in prop["diff"]} == {"dates", "sources"}            # the diff is against the shipped 2028 details
    vc.apply("acl", prop["id"])
    d = venues.catalog()["acl"]["detail"]
    assert d["dates"] == "New dates" and "location" not in d and d["edition"] == "ACL 2028"
