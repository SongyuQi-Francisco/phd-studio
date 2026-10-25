"""Interface language (#49): t(key, **params) over app/locales/<lang>/<module>.json.

The same rules as web/i18n.js (see docs/i18n.md): keys are "<module>.<key>", {name} placeholders,
plural forms {"one": ..., "other": ...} picked by n, and a missing key falls back to the other
language, then to the key itself (logged once).

The language is this Mac's Settings choice, ui_language: "zh" | "en" | "system". "system" follows
the OS language ($STUDIO_LANG overrides it: tests, demo screenshots). API errors and messages use it
(t); text written into the vault uses the vault language from studio.toml instead (tv), and the
assistant's answer language is its own setting.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import PROFILE, STUDIO_DIR, load_settings

LANGS = ("zh", "en")
APP_LOCALES = Path(__file__).resolve().parent / "locales"
WEB_LOCALES = STUDIO_DIR / "web" / "locales"
LANGUAGE_NAMES = {"zh": "中文", "en": "English"}  # what the assistant is told to answer in

log = logging.getLogger(__name__)
_warned: set[str] = set()
_files: dict[Path, tuple[int, dict[str, Any]]] = {}


def _norm(code: str) -> str | None:
    code = code.strip().strip('"').lower()
    if not code or code in ("c", "posix"):
        return None
    return "zh" if code.startswith("zh") else "en"


@lru_cache(maxsize=1)
def system_lang() -> str:
    """The OS language: macOS's first preferred language, else $LANG and friends; "en" if unknown."""
    if env := os.environ.get("STUDIO_LANG"):
        return _norm(env) or "en"
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                                 capture_output=True, text=True, timeout=3).stdout
            if m := re.search(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*", out):
                return _norm(m.group(0)) or "en"
        except (OSError, subprocess.TimeoutExpired):
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        if (v := os.environ.get(var)) and (n := _norm(v.split(":")[0])):
            return n
    return "en"


def ui_lang() -> str:
    s = load_settings().get("ui_language", "system")
    return s if s in LANGS else system_lang()


def vault_lang() -> str:
    """The language of the Markdown the app writes into the vault (Reading List.md, generated views).

    [profile] vault_language in studio.toml: "zh" (default) | "en" | "ui". The vault is shared by both
    Macs, so it is one fixed language unless set to "ui" (each Mac's interface language: two Macs set
    differently then keep rewriting the same files).
    """
    v = str(PROFILE.get("vault_language") or "zh").lower()
    if v == "ui":
        return ui_lang()
    return v if v in LANGS else "zh"


def answer_language() -> str:
    """The language the assistant answers in, as written into its prompts ("中文", "English", ...).

    Settings answer_language: "" = the profile's answer_language in studio.toml (default 中文),
    "ui" = the interface language, "zh" / "en" = that language.
    """
    s = load_settings().get("answer_language", "")
    if s == "ui":
        s = ui_lang()
    if s in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[s]
    return PROFILE.get("answer_language") or LANGUAGE_NAMES["zh"]


def answers_in_chinese() -> bool:
    return answer_language().lower() in ("中文", "chinese", "简体中文", "zh", "zh-cn")


# ---------- message files ----------

def _read(path: Path) -> dict[str, Any]:
    mtime = path.stat().st_mtime_ns
    hit = _files.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    _files[path] = (mtime, data)
    return data


def load_tree(root: Path) -> dict[str, dict[str, Any]]:
    """{lang: {"<module>.<key>": message}} for every <root>/<lang>/<module>.json (re-read when edited)."""
    out: dict[str, dict[str, Any]] = {}
    for lang in LANGS:
        table: dict[str, Any] = {}
        for f in sorted((root / lang).glob("*.json")):
            for k, v in _read(f).items():
                table[f"{f.stem}.{k}"] = v
        out[lang] = table
    return out


def web_messages() -> dict[str, dict[str, Any]]:
    return load_tree(WEB_LOCALES)


# ---------- lookup ----------

_PH = re.compile(r"\{(\w+)\}")


def _plural(lang: str, n: Any) -> str:
    if lang == "en" and n == 1:
        return "one"
    return "other"


def tl(lang: str, key: str, /, **params: Any) -> str:
    """t() in a given language."""
    tables = load_tree(APP_LOCALES)
    other = "en" if lang == "zh" else "zh"
    v = tables.get(lang, {}).get(key)
    if v is None:
        v = tables.get(other, {}).get(key)
        msg = f"missing key {key!r}" if v is None else f"{key!r} is missing in {lang}, using {other}"
        if msg not in _warned:
            _warned.add(msg)
            log.warning("i18n: %s", msg)
        if v is None:
            return key
    if isinstance(v, dict):
        n = params.get("n")
        v = v.get("zero") if n == 0 and v.get("zero") is not None else v.get(_plural(lang, n), v.get("other", ""))
    return _PH.sub(lambda m: str(params[m.group(1)]) if params.get(m.group(1)) is not None else m.group(0), v)


def t(key: str, /, **params: Any) -> str:
    """The message for key in this Mac's interface language."""
    return tl(ui_lang(), key, **params)


def tv(key: str, /, **params: Any) -> str:
    """The message for key in the vault language: for text written into vault files."""
    return tl(vault_lang(), key, **params)
