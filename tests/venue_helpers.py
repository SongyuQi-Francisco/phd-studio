"""Shared setup for the venue details tests."""
from __future__ import annotations

import json
from datetime import date

import pytest

from app import venues

TODAY = date.today().isoformat()


@pytest.fixture(autouse=True)
def own_details_file(tmp_path, monkeypatch):
    """Import this into a test module: `shipped()` then writes a per-test file, not the shared sandbox one."""
    monkeypatch.setattr(venues, "DETAILS_FILE", tmp_path / "venue_details.json")


def shipped(*items):
    """Make these the shipped data/venue_details.json."""
    venues.DETAILS_FILE.write_text(json.dumps({"verified_at": "2026-01-01", "venues": list(items)}), encoding="utf-8")
    venues._catalog.cache_clear()


def acl(**kw):
    base = {"key": "ACL", "status": "confirmed", "edition": "ACL 2027", "edition_year": 2027, "homepage": "https://2027.example.org/",
            "cfp_url": "https://2027.example.org/cfp", "deadlines": [{"track": "Main", "abstract": None, "paper": "2099-01-05 AoE"}],
            "format": {"template": "ACL style", "review_model": "double-anonymous", "notable_rules": ["Anonymous"]},
            "tracks": [{"name": "Long", "page_limit": "8 pages", "notes": None}], "sources": ["https://2027.example.org/cfp"]}
    return {**base, **kw}
