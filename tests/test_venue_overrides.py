"""app/venue_overrides.py and how venues.py merges the vault overrides over data/venue_details.json."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

from app import config, venue_overrides as vo, venues

from .venue_helpers import TODAY, acl, own_details_file, shipped  # noqa: F401  (autouse fixture)

# ---------- merge_detail ----------


def test_merge_replaces_scalars_and_lists_and_merges_dicts_key_by_key():
    base = acl()
    out = vo.merge_detail(base, {"edition": "ACL 2028", "tracks": [{"name": "Short", "page_limit": "4 pages"}],
                                 "format": {"review_model": "single-blind"}})
    assert out["edition"] == "ACL 2028"
    assert out["tracks"] == [{"name": "Short", "page_limit": "4 pages"}]          # lists are replaced whole
    assert out["format"] == {"template": "ACL style", "review_model": "single-blind", "notable_rules": ["Anonymous"]}
    assert out["homepage"] == base["homepage"]


def test_merge_does_not_touch_its_inputs():
    base, patch = acl(), {"format": {"notable_rules": ["x"]}, "tracks": [{"name": "T"}]}
    before = json.dumps([base, patch], sort_keys=True)
    out = vo.merge_detail(base, patch)
    out["format"]["template"] = "changed"
    out["tracks"][0]["name"] = "changed"
    assert json.dumps([base, patch], sort_keys=True) == before


# ---------- the store ----------


def test_the_override_file_sits_next_to_plan_yaml_by_default():
    assert config.VENUE_OVERRIDES_FILE == config.PLAN_FILE.parent / "venue_details.yaml"
    assert vo.store.path == config.VENUE_OVERRIDES_FILE and config.VENUE_OVERRIDES_FILE.is_relative_to(config.VAULT_DIR)


def test_save_writes_a_sync_safe_record_per_venue_in_the_vault():
    rec = vo.save("acl", {"edition": "ACL 2028"}, {"year": 2028, "link": "", "dates": [], "upcoming": 2028}, name="ACL")
    data = yaml.safe_load(config.VENUE_OVERRIDES_FILE.read_text(encoding="utf-8"))
    assert config.VENUE_OVERRIDES_FILE.read_text(encoding="utf-8").startswith("# Venue details confirmed in PhD Studio")
    [r] = data["venues"]
    assert (r["id"], r["key"], r["details"], r["ccfddl_seen"]["upcoming"]) == ("acl", "ACL", {"edition": "ACL 2028"}, 2028)
    assert r["created_at"] and r["updated_at"] and rec["id"] == "acl"
    assert list(vo.load()) == ["acl"]


def test_a_second_save_adds_to_the_first_and_replaces_what_ccfddl_showed():
    vo.save("acl", {"edition": "ACL 2028", "format": {"template": "new"}}, {"year": 2028}, name="ACL")
    vo.save("acl", {"verified_at": TODAY, "format": {"review_model": "open"}}, {"year": 2029}, name="ACL")
    [r] = vo.load().values()
    assert r["details"] == {"edition": "ACL 2028", "verified_at": TODAY, "format": {"template": "new", "review_model": "open"}}
    assert r["ccfddl_seen"]["year"] == 2029


def test_reset_hides_the_override_and_a_later_save_starts_clean():
    vo.save("acl", {"edition": "ACL 2028"}, None, name="ACL")
    assert vo.reset("acl") is True and vo.load() == {}
    assert vo.reset("acl") is False and vo.reset("nope") is False
    vo.save("acl", {"homepage": "https://x.example/"}, None, name="ACL")
    assert vo.load()["acl"]["details"] == {"homepage": "https://x.example/"}          # not merged with the dropped one
    assert "deleted" not in vo.load()["acl"]


def test_the_stamp_changes_with_every_write_and_with_waiting_conflict_copies():
    assert vo.stamp() is None
    vo.save("acl", {"edition": "A"}, None)
    first = vo.stamp()
    assert first is not None
    vo.save("acl", {"edition": "B"}, None)
    assert vo.stamp() != first
    copy = config.VENUE_OVERRIDES_FILE.with_name("venue_details.sync-conflict-20260101-000000-ABCDEFG.yaml")
    copy.write_text("venues: []\n", encoding="utf-8")
    assert vo.stamp()[1] == 1


def test_a_syncthing_conflict_copy_is_merged_with_the_newer_record_winning():
    vo.save("acl", {"edition": "ACL 2028"}, None, name="ACL")
    older = vo.load()["acl"]["updated_at"]
    newer = (datetime.fromisoformat(older) + timedelta(hours=1)).isoformat(timespec="seconds")
    copy = config.VENUE_OVERRIDES_FILE.with_name("venue_details.sync-conflict-20260101-000000-ABCDEFG.yaml")
    copy.write_text(yaml.safe_dump({"venues": [
        {"id": "acl", "key": "ACL", "details": {"edition": "ACL 2029"}, "updated_at": newer},
        {"id": "kdd", "key": "KDD", "details": {"edition": "KDD 2028"}, "updated_at": newer}]}), encoding="utf-8")
    got = vo.load()
    assert got["acl"]["details"] == {"edition": "ACL 2029"} and got["kdd"]["details"] == {"edition": "KDD 2028"}
    assert not copy.exists()


# ---------- merged into the catalog ----------


def test_overrides_are_laid_over_the_shipped_details(venue_data):
    shipped(acl())
    vo.save("acl", {"edition": "ACL 2028", "edition_year": 2028, "format": {"review_model": "single-blind"}}, None, name="ACL")
    e = venues.catalog()["acl"]
    assert e["detail"]["edition"] == "ACL 2028" and e["detail"]["format"] == {
        "template": "ACL style", "review_model": "single-blind", "notable_rules": ["Anonymous"]}
    assert e["detail"]["homepage"] == "https://2027.example.org/"     # untouched fields come from the shipped file
    assert e["override"]["id"] == "acl"
    assert venues.get("acl")["overridden"] is True and venues.get("kdd")["overridden"] is False


def test_the_catalog_picks_up_a_new_override_without_a_manual_cache_clear(venue_data):
    shipped(acl())
    assert venues.catalog()["acl"]["detail"]["edition"] == "ACL 2027"
    vo.save("acl", {"edition": "ACL 2028"}, None, name="ACL")
    assert venues.catalog()["acl"]["detail"]["edition"] == "ACL 2028"
    vo.reset("acl")
    assert venues.catalog()["acl"]["detail"]["edition"] == "ACL 2027"


def test_an_override_can_add_details_for_a_venue_the_file_does_not_cover(venue_data):
    shipped(acl())
    vo.save("kdd", {"status": "confirmed", "edition": "KDD 2028", "edition_year": 2028}, None, name="KDD")
    d = venues.catalog()["kdd"]["detail"]
    assert d["key"] == "KDD" and d["edition"] == "KDD 2028" and venues.get("kdd")["has_detail"] is True


def test_overridden_deadlines_feed_the_timeline_when_ccfddl_has_nothing_upcoming(venue_data):
    shipped(acl(deadlines=[{"track": "Main", "abstract": None, "paper": "2099-01-05 AoE"}]))
    vo.save("acl", {"deadlines": [{"track": "Main", "abstract": None, "paper": "2099-03-01 AoE"}]}, None, name="ACL")
    ed = venues.catalog()["acl"]["editions"][-1]
    assert ed["source"] == "official" and ed["timeline"][0]["deadline"].startswith("2099-03-02")


def test_the_edition_year_of_overridden_details_names_the_official_edition(venue_data):
    shipped(acl(edition="ACL 2027", edition_year=2027))
    vo.save("acl", {"edition": "ACL (next)", "edition_year": 2030}, None, name="ACL")
    assert venues.catalog()["acl"]["editions"][-1]["year"] == 2030


def test_ccfddl_editions_exclude_the_one_built_from_the_cfp(venue_data):
    shipped(acl())
    acl_entry = venues.catalog()["acl"]
    assert len(acl_entry["editions"]) == len(acl_entry["ccfddl_editions"]) + 1
    assert all(e.get("source") != "official" for e in acl_entry["ccfddl_editions"])


def test_summary_carries_freshness_only_for_venues_with_details(venue_data):
    shipped(acl(verified_at=TODAY))
    cat = venues.catalog()
    assert venues.summary(cat["acl"])["freshness"]["stale"] is False
    assert venues.summary(cat["kdd"])["freshness"] is None
    assert venues.get("acl")["freshness"]["edition_year"] == 2027


def test_the_file_level_verified_at_is_the_fallback_for_entries_without_one(venue_data):
    shipped(acl())                                           # the helper's file says 2026-01-01
    assert venues.catalog()["acl"]["detail"]["verified_at"] == "2026-01-01"
    shipped(acl(verified_at="2026-09-09"))
    assert venues.catalog()["acl"]["detail"]["verified_at"] == "2026-09-09"


def test_real_details_file_records_the_edition_year_and_the_check_date():
    data = json.loads((venues.STUDIO_DIR / "data" / "venue_details.json").read_text(encoding="utf-8"))
    for v in data["venues"]:
        assert isinstance(v.get("edition_year"), int) and 2020 <= v["edition_year"] <= 2035, v["key"]
        assert date.fromisoformat(v["verified_at"]) <= datetime.now(timezone.utc).date(), v["key"]
        if v["edition"].split()[-1].isdigit():
            assert str(v["edition_year"]) == v["edition"].split()[-1], v["key"]


# ---------- removing fields ----------


def test_a_none_in_a_patch_removes_the_field_unless_the_patch_is_an_override_in_the_making():
    base = acl(location="Vienna")
    gone = vo.merge_detail(base, {"location": None, "format": {"template": None}})
    assert "location" not in gone and gone["format"] == {"review_model": "double-anonymous", "notable_rules": ["Anonymous"]}
    kept = vo.merge_detail({"cfp_url": "https://x.example/"}, {"cfp_url": None, "format": {"template": None}}, keep_none=True)
    assert kept == {"cfp_url": None, "format": {"template": None}}
    assert vo.merge_detail({}, {"format": {"template": None, "review_model": "open"}}) == {"format": {"review_model": "open"}}


def test_tombstones_name_only_removable_fields():
    assert vo.tombstones(["cfp_url", "format.template", "format.review_model"]) == {
        "cfp_url": None, "format": {"template": None, "review_model": None}}
    for bad in ("status", "edition", "edition_year", "sources", "format", "nope"):
        with pytest.raises(ValueError, match="不能删除"):
            vo.tombstones([bad])
    assert vo.tombstones(None) == {}


def test_saved_removals_survive_later_saves_and_hide_the_shipped_field(venue_data):
    shipped(acl(location="Vienna"))
    vo.save("acl", {"location": None}, None, name="ACL")
    vo.save("acl", {"verified_at": TODAY}, None, name="ACL")
    assert vo.load()["acl"]["details"] == {"location": None, "verified_at": TODAY}
    assert "location" not in venues.catalog()["acl"]["detail"]
    assert "location: null" in config.VENUE_OVERRIDES_FILE.read_text(encoding="utf-8")


# ---------- validating what is read from the vault ----------


@pytest.mark.parametrize("u", ["https://sigir2027.org/pages/submit-full.html?a=1#b", "http://localhost:8786/x", "https://www2027.thewebconf.org/?p=296"])
def test_url_accepts_real_http_addresses(u):
    assert vo.url(u, "x") == u


@pytest.mark.parametrize("u", ["https://<cfp>", "https://", "http://exa mple.com", "https://example.com:99999", "javascript:alert(1)",
                               "ftp://example.com/x", "example.com", "https://exa<mple>.com/", "https://example.com/\"x", "", None])
def test_url_refuses_what_the_browser_cannot_open(u):
    with pytest.raises(ValueError, match="http"):
        vo.url(u, "x")


def write_overrides(*records):
    config.VENUE_OVERRIDES_FILE.write_text(yaml.safe_dump({"venues": list(records)}), encoding="utf-8")


def test_a_record_with_unusable_details_is_skipped_with_a_warning_and_the_catalog_still_builds(venue_data, caplog):
    shipped(acl())
    write_overrides({"id": "acl", "key": "ACL", "details": "oops, a string", "updated_at": "2026-10-25T00:00:00"},
                    {"id": "kdd", "key": "KDD", "details": {"edition": "KDD 2028", "edition_year": 2028}, "updated_at": "2026-10-25T00:00:00"})
    with caplog.at_level("WARNING"):
        assert list(vo.load()) == ["kdd"]
    assert "acl" in caplog.text and "skipped" in caplog.text
    cat = venues.catalog()
    assert cat["acl"]["detail"]["edition"] == "ACL 2027" and cat["acl"]["override"] is None      # the shipped details stand
    assert cat["kdd"]["detail"]["edition"] == "KDD 2028"
    assert venues.summary(cat["acl"])["freshness"] is not None


@pytest.mark.parametrize("details", [{"tracks": "abc"}, {"deadlines": [1, 2]}, {"edition_year": "soon"}, {"status": "planned"},
                                     {"homepage": "https://<x>"}, {"format": ["a"]}, {"sources": ["not a url"]}, {"verified_at": "last week"}, ["list"], 5])
def test_every_field_is_validated_on_read(venue_data, details):
    shipped(acl())
    write_overrides({"id": "acl", "key": "ACL", "details": details})
    assert vo.load() == {}
    assert venues.catalog()["acl"]["detail"]["edition"] == "ACL 2027"


def test_unknown_fields_in_a_record_are_ignored_not_fatal(venue_data, caplog):
    shipped(acl())
    write_overrides({"id": "acl", "key": "ACL", "details": {"location": "Vienna", "mystery": 1}})
    with caplog.at_level("WARNING"):
        assert vo.load()["acl"]["details"] == {"location": "Vienna"}
    assert "mystery" in caplog.text


def test_an_unreadable_store_file_gives_no_overrides_instead_of_an_error(venue_data):
    shipped(acl())
    for text in ("- just\n- a list\n", "venues: not a list\n", "venues:\n- 5\n- [a]\n", "venues: [unclosed\n", ""):
        config.VENUE_OVERRIDES_FILE.write_text(text, encoding="utf-8")
        assert vo.load() == {}
        venues._catalog.cache_clear()
        assert venues.catalog()["acl"]["detail"]["edition"] == "ACL 2027"


def test_the_remembered_ccfddl_state_is_sanitised_whatever_a_hand_edit_left(venue_data):
    shipped(acl())
    write_overrides({"id": "acl", "key": "ACL", "details": {"location": "Vienna"},
                     "ccfddl_seen": {"year": "x", "upcoming": None, "link": None, "dates": ["2027-01-01T00:00:00+00:00", "junk", 5],
                                     "newer": {"2028": {"link": "a.org", "dates": "no"}, "2029": "bad"}}})
    seen = vo.load()["acl"]["ccfddl_seen"]
    assert seen == {"year": None, "upcoming": None, "link": "", "dates": ["2027-01-01T00:00:00+00:00"],
                    "newer": {"2028": {"link": "a.org", "dates": []}, "2029": {"link": "", "dates": []}}}
    write_overrides({"id": "acl", "key": "ACL", "details": {}, "ccfddl_seen": "garbage", "base": "garbage"})
    assert vo.load()["acl"]["ccfddl_seen"] is None and vo.load()["acl"]["base"] is None


def test_dates_the_yaml_parser_turns_into_date_objects_are_normalised(venue_data):
    shipped(acl())
    config.VENUE_OVERRIDES_FILE.write_text("venues:\n- id: acl\n  details:\n    verified_at: 2026-10-25\n", encoding="utf-8")
    assert vo.load()["acl"]["details"]["verified_at"] == "2026-10-25"
    assert venues.catalog()["acl"]["detail"]["verified_at"] == "2026-10-25"


def test_confirming_a_venue_ccfddl_has_no_edition_for_leaves_a_state_that_reads_back(venue_data):
    # PAKDD is CORE-only in the fixture: no ccfddl editions, so the remembered "upcoming" is null in the yaml
    from app import venue_check
    shipped(acl(key="PAKDD", edition="PAKDD 2026", edition_year=2026, verified_at="2025-01-01"))
    venue_check.mark_verified("PAKDD")
    text = config.VENUE_OVERRIDES_FILE.read_text(encoding="utf-8")
    assert "upcoming: null" in text
    venues._catalog.cache_clear()
    cat = venues.catalog()
    assert venues.summary(cat["pakdd"])["freshness"]["stale"] is False
    assert [r["acronym"] for r in venues.search("pakdd")] == ["PAKDD"]
    assert venues.get("pakdd")["freshness"]["verified_at"] == TODAY


def test_a_freshness_failure_never_breaks_the_summary(venue_data, monkeypatch, caplog):
    shipped(acl())
    monkeypatch.setattr(venues.venue_fresh, "check", lambda e: 1 / 0)
    with caplog.at_level("WARNING"):
        assert venues.summary(venues.catalog()["acl"])["freshness"] is None
    assert "freshness check failed" in caplog.text


# ---------- an override must not shadow newer shipped data ----------


def test_base_of_records_the_shipped_edition_and_check_date():
    assert vo.base_of(acl(verified_at="2026-10-24")) == {"edition_year": 2027, "verified_at": "2026-10-24"}
    assert vo.base_of(None) is None and vo.base_of({}) is None


def rec(details, base="default"):
    return {"id": "acl", "details": details, "base": {"edition_year": 2027, "verified_at": "2026-10-24"} if base == "default" else base}


@pytest.mark.parametrize("bundled,details,is_outdated", [
    (acl(verified_at="2026-10-24"), {"verified_at": "2026-10-25"}, False),                                   # unchanged since
    (acl(verified_at="2026-10-24"), {"location": "X", "verified_at": "2026-10-25"}, False),
    (acl(verified_at="2026-10-25"), {"verified_at": "2026-10-25"}, False),                                   # same day: the override stands
    (acl(verified_at="2026-11-02"), {"verified_at": "2026-10-25"}, True),                                    # re-verified later than the user
    (acl(edition="ACL 2028", edition_year=2028, verified_at="2026-10-01"), {"verified_at": "2026-10-25"}, True),   # a newer edition
    (acl(edition="ACL 2028", edition_year=2028, verified_at="2026-10-01"), {"edition_year": 2028, "verified_at": "2026-10-25"}, False),
    (acl(edition="ACL 2026", edition_year=2026, verified_at="2026-12-01"), {"edition_year": 2027, "verified_at": "2026-10-25"}, False),  # user is ahead
])
def test_an_override_is_outdated_once_the_shipped_details_moved_past_it(bundled, details, is_outdated):
    assert vo.outdated(bundled, rec(details)) is is_outdated


def test_nothing_is_outdated_without_a_base_or_shipped_details():
    assert vo.outdated(acl(verified_at="2030-01-01"), rec({"verified_at": "2026-10-25"}, base=None)) is False
    assert vo.outdated(None, rec({"verified_at": "2026-10-25"})) is False


def test_shipped_details_that_moved_on_win_over_a_stale_override(venue_data):
    shipped(acl(verified_at="2026-10-24"))
    vo.save("acl", {"format": {"review_model": "single-blind"}, "verified_at": "2026-10-25", "sources": ["https://old.example/"]}, None,
            name="ACL", base=vo.base_of(acl(verified_at="2026-10-24")))
    assert venues.catalog()["acl"]["detail"]["format"]["review_model"] == "single-blind"
    # the next release ships ACL 2028 with other tracks, rules and sources
    shipped(acl(edition="ACL 2028", edition_year=2028, verified_at="2027-01-10", tracks=[{"name": "Main", "page_limit": "9 pages"}],
                format={"template": "new", "review_model": "double-anonymous", "notable_rules": []}, sources=["https://2028.example.org/"]))
    e = venues.catalog()["acl"]
    assert e["detail"]["edition"] == "ACL 2028" and e["detail"]["verified_at"] == "2027-01-10"
    assert e["detail"]["format"]["review_model"] == "double-anonymous" and e["detail"]["sources"] == ["https://2028.example.org/"]
    assert e["override"] is None and e["override_outdated"]["id"] == "acl"
    assert e["bundled"]["edition_year"] == 2028


def test_a_new_confirmation_after_an_outdated_override_starts_from_the_shipped_details(venue_data):
    from app import venue_check
    shipped(acl(verified_at="2026-10-24"))
    vo.save("acl", {"location": "Old place", "verified_at": "2026-10-25"}, None, name="ACL", base=vo.base_of(acl(verified_at="2026-10-24")))
    shipped(acl(edition="ACL 2028", edition_year=2028, verified_at="2026-10-20"))
    assert venues.catalog()["acl"]["override_outdated"]
    venue_check.mark_verified("ACL")
    rec_ = vo.load()["acl"]
    assert "location" not in rec_["details"] and rec_["details"] == {"verified_at": TODAY}
    assert rec_["base"] == {"edition_year": 2028, "verified_at": "2026-10-20"}
    assert venues.catalog()["acl"]["override_outdated"] is None


def test_save_fresh_replaces_the_old_details():
    vo.save("acl", {"location": "A"}, None, name="ACL")
    vo.save("acl", {"dates": "B"}, None, name="ACL", fresh=True)
    assert vo.load()["acl"]["details"] == {"dates": "B"}
