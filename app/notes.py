"""Reading arbitrary vault notes: safe paths, frontmatter, sections, links."""
from __future__ import annotations

import glob
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from .config import VAULT_DIR, VAULT_NAME

_FM = re.compile(r"^---\n(.*?)\n---\n?", re.S)
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")


def resolve(rel: str) -> Path:
    """A vault-relative markdown path, refusing anything outside the vault."""
    p = (VAULT_DIR / rel).resolve()
    if not p.is_relative_to(VAULT_DIR.resolve()) or p.suffix != ".md" or not p.exists():
        raise FileNotFoundError(rel)
    return p


def rel(p: Path) -> str:
    return str(p.resolve().relative_to(VAULT_DIR.resolve()))


def obsidian_url(p: Path) -> str:
    return f"obsidian://open?vault={quote(VAULT_NAME)}&file={quote(rel(p))}"


def split(text: str) -> tuple[dict[str, Any], str]:
    m = _FM.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return (fm if isinstance(fm, dict) else {}), text[m.end():]


def sections(body: str) -> dict[str, str]:
    """'## Heading' -> section text (first occurrence wins)."""
    out: dict[str, str] = {}
    parts = re.split(r"^## +(.+?)\s*$", body, flags=re.M)
    for i in range(1, len(parts) - 1, 2):
        out.setdefault(parts[i].strip(), parts[i + 1].strip())
    return out


def find_section(secs: dict[str, str], *names: str) -> str:
    for name in names:
        for k, v in secs.items():
            if k.lower().startswith(name.lower()):
                return v
    return ""


def first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if block and not block.startswith(("|", "#", "<!--")):
            return re.sub(r"\s+", " ", block)
    return ""


def bullets(text: str) -> list[str]:
    return [re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", ln).strip() for ln in text.splitlines()
            if re.match(r"^\s*(?:[-*]|\d+\.)\s+", ln)]


def strip_links(text: str) -> str:
    """Plain text for cards: wikilinks -> their label, no bold/code markers or list dashes."""
    text = WIKILINK.sub(lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return re.sub(r"(^|\n)\s*[-*]\s+", r"\1", text).strip()


def read(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = split(text)
    return {"path": rel(path), "name": path.stem, "frontmatter": _plain(fm), "body": body,
            "sections": sections(body), "obsidian_url": obsidian_url(path), "mtime": path.stat().st_mtime}


def _plain(fm: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in fm.items()}


def find_by_name(name: str) -> Path | None:
    """Resolve an Obsidian [[wikilink]] target by file name anywhere in the vault."""
    name = name.strip()
    hits = [p for p in VAULT_DIR.rglob(f"{glob.escape(name)}.md") if ".obsidian" not in p.parts and ".trash" not in p.parts]
    return min(hits, key=lambda p: len(p.parts)) if hits else None
