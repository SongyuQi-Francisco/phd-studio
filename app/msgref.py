"""Messages that are kept, then worded when they are shown (#52).

Server text is normally looked up by `i18n.t()` at the moment a response is built. Text that is *kept*
first (in a state file two Macs share, a vault YAML, a cache or an in-memory job that the page re-reads)
would keep the language it was written in. For those, store `ref(key, **params)` instead and word it
with `show()` when it is served; the page then follows the interface language without a schema change.

A reference is a plain string, `i18n:<key>` or `i18n:<key>|<params as JSON>`, so it fits any text field.
`show()` hands every other string back unchanged: text written before (plain Chinese) or by hand.
String parameters may themselves be references (a reason that wraps another message).

A reference never goes into a vault file as it is: Obsidian would show `i18n:...|{json}`, and a Mac without
this version would too. `persist()` turns it into structured fields for the YAML,
`{code: <key>, params: {...}, text: <the words in the vault language>}`: the text is what Obsidian shows and what
an older version prints, the code lets the page word it in the interface language. `show()` reads both forms.
"""
from __future__ import annotations

import json
from typing import Any

PREFIX = "i18n:"


def ref(key: str, /, **params: Any) -> str:
    """A message to keep: `show()` words it later. Parameters must be JSON values."""
    return PREFIX + key + ("|" + json.dumps(params, ensure_ascii=False, sort_keys=True) if params else "")


def _parse(msg: str) -> tuple[str, dict[str, Any]] | None:
    key, _, raw = msg[len(PREFIX):].partition("|")
    try:
        params = json.loads(raw) if raw else {}
    except ValueError:
        return None
    return (key, params) if isinstance(params, dict) else None


def persist(value: Any) -> Any:
    """`value` as it is written into a vault file: every reference in it (a string, or inside lists and dicts)
    becomes `{code, params, text}`, the text in the vault language. Anything else is kept as it is."""
    if isinstance(value, str) and value.startswith(PREFIX):
        parsed = _parse(value)
        if parsed is None:
            return value
        from . import i18n

        key, params = parsed
        return {"code": key, **({"params": {k: persist(v) for k, v in params.items()}} if params else {}),
                "text": show(value, i18n.vault_lang())}
    if isinstance(value, dict):
        return value if _stored(value) else {k: persist(v) for k, v in value.items()}
    if isinstance(value, list):
        return [persist(v) for v in value]
    return value


def _stored(msg: Any) -> bool:
    return isinstance(msg, dict) and isinstance(msg.get("code"), str) and isinstance(msg.get("text"), str)


def show(msg: Any, lang: str | None = None) -> Any:
    """`msg` worded for the reader: this Mac's interface language, or `lang` ("zh" | "en"; the vault language
    for text written into vault files). Anything that is not a reference (or its stored form) is returned as it is."""
    from . import i18n  # late: this module is imported by code that must stay light (see scholar_rank)

    if _stored(msg):  # as written by persist(): the code if this version knows it, else the words that were written
        key, params = msg["code"], msg.get("params") or {}
        if key not in i18n.load_tree(i18n.APP_LOCALES).get("zh", {}):
            return msg["text"]
    elif isinstance(msg, str) and msg.startswith(PREFIX):
        parsed = _parse(msg)
        if parsed is None:
            return msg
        key, params = parsed
    else:
        return msg
    params = {k: show(v, lang) if isinstance(v, (str, dict)) else v for k, v in params.items()}
    # every key that is kept as a reference (the tests check that they exist)
    # i18n-keys: scholars.oa.{connect,quota,http,bad_data} scholars.job.{logos,inst_error,scholar_error} scholars.capture.{timeout,unknown_page,page,profile} scholars.refresh.{manual_exclude,src_import,src_app_window,src_app_read,src_read,src_snapshot,src_openalex,scholar_failed,oa_unavailable,failed,no_author,ambiguous} scholars.rank.{mega_team,off_field,no_works,inactive,excluded,deceased,noise_note,same_lab,unchecked,few_papers,note_excluded,note_listed,note_pinned} track.src.{connect,rate_limited,not_found,http,not_json,captcha_title,captcha_search,acl_not_paper,acl_download_failed,query_failed} track.run.{stopped,step_failed,no_raw,no_stats}
    return i18n.tl(lang, key, **params) if lang else i18n.t(key, **params)
