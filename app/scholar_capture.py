"""Read Google Scholar's label rankings in a second window of the desktop app.

Scholar's author search needs a signed-in Google account, so the server cannot fetch it. The
desktop app (pywebview, private_mode=False) keeps cookies, so the user signs in once in this
window. Pages are opened one at a time, several seconds apart, and read with evaluate_js.
A sign-in page or a CAPTCHA is never automated: a manual run shows the window and waits for the
user to finish; an automatic run stops and reports that the user is needed.

Only available inside the desktop window (pywebview running); elsewhere `available()` is False
and the caller falls back to a snapshot or OpenAlex. STUDIO_SCHOLAR_BASE points the window at
a local copy of the pages for testing.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Any, Callable
from urllib.parse import quote

BASE = os.environ.get("STUDIO_SCHOLAR_BASE", "https://scholar.google.com").rstrip("/")
PACE = (5.0, 9.0)  # seconds between page loads
USER_WAIT = 15 * 60  # how long a manual run waits for the user to sign in / solve a CAPTCHA
_lock = threading.Lock()
_win: dict[str, Any] = {"w": None}


class CaptureError(RuntimeError):
    pass


class NeedsUser(RuntimeError):
    def __init__(self, state: str):
        super().__init__(state)
        self.state = state  # "login" | "captcha"


def available() -> bool:
    try:
        import webview
    except ImportError:
        return False
    return any(w is not _win["w"] for w in webview.windows)


# Reads the current page: its state, author rows (search pages) or the profile (profile pages),
# and the next page's URL. Runs inside the Scholar window.
READ_JS = r"""
(() => {
  const txt = document.body ? document.body.innerText : "";
  if (/(^|\.)accounts\.google\./.test(location.host) || /ServiceLogin|\/signin/.test(location.href))
    return JSON.stringify({state: "login", url: location.href});
  if (document.querySelector('#gs_captcha_ccl, #captcha-form, iframe[src*="recaptcha"], form[action*="sorry"]')
      || /unusual traffic|not a robot|异常流量|人机身份验证/i.test(txt))
    return JSON.stringify({state: "captcha", url: location.href});
  const num = (s) => parseInt(String(s || "").replace(/[^0-9]/g, ""), 10) || 0;
  const uid = (a) => { const m = /[?&]user=([\w-]{12})/.exec(a ? a.getAttribute("href") || "" : ""); return m ? m[1] : null; };
  const rows = [...document.querySelectorAll(".gs_ai_chpr, .gsc_1usr .gs_ai")].map((el) => {
    const a = el.querySelector(".gs_ai_name a");
    return { name: a ? a.textContent.trim() : "", scholar_id: uid(a),
             affiliation: (el.querySelector(".gs_ai_aff") || {}).textContent || "",
             citations: num((el.querySelector(".gs_ai_cby") || {}).textContent),
             interests: [...el.querySelectorAll(".gs_ai_int a, .gs_ai_one_int")].map((x) => x.textContent.trim()) };
  }).filter((r) => r.name && r.scholar_id);
  let next = null;
  const btn = document.querySelector("button.gsc_pgn_pnx, button[aria-label='Next']");
  if (btn && !btn.disabled) {
    const m = /window\.location='([^']+)'/.exec(btn.getAttribute("onclick") || "");
    if (m) next = new URL(m[1].replace(/\\x3d/g, "=").replace(/\\x26/g, "&"), location.href).href;
  }
  let profile = null;
  const pn = document.querySelector("#gsc_prf_in");
  if (pn) {
    const m = /[?&]user=([\w-]{12})/.exec(location.href);
    profile = { name: pn.textContent.trim(), scholar_id: m ? m[1] : null,
                affiliation: (document.querySelector(".gsc_prf_il") || {}).textContent || "",
                citations: num((document.querySelector("#gsc_rsb_st td.gsc_rsb_std") || {}).textContent) };
  }
  const ok = rows.length || profile || document.querySelector(".gsc_1usr, #gsc_prf_in, #gsc_sa_ccl, .gs_med");
  return JSON.stringify({state: ok ? "ok" : "unknown", url: location.href, rows, next, profile});
})()
"""


def _window() -> Any:
    import webview

    with _lock:
        w = _win["w"]
        if w is None or w not in webview.windows:
            w = webview.create_window("Google Scholar · PhD Studio", BASE, width=1100, height=820, hidden=True)
            _win["w"] = w
            w.events.loaded.wait(40)
        return w


def _load(w: Any, url: str) -> None:
    w.events.loaded.clear()
    w.load_url(url)
    if not w.events.loaded.wait(40):
        raise CaptureError(f"页面加载超时：{url}")
    time.sleep(1.0)


def _read(w: Any) -> dict[str, Any]:
    raw = w.evaluate_js(READ_JS)
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return {"state": "unknown"}


def _page(w: Any, url: str, show_on_block: bool, progress: Callable[..., None]) -> dict[str, Any]:
    time.sleep(random.uniform(*PACE))
    _load(w, url)
    r = _read(w)
    if r.get("state") in ("login", "captcha"):
        if not show_on_block:
            raise NeedsUser(r["state"])
        progress(needs_user=r["state"])
        w.show()  # the user signs in / solves it themselves; nothing here touches the page
        deadline = time.time() + USER_WAIT
        while r.get("state") in ("login", "captcha"):
            if time.time() > deadline:
                raise NeedsUser(r["state"])
            time.sleep(3)
            r = _read(w)
        progress(needs_user=None)
        _load(w, url)  # back to the page we wanted
        r = _read(w)
    if r.get("state") != "ok":
        raise CaptureError("页面结构不认识（Scholar 可能改版了）")
    return r


def capture(labels: list[str], pages: int, profiles: list[str], *, show_on_block: bool,
            progress: Callable[..., None] = lambda **kw: None) -> list[dict[str, Any]]:
    """Every label search (up to `pages` pages of 10) plus the given profile ids, one row per
    person with its labels. Raises NeedsUser / CaptureError."""
    w = _window()
    rows: dict[str, dict[str, Any]] = {}
    total = len(labels) * pages + len(profiles)
    done = 0
    try:
        for q in labels:
            url: str | None = f"{BASE}/citations?view_op=search_authors&hl=en&mauthors={quote(q)}"
            for n in range(pages):
                if not url:
                    break
                progress(current=f"{q}（第 {n + 1} 页）", done=done, total=total)
                r = _page(w, url, show_on_block, progress)
                for i, row in enumerate(r.get("rows") or []):
                    e = rows.setdefault(row["scholar_id"], {**row, "labels": [], "via": "label"})
                    e["citations"] = max(e.get("citations") or 0, row.get("citations") or 0)
                    e["labels"] = sorted(set(e["labels"]) | {q})
                    e.setdefault("scholar_rank", n * 10 + i + 1)
                url = r.get("next")
                done += 1
        for sid in profiles:
            if sid in rows:
                continue
            progress(current=f"个人主页 {sid}", done=done, total=total)
            r = _page(w, f"{BASE}/citations?user={quote(sid)}&hl=en", show_on_block, progress)
            if r.get("profile"):
                rows[sid] = {**r["profile"], "labels": [], "via": "profile"}
            done += 1
    finally:
        try:
            w.hide()
        except Exception:  # the window may already be gone
            pass
    return sorted(rows.values(), key=lambda e: -(e.get("citations") or 0))


def show() -> bool:
    """Bring the Scholar window up so the user can sign in ahead of a run."""
    if not available():
        return False
    w = _window()
    w.show()
    return True
