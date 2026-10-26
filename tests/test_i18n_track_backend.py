"""Server text of the tracking, scholars and venue modules (#52), in Chinese and English.

Most cases build one message in both languages and check the pair: both exist, they differ, no key or stored
reference leaks through, Chinese only in zh. The rest check what is *kept* (vault files, caches, job state):
it is worded when it is served, so it follows the interface language.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable

import pytest
import yaml
from fastapi.testclient import TestClient

from app import config, i18n, msgref, reading, scholar_rank as R, scholar_refresh, scholars, sources, tracker_run, tracking, venue_check as vc
from app import venue_fresh, venues
from app.main import app

from .fakenet import fixture_json, net  # noqa: F401  (net is a fixture)
from .venue_helpers import acl, own_details_file, shipped  # noqa: F401  (autouse fixture)

CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
LEAK = re.compile(r"\b(?:track|scholars|venue)\.[a-z0-9_]+(?:\.[a-z0-9_]+)+\b|i18n:")  # a locale key, or a stored reference
TODAY = date(2026, 10, 25)


def in_both(make: Callable[[], Any]) -> tuple[Any, Any]:
    """`make()` in the Chinese interface, then in the English one."""
    out = []
    for lang in ("zh", "en"):
        config.save_settings({"ui_language": lang})
        out.append(make())
    config.save_settings({"ui_language": "zh"})
    return out[0], out[1]


def check_pair(zh: str, en: str) -> None:
    assert zh and en and zh != en, (zh, en)
    assert CJK.search(zh) and not CJK.search(en), (zh, en)
    assert not LEAK.search(zh) and not LEAK.search(en), (zh, en)


@pytest.fixture
def ctx(net, make_note, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(reading, "fetch_arxiv_title", lambda aid: f"Title of {aid}")
    return SimpleNamespace(client=TestClient(app), net=net, make_note=make_note, mp=monkeypatch, tmp=tmp_path)


@pytest.fixture(autouse=True)
def state_dirs():
    """The sandbox empties the data dir for each test; these folders are made when the modules load."""
    for d in (scholar_refresh.DIR, scholar_refresh.SNAPS, scholars.CACHE, tracking.FEED_CACHE):
        d.mkdir(parents=True, exist_ok=True)


def detail_of(r) -> str:
    return r.json()["detail"]


# ---------- API errors and function messages: one pair each ----------


def tracker_dir(c, **state):
    (c.tmp / "tracker.py").write_text("", encoding="utf-8")
    c.mp.setattr(tracker_run, "TRACKER_DIR", c.tmp)
    c.mp.setattr(tracker_run, "_state", {"running": False, **state})


def busy_running(c):
    tracker_dir(c, running=True)
    return detail_of(c.client.post("/api/track/tracker/run"))


def busy_lock(c):
    tracker_dir(c)
    c.mp.setattr(tracker_run, "_try_lock", lambda: None)
    return detail_of(c.client.post("/api/track/tracker/run"))


def busy_proc(c):
    tracker_dir(c)
    c.mp.setattr(tracker_run, "_other_tracker_procs", lambda: ["tracker.py fetch"])
    return detail_of(c.client.post("/api/track/tracker/run"))


def no_python(c):
    c.mp.setattr(tracker_run, "STUDIO_DIR", c.tmp)
    c.mp.setattr(tracker_run.shutil, "which", lambda name: None)
    tracker_dir(c)
    return detail_of(c.client.post("/api/track/tracker/run"))


def refresh_running(c):
    c.mp.setitem(scholar_refresh._job, "running", True)
    return detail_of(c.client.post("/api/track/scholars/refresh"))


def no_pdf(c):
    c.make_note("Paper A")
    return detail_of(c.client.get("/api/thumb/Paper A"))


def capture_not_found(c):
    c.net.add("api2.openreview.net/notes", {"notes": []})
    return tracking.capture("https://openreview.net/forum?id=mmIAp3cVS0", "")["error"]


def capture_human_check(c):
    c.net.add("api2.openreview.net/notes", fixture_json("openreview_challenge.json"), status=403)
    return tracking.capture("https://openreview.net/forum?id=mmIAp3cVS0", "")["warning"]


def capture_all_sources_down(c):
    return tracking.capture("a title long enough to be searched", "")["error"]  # no route: every source is down


CASES: dict[str, Callable[[Any], str]] = {
    "api scholar window": lambda c: detail_of(c.client.post("/api/track/scholars/scholar-window")),
    "api openalex search": lambda c: detail_of(c.client.get("/api/track/scholars/search", params={"q": "ab"})),
    "api no candidate": lambda c: detail_of(c.client.post("/api/track/capture/pick", json={})),
    "api add scholar": lambda c: detail_of(c.client.post("/api/track/scholars", json={})),
    "api exclude without a name": lambda c: detail_of(c.client.post("/api/track/scholars/exclude", json={})),
    "api lab hint without a name": lambda c: detail_of(c.client.post("/api/track/scholars/lab-hint", json={})),
    "api snapshot without rows": lambda c: detail_of(c.client.post("/api/track/scholars/snapshot", json={"text": "nothing"})),
    "api apply a handled suggestion": lambda c: detail_of(c.client.post("/api/track/scholars/refresh/nope/apply")),
    "api refresh already running": refresh_running,
    "api tracker not set up": lambda c: detail_of(c.client.post("/api/track/tracker/run")),
    "api tracker running": busy_running,
    "api tracker lock held": busy_lock,
    "api tracker process found": busy_proc,
    "api tracker has no python": no_python,
    "api thumb of an unknown paper": lambda c: detail_of(c.client.get("/api/thumb/nope")),
    "api thumb without a pdf": no_pdf,
    "capture id not found": capture_not_found,
    "capture human check warning": capture_human_check,
    "capture every source down": capture_all_sources_down,
    "title search source down": lambda c: tracking.capture("a title long enough to be searched", "")["sources"][0]["error"],
    "acl short volume": lambda c: next(label for _, label in sources.acl_volumes(TODAY) if label.endswith(("短文", "Short"))),
}


@pytest.mark.parametrize("name", CASES)
def test_message_in_both_languages(name, ctx):
    zh, en = in_both(lambda: CASES[name](ctx))
    check_pair(zh, en)


SOURCE_ERRORS = [("track.src.connect", {"what": "arXiv", "cls": "ConnectError"}), ("track.src.rate_limited", {"what": "Crossref"}),
                 ("track.src.http", {"what": "OpenAlex", "status": 500}), ("track.src.not_json", {"what": "Crossref"}),
                 ("track.src.captcha_title", {}), ("track.src.captcha_search", {}), ("track.src.acl_not_paper", {}),
                 ("track.src.not_found", {"what": "OpenReview"})]


@pytest.mark.parametrize("key,params", SOURCE_ERRORS, ids=[k for k, _ in SOURCE_ERRORS])
def test_source_errors_are_worded_when_they_are_read(key, params):
    err = sources.SourceError(key, **params)
    zh, en = in_both(lambda: str(err))
    check_pair(zh, en)
    assert msgref.show(err.ref(), "en") == i18n.tl("en", key, **params)  # and can be kept, to be worded later


# ---------- venues ----------


def apply_conflict(c):
    prop = vc.propose("ACL", {"dates": "Aug 2028"}, ["https://2028.example.org/cfp"])
    venues.venue_overrides.save("acl", {"location": "Elsewhere"}, None, name="ACL")  # the details moved on after the check
    text = detail_of(c.client.post("/api/venue-check/acl/apply", json={"proposal_id": prop["id"]}))
    venues.venue_overrides.reset("acl")
    vc.dismiss("acl", prop["id"])
    return text


VENUE_CASES: dict[str, Callable[[Any], str]] = {
    "apply without a proposal": lambda c: detail_of(c.client.post("/api/venue-check/acl/apply", json={})),
    "dismiss without a proposal": lambda c: detail_of(c.client.post("/api/venue-check/acl/dismiss", json={})),
    "verify a venue without details": lambda c: detail_of(c.client.post("/api/venue-check/kdd/verified")),
    "apply after the details moved on": apply_conflict,
}


@pytest.mark.parametrize("name", VENUE_CASES)
def test_venue_check_errors_in_both_languages(name, ctx, venue_data):
    shipped(acl())
    zh, en = in_both(lambda: VENUE_CASES[name](ctx))
    check_pair(zh, en)


def entry(detail: dict | None = None, eds=(), seen=None) -> dict:
    d = {"key": "ACL", "status": "confirmed", "edition": "ACL 2027", "edition_year": 2027, "verified_at": "2026-10-24",
         "homepage": "https://2027.example.org/", "sources": [], "deadlines": [{"track": "Main", "abstract": None, "paper": "2027-01-05 AoE"}]}
    return {"detail": {**d, **(detail or {})}, "ccfddl_editions": list(eds), "override": {"ccfddl_seen": seen} if seen else None}


def edition(year, link=None, *dates):
    return {"year": year, "link": link, "timeline": [{"abstract": None, "deadline": d, "comment": None} for d in dates]}


NOW = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)
HOME = "https://2027.example.org/"
SEEN = {"year": 2027, "link": "other.example.com", "dates": ["2027-01-13T11:59:59+00:00"]}
FRESHNESS = {
    "no edition and no check date": entry({"edition": None, "edition_year": None, "verified_at": None}),
    "checked long ago": entry({"verified_at": "2026-01-01"}),
    "checked one month over the limit": entry({"verified_at": "2026-04-24"}),
    "older edition": entry({"edition": "ACL 2026", "edition_year": 2026}, [edition(2026, None, "2026-04-01T00:00:00+00:00"), edition(2027)]),
    "link ccfddl shows": entry(eds=[edition(2027, "https://other.example.com/", "2027-01-06T11:59:59+00:00")]),
    "deadline ccfddl shows": entry(eds=[edition(2027, HOME, "2027-01-13T11:59:59+00:00")]),
    "link changed since the check": entry(eds=[edition(2027, "https://newer.example.com/", "2027-01-13T11:59:59+00:00")], seen=SEEN),
    "deadlines changed since the check": entry(eds=[edition(2027, "https://other.example.com/", "2027-01-20T11:59:59+00:00")], seen=SEEN),
    "newer edition got news": entry({"edition": "ACL 2026", "edition_year": 2026},
                                    [edition(2026, None, "2026-04-01T00:00:00+00:00"), edition(2027, "https://x.example.org/", "2027-04-14T11:59:59+00:00")],
                                    seen={**venue_fresh.fingerprint(entry({"edition_year": 2026}, [edition(2026, None, "2026-04-01T00:00:00+00:00"),
                                                                                                    edition(2027)]), 2026, NOW)}),
}


@pytest.mark.parametrize("name", FRESHNESS)
def test_freshness_reasons_and_chips(name):
    def reasons():
        return [r["text"] + " | " + r["short"] for r in venue_fresh.check(FRESHNESS[name], NOW)["reasons"]]
    zh, en = in_both(reasons)
    assert zh and len(zh) == len(en)
    for a, b in zip(zh, en):
        check_pair(a.split(" | ")[0], b.split(" | ")[0])
        check_pair(a.split(" | ")[1], b.split(" | ")[1])


def test_freshness_counts_months_in_english():
    config.save_settings({"ui_language": "en"})

    def text(verified, limit):
        return venue_fresh.check(entry({"verified_at": verified}), NOW, months=limit)["reasons"][0]["text"]
    assert text("2026-09-25", 1) == "Last checked on 2026-09-25, 1 month ago (limit: 1 months)"
    assert text("2026-08-25", 2) == "Last checked on 2026-08-25, 2 months ago (limit: 2 months)"


def test_diff_labels_and_lines_in_both_languages():
    before = acl()
    after = venues.venue_overrides.merge_detail(before, {
        "edition": "ACL 2028", "deadlines": [{"track": None, "abstract": "2028-01-01 AoE", "paper": "2028-01-08 AoE"}],
        "tracks": [{"name": "Short", "page_limit": "4 pages", "notes": "no appendix"}, {"name": "Long", "page_limit": None, "notes": None}]})
    zh, en = in_both(lambda: {d["field"]: d for d in vc.diff_detail(before, after)})
    assert list(zh) == list(en) == ["edition", "deadlines", "tracks"]
    for field in zh:
        check_pair(zh[field]["label"], en[field]["label"])

    def rows(diff, field):
        return [(x["op"], x["text"]) for x in diff[field]["lines"]]
    assert rows(zh, "deadlines") == [("-", "Main：Full paper deadline 2099-01-05 AoE"),
                                     ("+", "Main track：Abstract deadline 2028-01-01 AoE · Full paper deadline 2028-01-08 AoE")]
    assert rows(en, "deadlines") == [("-", "Main: Full paper deadline 2099-01-05 AoE"),
                                     ("+", "Main track: Abstract deadline 2028-01-01 AoE · Full paper deadline 2028-01-08 AoE")]
    assert rows(zh, "tracks") == [("-", "Long：8 pages"), ("+", "Short：4 pages（no appendix）"), ("+", "Long：—")]
    assert rows(en, "tracks") == [("-", "Long: 8 pages"), ("+", "Short: 4 pages (no appendix)"), ("+", "Long: —")]


@pytest.mark.parametrize("field", vc.FIELDS)
def test_every_diff_field_has_a_label_in_both_languages(field):
    key = "venue.field." + field.replace(".", "_")
    check_pair(i18n.tl("zh", key), i18n.tl("en", key))


@pytest.mark.parametrize("code", venues.CCF_FIELDS)
def test_ccf_fields_are_worded_by_the_interface_language(code):
    entry_ = {"key": "x", "acronym": "X", "title": "", "core": None, "ccf": "A", "field": code, "editions": [], "accept_rates": []}
    zh, en = in_both(lambda: venues.summary(entry_)["field"])
    check_pair(zh, en)
    assert venues.summary({**entry_, "field": "XX"})["field"] == "XX"  # a code CCF does not define is shown as it is


# ---------- kept text is worded when it is served ----------


def test_references_are_text_in_any_field_and_old_text_passes_through():
    nested = msgref.ref("scholars.rank.unchecked", why=msgref.ref("scholars.refresh.no_author"))
    assert msgref.show(nested, "zh") == "无法核实：OpenAlex 里找不到这个人"
    assert msgref.show(nested, "en") == "Couldn't verify: Not found in OpenAlex"
    assert msgref.show("无法核实：同名多人") == "无法核实：同名多人" and msgref.show(None) is None and msgref.show(3) == 3
    assert msgref.show("i18n:scholars.rank.deceased|{not json") == "i18n:scholars.rank.deceased|{not json"
    config.save_settings({"ui_language": "en"})
    assert msgref.show(msgref.ref("scholars.rank.deceased")) == "Deceased" and msgref.show(msgref.ref("scholars.rank.deceased"), "zh") == "已故"
    assert i18n.vault_lang() == "zh" and msgref.show(msgref.ref("scholars.rank.deceased"), i18n.vault_lang()) == "已故"


def propose(**kw) -> dict:
    run = {"created": "2026-10-01T10:00:00", "status": "proposed", "manual": True, "citation_source": "Google Scholar label ranking, 2026-10-01",
           "source": msgref.ref("scholars.refresh.src_read", source=msgref.ref("scholars.refresh.src_app_window"), date="2026-10-01"),
           "ranking": [{"name": "Ada Example", "scholar_id": "A" * 12, "citations": 900, "rank": 1}],
           "skipped": [{"name": "Zed Student", "citations": 800, "reason": msgref.ref("scholars.rank.same_lab", name="Ada Example")},
                       {"name": "Old Prof", "citations": 700, "reason": msgref.ref("scholars.rank.inactive", n=3, last="2021-05")},
                       {"name": "Legacy", "citations": 600, "reason": "在排除名单里"}],
           "enter": [{"name": "Ada Example", "scholar_id": "A" * 12, "openalex": "A1"}], "leave": [], "moves": [], **kw}
    return scholar_refresh.store.add("runs", msgref.persist(run), "run")  # as the app writes it into the vault file


def test_a_stored_suggestion_follows_the_interface_language():
    run = propose()
    zh, en = in_both(lambda: scholar_refresh.status())
    assert [x["reason"] for x in zh["pending"]["skipped"]] == ["同 lab 已有 Ada Example", "3 年内没有新发表（最近 2021-05）", "在排除名单里"]
    assert [x["reason"] for x in en["pending"]["skipped"]] == ["Ada Example already represents this lab",
                                                               "No new publications in the last 3 years (latest 2021-05)", "在排除名单里"]  # text kept from before: as it was
    check_pair(zh["pending"]["source"], en["pending"]["source"])
    assert zh["history"][0]["source"] == zh["pending"]["source"] and en["history"][0]["source"] == en["pending"]["source"]
    assert "已打开" not in zh["pending"]["source"] and "App 窗口" in zh["pending"]["source"] and "app window" in en["pending"]["source"]
    raw = config.LAB_TRACKER_DIR.joinpath("scholar-ranking.yaml").read_text(encoding="utf-8")
    kept = yaml.safe_load(raw)
    assert "i18n:" not in raw  # no encoded string in the shared file: structured fields, with the words in the vault language
    assert kept["runs"][0]["skipped"][0]["reason"] == {"code": "scholars.rank.same_lab", "params": {"name": "Ada Example"},
                                                       "text": "同 lab 已有 Ada Example"}
    assert kept["runs"][0]["id"] == run["id"]
    assert kept["runs"][0]["source"]["code"] == "scholars.refresh.src_read" and kept["runs"][0]["source"]["params"]["source"]["code"]
    # a Mac whose version does not know the code still shows the words that were written
    kept["runs"][0]["skipped"][0]["reason"]["code"] = "scholars.rank.made_up_later"
    assert msgref.show(kept["runs"][0]["skipped"][0]["reason"]) == "同 lab 已有 Ada Example"


def test_a_new_suggestion_is_written_with_structured_reasons_and_words_in_the_vault_language():
    config.save_settings({"ui_language": "en"})
    run = {"created": "2026-10-01T10:00:00", "status": "proposed", "source": msgref.ref("scholars.refresh.src_openalex"),
           "skipped": [{"name": "Zed Student", "reason": msgref.ref("scholars.rank.same_lab", name="Ada Example")}]}
    run_id = scholar_refresh._save_run("2026-10-01", run)
    raw = config.LAB_TRACKER_DIR.joinpath("scholar-ranking.yaml").read_text(encoding="utf-8")
    assert "i18n:" not in raw
    stored = next(x for x in yaml.safe_load(raw)["runs"] if x["id"] == run_id)
    assert stored["source"]["code"] == "scholars.refresh.src_openalex" and CJK.search(stored["source"]["text"])  # the vault is zh by default
    assert stored["skipped"][0]["reason"]["text"] == "同 lab 已有 Ada Example"
    shown = scholar_refresh.status()["pending"]  # this Mac is in English
    assert shown["skipped"][0]["reason"] == "Ada Example already represents this lab" and not CJK.search(shown["source"])


def test_applying_notes_are_worded_now_and_the_provenance_in_the_vault_language(monkeypatch):
    monkeypatch.setattr(scholars, "refresh_in_background", lambda *a, **k: {})
    scholars.add({"name": "Bob Pinned", "openalex": "A2"})
    scholars.update(scholars.scholars()[0]["id"], {"scholar_id": "B" * 12})
    scholar_refresh.set_pin(scholars.scholars()[0]["id"], True)
    run = propose(leave=[{"name": "Bob Pinned", "scholar_id": "B" * 12}], enter=[{"name": "Ada Example", "scholar_id": "A" * 12, "openalex": "A1"},
                                                                             {"name": "Zed Student", "scholar_id": "Z" * 12}])
    scholar_refresh.exclude({"name": "Zed Student", "scholar_id": "Z" * 12}, "")
    config.save_settings({"ui_language": "en"})
    out = scholar_refresh.apply(run["id"])
    assert out["notes"] == ["Zed Student: on the exclude list, not added", "Bob Pinned: pinned, not removed"]
    added = next(s for s in scholars.scholars() if s["name"] == "Ada Example")
    assert added["sources"]["added"] == f"{run['id']} 建议，{date.today().isoformat()} 确认"  # the vault is zh whatever this Mac shows
    assert added["sources"]["rank"] == "Google Scholar（App 窗口）（2026-10-01 读取）"
    monkeypatch.setitem(config.PROFILE, "vault_language", "en")
    run2 = propose(enter=[{"name": "Cat Example", "scholar_id": "C" * 12}])
    scholar_refresh.apply(run2["id"])
    cat = next(s for s in scholars.scholars() if s["name"] == "Cat Example")
    assert cat["sources"]["added"] == f"{run2['id']} suggestion, confirmed {date.today().isoformat()}"
    with pytest.raises(ValueError) as gone:
        scholar_refresh.apply(run["id"])
    assert "already been handled" in str(gone.value)


def test_the_default_exclude_reason_is_kept_as_fields_not_as_an_encoded_string():
    rec = scholar_refresh.exclude({"name": "Ada Example"}, "")
    assert rec["reason"] == "手动排除"
    assert scholar_refresh.excluded()[0]["reason"] == {"code": "scholars.refresh.manual_exclude", "text": "手动排除"}  # vault language, with its code
    scholar_refresh.exclude({"name": "Grace Sample"}, "not in the field")
    zh, en = in_both(lambda: {x["name"]: x["reason"] for x in scholar_refresh.status()["excluded"]})
    assert zh == {"Ada Example": "手动排除", "Grace Sample": "not in the field"}
    assert en == {"Ada Example": "Excluded manually", "Grace Sample": "not in the field"}


def test_imported_snapshots_name_their_source_in_the_language_asked():
    text = "1. Ada Example · BBBBBBBBBBBB · 124k · Example University\n"
    config.save_settings({"ui_language": "en"})
    saved = scholar_refresh.import_snapshot(text)
    assert saved["source"] == "Google Scholar (imported)" and saved["file"].endswith("-google-scholar-import.json")
    assert json.loads((scholar_refresh.SNAPS / saved["file"]).read_text(encoding="utf-8"))["source"].startswith("i18n:")
    zh, en = in_both(lambda: scholar_refresh.status()["snapshot"]["source"])
    assert (zh, en) == ("Google Scholar（导入）", "Google Scholar (imported)")


def test_job_state_and_attempts_follow_the_interface_language(monkeypatch):
    err = msgref.ref("scholars.refresh.oa_unavailable", error=msgref.ref("scholars.oa.http", status=503))
    monkeypatch.setattr(scholar_refresh, "_job", {"running": False, "stage": "failed", "error": err, "source": msgref.ref("scholars.refresh.src_openalex"),
                                                  "current": msgref.ref("scholars.capture.page", label="label:recsys", n=2)})
    scholar_refresh._record_attempt(False, err)
    zh, en = in_both(lambda: scholar_refresh.status())
    for key in ("error", "source", "current"):
        check_pair(zh["job"][key], en["job"][key])
    check_pair(zh["attempts"]["error"], en["attempts"]["error"])
    assert en["job"]["current"] == "label:recsys (page 2)" and en["job"]["stage"] == "failed"
    monkeypatch.setattr(scholars, "_job", {"running": False, "current": msgref.ref("scholars.job.logos"), "error": msgref.ref("scholars.oa.quota")})
    zh, en = in_both(scholars.job_view)
    check_pair(zh["current"], en["current"])
    check_pair(zh["error"], en["error"])


def test_openalex_errors_are_kept_as_references(net, monkeypatch):  # noqa: F811
    net.add("api.openalex.org/authors", {}, status=429)
    with pytest.raises(R.CheckUnavailable) as e:
        scholar_refresh._oa("authors", search="Ada")
    zh, en = in_both(lambda: msgref.show(str(e.value)))
    check_pair(zh, en)
    assert "OPENALEX_API_KEY" in zh and "OPENALEX_API_KEY" in en


def test_a_failed_tracker_run_words_its_error_when_the_page_asks(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker_run, "_state", {"running": False, "stage": "failed", "ok": False,
                                                "error": str(tracker_run.RunError("track.run.step_failed", step="fetch", code=2))})
    zh, en = in_both(lambda: tracker_run.status()["error"])
    assert (zh, en) == ("fetch 失败（2）", "fetch failed (exit code 2)")
    monkeypatch.setattr(tracker_run, "_state", {"running": False, "error": "Traceback: boom"})  # raw tracker output is shown as it is
    assert tracker_run.status()["error"] == "Traceback: boom"


def test_a_failing_feed_source_is_remembered_and_worded_for_each_reading(net, monkeypatch):  # noqa: F811
    monkeypatch.setattr(sources, "_spawn_fill", sources.acl_fill)
    tracking.add_follow("keyword", "memory")
    config.save_settings({"ui_language": "zh"})
    first = {s["id"]: s["error"] for s in tracking.feed(today=TODAY, refresh=True)[0]["sources"]}  # nothing is routed: all three fail
    assert first["arxiv"] and first["openreview"] and all(CJK.search(e) for e in first.values() if e)  # the Anthology volumes are still loading
    config.save_settings({"ui_language": "en"})
    later = {s["id"]: s["error"] for s in tracking.feed(today=TODAY)[0]["sources"]}  # from the failure records, now in English
    assert set(later) == {"arxiv", "openreview", "acl"}
    for sid, text in later.items():
        assert text and not CJK.search(text) and not LEAK.search(text), (sid, text)
        assert "retrying" in text, text
    record = json.loads(next(tracking.FEED_CACHE.glob("*.fail.json")).read_text(encoding="utf-8"))
    assert record["error"].startswith("i18n:")


def test_reasons_the_page_stores_are_worded_in_the_vault_language_not_the_interface_language(ctx, monkeypatch):
    """The reading list and the exclusion list live in the vault, shared by both Macs."""
    config.save_settings({"ui_language": "en"})

    def ask(key: str, **params: Any) -> str:
        r = ctx.client.post("/api/track/reason", json={"key": key, "params": params})
        assert r.status_code == 200, r.text
        return r.json()["text"]

    assert ask("scholars_lines", names=["Ada", "Bob"], lines=["Atlas"]) == "追踪 · 领域 Top 学者：Ada、Bob · 触及 Atlas"
    assert ask("scholars", names=["Ada"]) == "追踪 · 领域 Top 学者：Ada"
    assert ask("follow_person", value="Ada Example") == "关注学者：Ada Example"
    assert ask("exclude_review") == "确认建议时排除"
    monkeypatch.setitem(config.PROFILE, "vault_language", "en")
    config.save_settings({"ui_language": "zh"})
    assert ask("scholars_lines", names=["Ada", "Bob"], lines=["Atlas"]) == "Track · top scholars in the field: Ada, Bob · touches Atlas"
    assert ask("follow_keyword", value="rag") == "Following keyword: rag"
    bad = ctx.client.post("/api/track/reason", json={"key": "track.api.no_candidate"})  # only the reasons it lists
    assert bad.status_code == 400 and bad.json()["detail"] == "不认识的理由"


def test_capture_defaults_are_written_into_the_reading_list_in_the_vault_language(monkeypatch):
    monkeypatch.setattr(reading, "fetch_arxiv_title", lambda aid: f"Title of {aid}")
    config.save_settings({"ui_language": "en"})
    tracking.capture("2606.17328", "")
    assert [(i["reason"], i["pushed_by"]) for i in reading.load()] == [("手动捕捉", "追踪 · 捕捉")]  # vault_language is zh by default
    monkeypatch.setitem(config.PROFILE, "vault_language", "en")
    tracking.capture("2606.17329", "")
    item = next(i for i in reading.load() if i["arxiv"] == "2606.17329")
    assert (item["reason"], item["pushed_by"]) == ("Captured manually", "Track · capture")
    config.save_settings({"ui_language": "zh"})
    tracking.capture("2606.17330", "my reason")
    assert next(i for i in reading.load() if i["arxiv"] == "2606.17330")["reason"] == "my reason"  # the user's own words are never replaced


def test_ranking_reasons_are_references_and_cover_every_branch():
    cands = [{"name": n, "citations": c, "scholar_id": n[0] * 12, **kw} for n, c, kw in [
        ("Excluded", 900, {}), ("Dead", 800, {"deceased": True}), ("Noisy", 700, {"noise": True}), ("Head", 600, {"lab": "x"}),
        ("Student", 500, {"lab": "x"}), ("Mystery", 400, {}), ("Few", 300, {}), ("Idle", 200, {})]]

    def check(c):
        if c["name"] == "Mystery":
            raise R.Unresolved(msgref.ref("scholars.refresh.ambiguous"))
        return {"field_papers": 0 if c["name"] == "Few" else 9, "inactive": R.inactive_verdict("2019-01-01", date(2026, 10, 25)) if c["name"] == "Idle" else None}
    out = R.rank(cands, check, top_n=5, alternates=0, min_papers=3, excluded=[{"name": "Excluded"}])
    reasons = {c["name"]: c["reason"] for c in out["skipped"]}
    assert set(reasons) == {"Excluded", "Dead", "Noisy", "Student", "Mystery", "Few", "Idle"}
    assert all(r.startswith("i18n:") for r in reasons.values())
    zh, en = in_both(lambda: {k: msgref.show(v) for k, v in reasons.items()})
    for k in zh:
        check_pair(zh[k], en[k])
    assert zh["Student"] == "同 lab 已有 Head" and en["Student"] == "Head already represents this lab"
    for text in (R.noise_verdict([{"title": "x", "cites": 9000, "team": 99}], R.compile_model({})), R.inactive_verdict(None, date.today()),
                 R.noise_verdict([{"title": "x", "cites": 100, "team": 1}], R.compile_model({"domain_gate": {"patterns": ["zzz"]}}))):
        pair = in_both(lambda: msgref.show(text))
        check_pair(*pair)
