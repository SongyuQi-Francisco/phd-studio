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


def _p(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()


VAULT_DIR = _p(_paths["vault"])
PAPERS_DIR = VAULT_DIR / _paths.get("papers", "Papers")
VAULT_PDFS_DIR = VAULT_DIR / _paths.get("pdfs", "PDFs")
READING_QUEUE = VAULT_DIR / _paths.get("reading_queue", "Reading Queue.md")
READING_LIST = VAULT_DIR / _paths.get("reading_list", "reading.yaml")
AGENTS_MD = VAULT_DIR / _paths.get("rules", "AGENTS.md")
ZOTERO_DIR = _p(_paths.get("zotero", "~/Zotero"))
SECRETS_DIR = _p(_paths.get("secrets", "~/.config/secrets"))
CLAUDE_CLI = Path(_paths.get("claude_cli") and os.path.expanduser(_paths["claude_cli"])
                  or shutil.which("claude") or "claude")
# Extra folders Claude may read (code repos, drafts), relative to the vault or absolute.
EXTRA_READ_DIRS = [str(_p(d) if d.startswith(("/", "~")) else (VAULT_DIR / d).resolve())
                   for d in _paths.get("extra_read_dirs", [])]
VAULT_NAME = VAULT_DIR.name

# Small, valuable state (chat logs, session ids, settings) stays with the project.
STATE_DIR = STUDIO_DIR / "state"
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
    # "" = whatever the Claude CLI defaults to for this account.
    "claude_model": "",
    # Engine for selection translation and full-PDF translation: "deepseek" | "google".
    "translate_engine": "deepseek",
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
