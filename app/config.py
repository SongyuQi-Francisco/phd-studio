"""Paths, secrets and user settings.

Everything personal (vault location, Zotero, research profile) comes from
studio.toml next to this project (git-ignored; see studio.example.toml), or
from the file named by $STUDIO_CONFIG.

The Obsidian vault and Zotero stay the single source of truth. This app only
reads them; every vault write goes through Claude and needs approval in the UI.
Large binaries (downloaded PDFs, bilingual PDFs, crops) live outside the vault.
"""
from __future__ import annotations

import json
import os
import re
import socket
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any

STUDIO_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = Path(os.environ.get("STUDIO_CONFIG", STUDIO_DIR / "studio.toml"))

if not CONFIG_FILE.exists():
    sys.exit(f"Missing {CONFIG_FILE}. Copy studio.example.toml to studio.toml and fill in your paths.")

_cfg = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
_paths = _cfg.get("paths", {})
PROFILE: dict[str, Any] = _cfg.get("profile", {})


def _months(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


# [venues] stale_months: venue details count as out of date this many months after they were last verified.
VENUE_STALE_MONTHS = _months(_cfg.get("venues", {}).get("stale_months"), 6)


def _p(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()


VAULT_DIR = _p(_paths["vault"])
PAPERS_DIR = VAULT_DIR / _paths.get("papers", "Papers")
VAULT_PDFS_DIR = VAULT_DIR / _paths.get("pdfs", "PDFs")
READING_QUEUE = VAULT_DIR / _paths.get("reading_queue", "Reading Queue.md")
READING_LIST = VAULT_DIR / _paths.get("reading_list", "reading.yaml")
PLAN_FILE = VAULT_DIR / _paths.get("plan", "plan.yaml")
# Venue details the assistant re-checked and the user confirmed: a sync-safe store merged on top of
# data/venue_details.json. Default: next to plan.yaml.
VENUE_OVERRIDES_FILE = (VAULT_DIR / _paths["venue_overrides"] if _paths.get("venue_overrides")
                        else PLAN_FILE.parent / "venue_details.yaml")
IDEAS_FILE = VAULT_DIR / _paths.get("ideas", "ideas.yaml")
IDEAS_DIR = VAULT_DIR / _paths.get("ideas_dir", "Ideas")
IDEA_TEMPLATE = _paths.get("idea_template", "Templates/Idea Note Template.md")
ROADMAP = VAULT_DIR / _paths.get("roadmap", "Atlas/PhD Roadmap.md")
IDEA_RADAR = VAULT_DIR / _paths.get("idea_radar", "Ideas/Idea Radar.md")
LAB_TRACKER_DIR = VAULT_DIR / _paths.get("lab_tracker", "Lab Tracker")
TRACKER_DIR = _p(_paths["tracker"]) if _paths.get("tracker") else None  # research_tracker code dir (runs/)
AGENTS_MD = VAULT_DIR / _paths.get("rules", "AGENTS.md")
# Folders the research-line chats point at. Empty: a top-level vault folder with that name,
# number prefix allowed ("08 Review Packets"); see line_chat.vault_folder.
EVIDENCE = _paths.get("evidence", "")
REVIEW_PACKETS = _paths.get("review_packets", "")
TEMPLATES = _paths.get("templates", "")
REVIEW_TEMPLATE = _paths.get("review_template", "")  # advisor packet template; default: found in TEMPLATES
# Research maps (研究地图): one folder per project under this vault folder; see docs/research-map.md.
RMAP_DIR = VAULT_DIR / _paths.get("research_maps", "Research Maps")
ZOTERO_DIR = _p(_paths.get("zotero", "~/Zotero"))
SECRETS_DIR = _p(_paths.get("secrets", "~/.config/secrets"))
_cli = os.path.expanduser(_paths.get("claude_cli", ""))
CLAUDE_CLI = Path(_cli if _cli and Path(_cli).exists() else shutil.which("claude") or "claude")
# Extra folders Claude may read (code repos, drafts), relative to the vault or absolute.
EXTRA_READ_DIRS = [str(_p(d) if d.startswith(("/", "~")) else (VAULT_DIR / d).resolve())
                   for d in _paths.get("extra_read_dirs", [])]
VAULT_NAME = VAULT_DIR.name

# Small, valuable state (chat logs, session ids, settings) stays with the project, one
# subfolder per machine: when the project folder is synced between computers, each
# machine only ever writes its own subfolder, so sync never sees two writers.
DEVICE = re.sub(r"[^A-Za-z0-9_-]+", "-", os.environ.get("STUDIO_DEVICE") or socket.gethostname()
                .removesuffix(".local")).strip("-") or "this-mac"
STATE_ROOT = _p(_paths["state"]) if _paths.get("state") else STUDIO_DIR / "state"
STATE_DIR = STATE_ROOT / DEVICE
CHATS_DIR = STATE_DIR / "chats"
SETTINGS_FILE = STATE_DIR / "settings.json"

# Large, re-creatable files.
if sys.platform == "darwin":
    DATA_DIR = Path.home() / "Library" / "Application Support" / "PhD Studio"
else:
    DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "phd-studio"
DATA_DIR = _p(_paths["data"]) if _paths.get("data") else DATA_DIR
PDF_CACHE_DIR = DATA_DIR / "pdfs"
BILINGUAL_DIR = DATA_DIR / "bilingual"
TEXT_DIR = DATA_DIR / "text"
CROPS_DIR = DATA_DIR / "crops"

for _d in (STATE_DIR, CHATS_DIR, PDF_CACHE_DIR, BILINGUAL_DIR, TEXT_DIR, CROPS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# One-time move of state written before it became per-device.
_legacy = STATE_ROOT
for _name in ("chats", "settings.json", "sessions.json"):
    _old = _legacy / _name
    if _old.exists() and not (_old.is_dir() and not any(_old.iterdir())):
        if _old.is_dir():
            for _f in _old.iterdir():
                _f.rename(CHATS_DIR / _f.name)
            _old.rmdir()
        elif not (STATE_DIR / _name).exists():
            _old.rename(STATE_DIR / _name)
    elif _old.is_dir():
        _old.rmdir()


def load_secrets(*names: str) -> dict[str, str]:
    """Read KEY=VALUE pairs from <secrets>/<name>.env without touching os.environ."""
    out: dict[str, str] = {}
    for name in names:
        f = SECRETS_DIR / f"{name}.env"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip().removeprefix("export ").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def secret(key: str, *files: str) -> str:
    return os.environ.get(key) or load_secrets(*files).get(key, "")


DEFAULT_SETTINGS: dict[str, Any] = {
    # Make bilingual PDFs ahead of time when a reading item becomes "T1" or "T1T2"; "off" = only on click.
    # Off by default: every Mac makes, stores and pays for its own copy.
    "bilingual_prefetch": "off",
    # "" = whatever the Claude CLI defaults to for this account.
    "claude_model": "",
    # Engine for selection translation and full-PDF translation: "deepseek" | "glm" | "google".
    "translate_engine": "deepseek",
    # Add papers to Zotero by themselves: "off" | "triage" (given a tier, or a note) | "all" (also inbox).
    "zotero_auto": "off",
    # Zotero collection key new items go into; "" = the library root.
    "zotero_collection": "",
    # Interface language on this Mac: "zh" | "en" | "system" (the OS language). See app/i18n.py.
    "ui_language": "system",
    # The assistant's answer language: "" = the profile's answer_language | "ui" = the interface language | "zh" | "en".
    "answer_language": "",
}


def load_settings() -> dict[str, Any]:
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return s


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    s = load_settings()
    s.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    SETTINGS_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    return s
