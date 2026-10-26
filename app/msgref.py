"""Messages that are kept, then worded when they are shown (#52).

Server text is normally looked up by `i18n.t()` at the moment a response is built. Text that is *kept*
first (in a state file two Macs share, a vault YAML, a cache or an in-memory job that the page re-reads)
would keep the language it was written in. For those, store `ref(key, **params)` instead and word it
with `show()` when it is served; the page then follows the interface language without a schema change.

A reference is a plain string, `i18n:<key>` or `i18n:<key>|<params as JSON>`, so it fits any text field.
`show()` hands every other string back unchanged: text written before (plain Chinese) or by hand.
String parameters may themselves be references (a reason that wraps another message).
"""
from __future__ import annotations

import json
from typing import Any

PREFIX = "i18n:"


def ref(key: str, /, **params: Any) -> str:
    """A message to keep: `show()` words it later. Parameters must be JSON values."""
    return PREFIX + key + ("|" + json.dumps(params, ensure_ascii=False, sort_keys=True) if params else "")


def show(msg: Any, lang: str | None = None) -> Any:
    """`msg` worded for the reader: this Mac's interface language, or `lang` ("zh" | "en"; the vault language
    for text written into vault files). Anything that is not a reference is returned as it is."""
    if not isinstance(msg, str) or not msg.startswith(PREFIX):
        return msg
    key, _, raw = msg[len(PREFIX):].partition("|")
    try:
        params = json.loads(raw) if raw else {}
        if not isinstance(params, dict):
            return msg
    except ValueError:
        return msg
    from . import i18n  # late: this module is imported by code that must stay light (see scholar_rank)

    params = {k: show(v, lang) if isinstance(v, str) else v for k, v in params.items()}
    # every key that is kept as a reference (the tests check that they exist)
    # i18n-keys: scholars.oa.{connect,quota,http,bad_data} scholars.job.{logos,inst_error,scholar_error} scholars.capture.{timeout,unknown_page,page,profile} scholars.refresh.{manual_exclude,src_import,src_app_window,src_app_read,src_read,src_snapshot,src_openalex,scholar_failed,oa_unavailable,failed,no_author,ambiguous} scholars.rank.{mega_team,off_field,no_works,inactive,excluded,deceased,noise_note,same_lab,unchecked,few_papers,note_excluded,note_listed,note_pinned} track.src.{connect,rate_limited,not_found,http,not_json,captcha_title,captcha_search,acl_not_paper,acl_download_failed,query_failed} track.run.{stopped,step_failed,no_raw,no_stats}
    return i18n.tl(lang, key, **params) if lang else i18n.t(key, **params)
