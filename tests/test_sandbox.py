"""The test sandbox itself: nothing here may point at the user's real vault, data or state."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import config, library, plan, reading, store, venue_check, venue_overrides, venues

from .conftest import ROOT

REAL_PLACES = [Path.home() / "Library" / "Application Support" / "PhD Studio", Path.home() / ".local" / "share" / "phd-studio",
               Path.home() / "Desktop", Path.home() / "Zotero", Path.home() / ".config" / "secrets"]


@pytest.mark.parametrize("name", ["VAULT_DIR", "PAPERS_DIR", "VAULT_PDFS_DIR", "READING_QUEUE", "READING_LIST", "PLAN_FILE",
                                  "IDEAS_FILE", "VENUE_OVERRIDES_FILE", "AGENTS_MD", "DATA_DIR", "PDF_CACHE_DIR", "TEXT_DIR", "CROPS_DIR",
                                  "BILINGUAL_DIR", "STATE_DIR", "CHATS_DIR", "SETTINGS_FILE", "ZOTERO_DIR", "SECRETS_DIR",
                                  "RMAP_DIR"])
def test_config_paths_live_inside_the_sandbox(name):
    path = getattr(config, name)
    assert path.is_relative_to(ROOT), f"{name} = {path}"
    assert not any(path.is_relative_to(real) for real in REAL_PLACES)


def test_stores_and_caches_are_wired_to_the_sandbox():
    for p in (plan.plan_store.path, plan.idea_store.path, reading.READING_LIST, reading.READING_VIEW, reading._LOCK, reading._TMP,
              store.CONFLICTS_DIR, reading.CONFLICTS_DIR, venues.VENUE_DIR, venues.STAMP, library._ZOTERO_SNAPSHOT,
              plan.plan_store._lock, plan.plan_store._tmp, venue_overrides.store.path, venue_overrides.store._lock,
              venue_overrides.store._tmp, venue_check.PROPOSALS_FILE):
        assert p.is_relative_to(ROOT), p


def test_every_test_starts_from_an_empty_vault():
    assert [p.name for p in config.VAULT_DIR.rglob("*") if p.is_file()] == []
    assert reading.load() == [] and plan.ideas() == [] and plan.plan_store.load()["milestones"] == []


def test_network_is_blocked():
    import httpx
    with pytest.raises(RuntimeError, match="network access"):
        httpx.get("https://example.com")


def test_state_dir_is_overridable_and_per_device():
    assert config.STATE_DIR == config.STATE_ROOT / "test"
    assert config.STATE_ROOT.is_relative_to(ROOT)
