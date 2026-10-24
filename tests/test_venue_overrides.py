"""app/venue_overrides.py and how venues.py merges the vault overrides over data/venue_details.json."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

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
    assert r["ccfddl_seen"] == {"year": 2029}


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
    assert ed["timezone"] == "官网" and ed["timeline"][0]["deadline"].startswith("2099-03-02")


def test_the_edition_year_of_overridden_details_names_the_official_edition(venue_data):
    shipped(acl(edition="ACL 2027", edition_year=2027))
    vo.save("acl", {"edition": "ACL (next)", "edition_year": 2030}, None, name="ACL")
    assert venues.catalog()["acl"]["editions"][-1]["year"] == 2030


def test_ccfddl_editions_exclude_the_one_built_from_the_cfp(venue_data):
    shipped(acl())
    acl_entry = venues.catalog()["acl"]
    assert len(acl_entry["editions"]) == len(acl_entry["ccfddl_editions"]) + 1
    assert all(e["timezone"] != "官网" for e in acl_entry["ccfddl_editions"])


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
