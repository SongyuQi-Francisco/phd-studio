"""app/scholars.py: the 追踪 page is told a logo exists only when it is on disk, so it never asks for one that 404s."""
from __future__ import annotations

import os
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import scholars, track_api

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

GOOD, DEAD, NONE = "https://img.example.test/good-logo.png", "https://img.example.test/dead-logo.png", None


@pytest.fixture
def world(monkeypatch):
    """Three scholars at three institutions: a logo that downloads, a logo link that is dead, and no logo link."""
    for d in (scholars.CACHE / "img", scholars.THUMBS):
        d.mkdir(parents=True, exist_ok=True)
    fetched: list[str] = []

    def fake_download(url, limit, timeout=30):
        fetched.append(url)
        if url == DEAD:
            req = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))
        return PNG, "image/png"

    jobs: list[dict] = []
    monkeypatch.setattr(scholars, "_download", fake_download)
    monkeypatch.setattr(scholars, "refresh_in_background", lambda force=False, ids=None: jobs.append({"ids": ids}) or {})
    monkeypatch.setattr(scholars, "_logos_tried", [0.0])
    scholars._cache_put("institutions", {
        "I100": {"name": "Alpha University", "logo": GOOD, "homepage": "https://alpha.example.test"},
        "I200": {"name": "Beta Institute", "logo": DEAD, "homepage": None},
        "I300": {"name": "Gamma Lab", "logo": NONE, "homepage": None},
    })
    for n, inst in enumerate(("I100", "I200", "I300"), 1):
        s = scholars.add({"name": f"Person {n}", "openalex": f"A{n}"})
        scholars._cache_put(f"author-A{n}", {"author": {"inst": {"name": f"Inst {inst}", "id": inst}}, "works": [], "arxiv": [],
                                             "by_year": [], "fetched": "2026-10-25"})
        assert s["openalex"] == f"A{n}"
    return fetched, jobs


def logo_flags() -> dict[str, bool]:
    return {r["inst"]["id"]: r["inst"]["logo"] for r in scholars.activity()["scholars"]}


def test_a_logo_link_is_not_a_logo_until_it_is_downloaded(world):
    fetched, jobs = world
    assert logo_flags() == {"I100": False, "I200": False, "I300": False}  # the page asks for nothing yet
    assert fetched == []  # activity() reads the cache: no downloads on the request path


def test_logos_are_resolved_in_the_background_then_flagged(world):
    fetched, jobs = world
    logo_flags()
    assert jobs == [{"ids": set()}]  # a job with no scholars: just the logos
    scholars._fetch_logos()
    assert logo_flags() == {"I100": True, "I200": False, "I300": False}  # the dead link and the missing one are not requested
    assert sorted(fetched) == sorted([GOOD, DEAD])  # and the missing one was never tried


def test_every_flagged_logo_is_served(world):
    scholars._fetch_logos()
    client = TestClient(_app())
    flags = logo_flags()
    assert any(flags.values())
    for inst_id, flagged in flags.items():
        if flagged:  # what the page asks for is what exists
            r = client.get(f"/api/track/logo/{inst_id}")
            assert r.status_code == 200 and r.content == PNG
    assert client.get("/api/track/logo/I200").status_code == 404  # the route is unchanged: this is what the page no longer asks for


def test_a_dead_link_is_not_tried_again_for_a_day_nor_does_it_restart_the_job(world):
    fetched, jobs = world
    logo_flags()
    scholars._fetch_logos()
    first = list(fetched)
    jobs.clear()
    scholars._logos_tried[0] = 0.0  # even when the half-hour guard has passed: nothing is pending any more
    scholars._fetch_logos()
    logo_flags()
    assert fetched == first and jobs == []


def test_the_logo_only_job_is_not_restarted_within_half_an_hour(world):
    fetched, jobs = world
    logo_flags()
    logo_flags()
    assert len(jobs) == 1  # the second page visit finds the guard set, although nothing is on disk yet


def test_a_logo_that_was_pruned_from_the_cache_is_flagged_off_and_fetched_again(world):
    fetched, jobs = world
    scholars._fetch_logos()
    assert logo_flags()["I100"] is True
    for f in (scholars.CACHE / "img").glob("i-I100.*"):
        f.unlink()
    assert logo_flags()["I100"] is False
    scholars._fetch_logos()
    assert logo_flags()["I100"] is True and fetched.count(GOOD) == 2


def test_a_failed_download_marker_expires(world):
    fetched, jobs = world
    scholars._fetch_logos()
    marker = scholars.CACHE / "img" / "i-I200.fail"
    assert marker.exists()
    old = time.time() - scholars.FAIL_TTL - 60
    os.utime(marker, (old, old))
    assert scholars._logos_pending(scholars._cache_get("institutions")[0]) == ["I200"]


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(track_api.router)
    return app
