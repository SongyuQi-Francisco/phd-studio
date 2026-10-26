# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.47", "imageio-ffmpeg>=0.5"]
# ///
"""Screenshots and short clips of the demo workspace, for the README and docs.

    uv run scripts/demo/make_demo.py --fresh                  # build the fictional demo first
    STUDIO_CONFIG=<demo>/studio.toml STUDIO_DEVICE=demo uv run uvicorn app.main:app --port 8790 &
    uv run scripts/demo/screenshots.py --url http://127.0.0.1:8790 --out <dir outside the repo>

Uses the system Chrome through Playwright (channel="chrome"), so no browser is downloaded. Every page at
1440×900 and 2x, light and dark, close-ups of single features, and a few MP4/GIF clips (CDP screencast
frames put together with the ffmpeg that imageio-ffmpeg ships). The browser runs in Asia/Shanghai time
with a zh-CN locale, whatever this Mac is set to. The reader opens real papers on page 3, not on the
author list.

--only live runs the steps that ask a model (explain a passage, crop-to-ask, Claim Gate audit, the ⌘J
assistant). They need Claude logged in for the demo (make_demo.py --secrets) and take a few minutes;
their answers stay in the demo's chat logs, so later page shots show them.
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import Browser, BrowserContext, Locator, Page, sync_playwright

W, H = 1440, 900
PAPER = "Attention 2017"
DEFINITION = "An attention function can be described as mapping a query and a set of key-value pairs to an output"
SCALING = ("We suspect that for large values of dk, the dot products grow large in magnitude, pushing the softmax "
           "function into regions where it has extremely small gradients")
FIGURE1 = (0.29, 0.075, 0.71, 0.53)  # Figure 1 (the architecture) on page 3, as fractions of the page box


class Shots:
    def __init__(self, browser: Browser, url: str, out: Path):
        self.browser, self.url, self.out = browser, url.rstrip("/"), out
        self.index: list[tuple[str, str]] = []

    # ---------- plumbing ----------

    @contextmanager
    def context(self, scheme: str, assistant: bool = False, start: str | None = None) -> Iterator[Page]:
        ctx: BrowserContext = self.browser.new_context(
            viewport={"width": W, "height": H}, device_scale_factor=2, color_scheme=scheme,
            locale="zh-CN", timezone_id="Asia/Shanghai")
        prefs = {"assistant": assistant, "lastStem": None, "sideW": 440, "libFilter": "queue",
                 **{f"page:{s}": 3 for s in (PAPER, "FlashAttention 2022", "RAG 2020")}}  # page 1 lists the authors
        ctx.add_init_script("(() => { const p = %s; for (const [k, v] of Object.entries(p)) "
                            "localStorage.setItem('studio:' + k, JSON.stringify(v)); })()" % json.dumps(prefs))
        page = ctx.new_page()
        page.goto(self.url)
        page.wait_for_selector(".rail")
        self.settle(page, 1.4)
        if start:
            self.go(page, start)
        try:
            yield page
        finally:
            ctx.close()

    @staticmethod
    def settle(page: Page, seconds: float = 0.8) -> None:
        page.wait_for_timeout(int(seconds * 1000))

    def shot(self, page: Page, name: str, what: str, target: Locator | None = None, pad: int = 16,
             clip: dict | None = None) -> None:
        path = self.out / f"{name}.png"
        if target is not None:
            target.first.scroll_into_view_if_needed()
            self.settle(page, 0.5)
            box = target.first.bounding_box()
            if box is None:
                print(f"  ! {name}: not visible")
                return
            x, y = max(0, box["x"] - pad), max(0, box["y"] - pad)
            clip = {"x": x, "y": y, "width": min(W - x, box["width"] + 2 * pad), "height": min(H - y, box["height"] + 2 * pad)}
        page.screenshot(path=str(path), clip=clip, animations="disabled")
        self.index.append((path.name, what))
        print(f"  {path.name}")

    def go(self, page: Page, view: str) -> None:
        page.locator(f'.rail-btn[data-view="{view}"]').click()  # not the title: it follows the interface language
        self.settle(page, 1.5)

    def open_paper(self, page: Page, query: str = "Attention", keycap: bool = False) -> None:
        page.keyboard.press("Meta+k")
        if keycap:
            self.keycap(page, "⌘K")
        self.settle(page, 0.6)
        page.keyboard.type(query, delay=90 if keycap else 30)
        self.settle(page, 0.7 if keycap else 0.3)
        page.keyboard.press("Enter")
        page.wait_for_selector(".pdfViewer .page .textLayer span", timeout=30000)
        self.settle(page, 1.6)

    def tab(self, page: Page, label: str) -> None:
        page.locator(".seg button", has_text=label).first.click()
        self.settle(page, 1.3)

    def side_tab(self, page: Page, title: str) -> None:
        page.locator(f'.rail-btn[title="{title}"]').click()
        self.settle(page, 1.0)

    def select_text(self, page: Page, needle: str) -> None:
        """Select the text-layer characters spelling `needle` (whitespace ignored), then let the reader see a mouseup."""
        page.evaluate("""(needle) => {
          const want = needle.replace(/\\s+/g, '');
          const chars = [];
          for (const s of document.querySelectorAll('.textLayer span')) {
            const node = s.firstChild;
            if (!node || node.nodeType !== 3) continue;
            for (let i = 0; i < node.data.length; i++) if (!/\\s/.test(node.data[i])) chars.push([node, i, node.data[i]]);
          }
          const at = chars.map((c) => c[2]).join('').indexOf(want);
          if (at < 0) throw new Error('text not found: ' + needle);
          const [n0, i0] = chars[at], [n1, i1] = chars[at + want.length - 1];
          n0.parentElement.scrollIntoView({block: 'center'});
          const r = document.createRange();
          r.setStart(n0, i0); r.setEnd(n1, i1 + 1);
          const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
        }""", needle)
        self.settle(page, 0.5)
        page.evaluate("document.getElementById('viewerContainer').dispatchEvent(new MouseEvent('mouseup', {bubbles: true}))")
        self.settle(page, 0.7)

    def goto_page(self, page: Page, n: int) -> None:
        box = page.locator(".pageinput input")
        box.fill(str(n))
        box.press("Enter")
        self.settle(page, 1.3)

    def crop(self, page: Page, n: int, frac: tuple[float, float, float, float], mid=None) -> None:
        """Turn on the crop tool and drag a rectangle over part of page n; `mid()` runs before the mouse is let go."""
        self.goto_page(page, n)
        page.locator('.tb-btn[title^="框选图表"]').click()
        self.settle(page, 0.4)
        box = page.locator(f'.page[data-page-number="{n}"]').bounding_box()
        x0, y0 = box["x"] + box["width"] * frac[0], box["y"] + box["height"] * frac[1]
        x1, y1 = box["x"] + box["width"] * frac[2], box["y"] + box["height"] * frac[3]
        page.mouse.move(x0, y0)
        page.mouse.down()
        for k in range(1, 13):
            page.mouse.move(x0 + (x1 - x0) * k / 12, y0 + (y1 - y0) * k / 12)
            page.wait_for_timeout(40)
        if mid:
            mid()
        page.mouse.up()
        self.settle(page, 1.0)

    def close_modals(self, page: Page) -> None:
        while page.locator(".modal-bg").count():
            page.locator(".modal-bg").last.click(position={"x": 6, "y": 6})
            self.settle(page, 0.5)

    def keycap(self, page: Page, label: str, ms: int = 1200) -> None:
        """A key-press badge at the bottom of the window, for the clips."""
        page.evaluate("""([label, ms]) => {
          const el = document.createElement('div');
          el.textContent = label;
          el.style.cssText = 'position:fixed;left:50%;bottom:40px;transform:translateX(-50%);z-index:99999;padding:10px 20px;'
            + 'border-radius:12px;font:600 22px -apple-system,"SF Pro Text",sans-serif;letter-spacing:.04em;color:#fff;'
            + 'background:rgba(10,14,20,.84);box-shadow:0 8px 30px rgba(0,0,0,.35);border:1px solid rgba(57,135,229,.6)';
          document.body.appendChild(el);
          setTimeout(() => el.remove(), ms);
        }""", [label, ms])

    def wait_answer(self, page: Page, timeout: float = 300, approve: bool | None = None) -> bool:
        """Wait until the visible chat stops streaming; True if it stopped at an edit waiting for approval."""
        page.wait_for_timeout(2000)
        end = time.time() + timeout
        while time.time() < end:
            if page.locator(".side .perm .perm-foot").count():
                return True
            if not page.locator(".side .msg-ai .typing").count():
                break
            page.wait_for_timeout(1000)
        self.settle(page, 1.2)
        return False

    def scroll_chat(self, page: Page, to: str = "bottom") -> None:
        page.evaluate("""(to) => { for (const el of document.querySelectorAll('aside.side .chat'))
          el.scrollTop = to === 'top' ? 0 : el.scrollHeight; }""", to)
        self.settle(page, 0.5)

    # ---------- every page ----------

    def pages(self, scheme: str) -> None:
        sfx = f"-{scheme}"
        with self.context(scheme) as page:
            self.shot(page, f"page-overview{sfx}", "Overview (总览): countdown rings for the next targets, milestones and deadlines; "
                      "reading, ideas and tracking KPIs; 120-day timeline")
            self.go(page, "list")
            self.shot(page, f"page-reading-list{sfx}", "Reading list: inbox → T1/T2/T3 → reading → done, caps, status donut and weekly in/out chart")
            self.go(page, "timeline")
            self.shot(page, f"page-timeline-watched{sfx}", "Timeline · watched venues: CORE / CCF ranks, next deadline countdowns")
            self.tab(page, "我的规划")
            self.shot(page, f"page-timeline-plan{sfx}", "Timeline · my plan: targets bound to editions (planning → writing → submitted), milestones and gates")
            self.tab(page, "全部会议")
            self.shot(page, f"page-venue-library{sfx}", "Venue library: 1048 CORE + CCF venues, filter by rank")
            self.go(page, "ideas")
            self.shot(page, f"page-ideas{sfx}", "Ideas: tags, research lines, source paper and page, merged ideas")
            self.go(page, "direction")
            self.shot(page, f"page-direction{sfx}", "Direction: PhD roadmap with a you-are-here marker, research-line cards with kill-criteria lights")
            self.go(page, "track")
            for label, key, what in [("学者动态", "scholars", "top scholars of the field: metrics, sparklines, lab and relevance chips"),
                                     ("本周雷达", "radar", "weekly lab radar: papers kept per run by tier, topics, analysis, paper cards"),
                                     ("关注", "follows", "followed keywords and people across arXiv, OpenReview and ACL Anthology"),
                                     ("实验室", "labs", "tracked labs: activity heatmap across weekly runs")]:
                self.tab(page, label)
                self.settle(page, 1.0)
                self.shot(page, f"page-track-{key}{sfx}", f"Tracking · {label}: {what} (fictional people, labs and papers)")
            self.open_paper(page)
            self.shot(page, f"page-reader{sfx}", f"Reader ({PAPER}, page 3) with the Claude co-reader panel")
            self.side_tab(page, "笔记")
            self.shot(page, f"page-reader-note{sfx}", "Reader with the paper's Obsidian note: [p.N] and §N links jump in the PDF")
            page.locator('.rail-btn[title="设置"]').click()
            self.settle(page, 1.3)
            self.shot(page, f"page-settings{sfx}", "Settings: models and providers, Zotero, translation engine, bilingual prefetch, reminders")
            self.close_modals(page)
        with self.context(scheme, assistant=True) as page:
            self.shot(page, f"page-overview-assistant{sfx}", "Overview with the ⌘J workbench assistant panel")
            page.locator("aside.side .mpick-btn").click()
            self.settle(page, 0.9)
            menu = page.locator(".mpick-menu")
            box, side = menu.bounding_box(), page.locator("aside.side").bounding_box()
            x = min(box["x"], side["x"]) - 16
            self.shot(page, f"closeup-model-picker{sfx}", "Model picker: Claude, Codex, Gemini, 智谱 GLM, DeepSeek, each with its login state "
                      "and the fix when unavailable; models never switch silently",
                      clip={"x": x, "y": 0, "width": W - x, "height": min(H, box["y"] + box["height"] + 24)})
            page.keyboard.press("Escape")
            self.settle(page, 0.4)
            page.keyboard.press("Meta+k")
            self.settle(page, 0.9)
            self.shot(page, f"page-library-drawer{sfx}", "⌘K paper library drawer: queue order, recent, local PDFs")

    # ---------- close-ups ----------

    def closeups(self, scheme: str) -> None:
        sfx = f"-{scheme}"
        with self.context(scheme) as page:
            self.shot(page, f"closeup-overview-rings{sfx}", "Overview: countdown rings (target deadlines, submitted target, next gate, next venue deadline)",
                      page.locator(".countdowns"))
            self.shot(page, f"closeup-overview-kpis{sfx}", "Overview: KPI tiles (read this week, T1 cap, inbox, new ideas, tracking run)",
                      page.locator(".kpis"))
            self.go(page, "list")
            self.shot(page, f"closeup-reading-charts{sfx}", "Reading list: status donut and weekly in/out bars",
                      page.locator(".card", has_text="每周进出"))
            self.shot(page, f"closeup-reading-kpis{sfx}", "Reading list: caps (T1 5 a week, 12 queued) as progress bars",
                      page.locator(".kpis"))
            self.go(page, "track")
            self.tab(page, "学者动态")
            self.settle(page, 1.0)
            self.shot(page, f"closeup-track-scholar-cards{sfx}", "Tracking: fictional scholar cards with initials avatars",
                      page.locator(".tk-people"))
            self.shot(page, f"closeup-track-heatmap{sfx}", "Tracking: scholar × month heatmap, topic bars, monthly output",
                      page.locator(".tk-grid2"))
            self.tab(page, "本周雷达")
            self.shot(page, f"closeup-track-radar{sfx}", "Tracking: papers kept per weekly run, stacked by tier",
                      page.locator(".tk-grid2"))
            self.tab(page, "实验室")
            self.shot(page, f"closeup-track-labs{sfx}", "Tracking: lab × weekly-run heatmap", page.locator(".tk-grid2"))
            self.go(page, "direction")
            self.shot(page, f"closeup-direction-roadmap{sfx}", "Direction: PhD roadmap, year cards, you-are-here marker",
                      page.locator(".card.roadmap"))
            self.shot(page, f"closeup-direction-line{sfx}", "Direction: a research-line card with countdowns and kill-criteria lights",
                      page.locator("section.card.line").first)
            self.go(page, "ideas")
            merged = page.locator(".idea", has=page.locator(".from-head")).first
            merged.locator(".from-head").click()
            self.settle(page, 0.8)
            self.shot(page, f"closeup-idea-merged{sfx}", "Ideas: a merged idea with its sources (provenance kept, restorable) and tags",
                      merged)
            page.locator(".tagbar .chip", has_text="#verifier").click()
            self.settle(page, 1.0)
            self.shot(page, f"page-ideas-tag-filter{sfx}", "Ideas filtered by the #verifier tag")
            self.shot(page, f"closeup-idea-tags{sfx}", "Ideas: status / line filters and tag facets with counts",
                      page.locator(".filters"), pad=12)
            page.locator(".tagbar .abtn", has_text="清除").click()
            page.locator(".filters .chip", has_text="合并…").click()
            self.settle(page, 0.6)
            for box in page.locator(".idea .pickbox:not([disabled])").all()[:2]:
                box.click()
                self.settle(page, 0.3)
            page.locator(".merge-bar .btn.primary").click()
            self.settle(page, 0.9)
            self.shot(page, f"closeup-idea-merge-dialog{sfx}", "Ideas: merging two ideas into one (tags unioned, sources kept)",
                      page.locator(".modal").last)
            page.keyboard.press("Escape")
            self.settle(page, 0.4)
            page.locator(".merge-bar .btn.ghost").click()
            self.settle(page, 0.4)
            page.locator('.rail-btn[title="设置"]').click()
            self.settle(page, 1.2)
            self.shot(page, f"closeup-settings{sfx}", "Settings dialog", page.locator(".modal.settings"))
            page.locator(".rm-row").click()
            self.settle(page, 1.2)
            self.shot(page, f"closeup-settings-reminders{sfx}", "Settings → deadline reminders: lead times, quiet hours, the next 30 days",
                      page.locator(".modal.rm-sheet"))
            self.close_modals(page)

            # reader: selection popover + translation, explain, crop, note links, model picker
            self.open_paper(page)
            self.select_text(page, DEFINITION)
            pop = page.locator(".reader .pop").first
            self.shot(page, f"closeup-reader-selection{sfx}", "Reader: select text → 翻译 / 讲解 / 问 Claude / 摘录", pop, pad=60)
            pop.locator("button", has_text="翻译").click()
            page.wait_for_selector(".reader .pop .trans .meta", timeout=30000)
            self.settle(page, 0.6)
            self.shot(page, f"closeup-reader-translate{sfx}", "Reader: selection translated in place (the engine used is shown)",
                      page.locator(".reader .pop").first, pad=60)
            self.shot(page, f"reader-translate{sfx}", "Reader: full window with a translated selection")
            page.keyboard.press("Escape")
            self.select_text(page, DEFINITION)
            page.locator(".reader .pop button", has_text="摘录").click()
            self.settle(page, 0.8)
            self.shot(page, f"closeup-reader-excerpt{sfx}", "Reader: 摘录 — the exact line (quote + [p.N]) is shown before it goes into the note",
                      page.locator(".reader .pop").first, pad=60)
            page.keyboard.press("Escape")
            self.crop(page, 3, FIGURE1, mid=lambda: self.shot(
                page, f"closeup-reader-cropping{sfx}", "Reader: dragging a crop rectangle over Figure 1", page.locator(".reader"), pad=0))
            self.shot(page, f"closeup-reader-crop{sfx}", "Reader: crop a figure (⌥-drag or the crop tool) → 讲解 / 问 Claude with the image",
                      page.locator(".reader"), pad=0)
            page.keyboard.press("Escape")
            self.side_tab(page, "Claude 伴读")
            self.scroll_chat(page, "top")
            self.shot(page, f"closeup-chat-panel{sfx}", "Claude co-reader panel: answers carry clickable page links and the model that answered",
                      page.locator("aside.side"), pad=0)
            self.side_tab(page, "笔记")
            self.shot(page, f"closeup-note-links{sfx}", "Note panel: page and section references are links", page.locator("aside.side"), pad=0)
            link = page.locator("aside.side .pagelink[data-page='6']:visible").first  # the hidden chat panel has links too
            link.scroll_into_view_if_needed()
            link.dispatch_event("click")  # the note handles clicks on its links by delegation
            self.settle(page, 1.5)
            self.shot(page, f"reader-note-jump{sfx}", "Clicking [p.6] in the note jumped the PDF to page 6")

    # ---------- steps that ask a model ----------

    def live(self, scheme: str) -> None:
        sfx = f"-{scheme}"
        with self.context(scheme) as page:
            self.open_paper(page)
            self.side_tab(page, "Claude 伴读")
            # 1. explain a passage
            self.select_text(page, SCALING)
            page.locator(".reader .pop button", has_text="讲解").click()
            self.wait_answer(page)
            self.shot(page, f"live-explain{sfx}", "Co-reader: 讲解 on a selected passage (real Claude answer on the public paper)")
            # 2. crop Figure 1 and ask about it
            self.crop(page, 3, FIGURE1)
            page.locator(".reader .pop button", has_text="问 Claude").click()
            self.settle(page, 0.6)
            page.keyboard.type("这张图里 encoder 和 decoder 各有几种子层？decoder 的第一个子层为什么要 mask？", delay=10)
            self.settle(page, 0.4)
            self.shot(page, f"live-crop-attached{sfx}", "Crop-to-ask: the cropped figure is attached to the question")
            page.keyboard.press("Enter")
            self.wait_answer(page)
            self.shot(page, f"live-crop-answer{sfx}", "Crop-to-ask: Claude answers about the figure (real answer)")
            # 3. Claim Gate audit: stop at the first diff and show it
            page.locator("aside.side button", has_text="Claim Gate 审计").click()
            waiting = self.wait_answer(page, timeout=600)
            if waiting:
                page.locator("aside.side .perm").last.scroll_into_view_if_needed()
                self.settle(page, 0.6)
                self.shot(page, f"live-claim-gate-diff{sfx}", "Claim Gate audit: the proposed note edit is shown as a diff and waits for approval")
                self.shot(page, f"closeup-claim-gate-diff{sfx}", "Claim Gate audit: diff approval card (允许写入 / 不要改)",
                          page.locator("aside.side .perm").last, pad=10)
                for _ in range(6):  # approve this and any follow-up edit of the audit
                    page.locator("aside.side .perm .perm-foot .btn.primary").last.click()
                    if not self.wait_answer(page, timeout=300):
                        break
            self.shot(page, f"live-claim-gate-done{sfx}", "Claim Gate audit: finished (audit table, edits written after approval)")
        with self.context(scheme, assistant=True) as page:
            page.locator("aside.side button", has_text="今天该做什么").click()
            self.wait_answer(page)
            self.shot(page, f"live-assistant{sfx}", "⌘J workbench assistant: 今天该做什么 (real answer from the demo timeline, list and ideas)")

    # ---------- more full-window states ----------

    def extras(self, scheme: str) -> None:
        sfx = f"-{scheme}"
        with self.context(scheme) as page:
            page.locator(".countdowns > *").first.click()  # the next target's ring opens its venue
            page.wait_for_selector(".sheet .sheet-head")
            self.settle(page, 1.2)
            self.shot(page, f"venue-sheet{sfx}", "Venue detail sheet (ACL): ranks, editions and deadlines, tracks and pages from the official CFP")
            self.shot(page, f"closeup-venue-sheet{sfx}", "Venue detail sheet", page.locator(".sheet"), pad=0)
            page.locator(".sheet-bg").click(position={"x": 6, "y": 6})
            self.settle(page, 0.6)
            page.locator('.rail-btn[title="设置"]').click()
            self.settle(page, 1.0)
            page.locator(".rm-row").click()
            self.settle(page, 1.2)
            self.shot(page, f"settings-reminders{sfx}", "Settings → 截稿提醒: reminders in the next 30 days, lead times, quiet hours, sending Mac")
            self.close_modals(page)
            self.go(page, "track")
            self.tab(page, "关注")
            page.locator(".tk-capture input").first.fill("https://arxiv.org/abs/2401.18059")
            page.locator(".tk-capture input").nth(1).fill("树状索引，和 Atlas 的长文档检索对比")
            self.settle(page, 0.6)
            self.shot(page, f"track-capture{sfx}", "Tracking · 关注 with a link pasted into the capture box (not sent) and the arXiv / OpenReview / "
                      "ACL Anthology feed")
            self.shot(page, f"closeup-track-capture{sfx}", "Capture box with a pasted arXiv link and a reason", page.locator(".tk-capture-wrap"))

            self.open_paper(page)
            self.side_tab(page, "Claude 伴读")
            self.select_text(page, DEFINITION)
            self.shot(page, f"reader-selection{sfx}", "Reader with the co-reader panel and the selection popover (翻译 / 讲解 / 问 Claude / 摘录)")
            page.keyboard.press("Escape")
            table = page.locator("aside.side .msg-ai table").first
            if table.count():
                table.evaluate("el => el.scrollIntoView({block: 'start'})")
                self.settle(page, 0.8)
                self.shot(page, f"claim-gate-table{sfx}", "Claim Gate audit table in the co-reader chat (claim, verdict, evidence with page)")
                self.shot(page, f"closeup-claim-gate-table{sfx}", "Claim Gate audit table", page.locator("aside.side"), pad=0)
            page.keyboard.press("Meta+i")
            page.wait_for_selector(".modal textarea, .modal input")
            page.keyboard.type("Beacon 的全局 token 也许能当检索头用，查一下 Figure 1 的 decoder 怎么接 encoder 输出", delay=5)
            self.settle(page, 0.6)
            self.shot(page, f"idea-capture{sfx}", "⌘I quick idea capture while reading (linked to the paper and page)")
            self.shot(page, f"closeup-idea-capture{sfx}", "⌘I quick idea capture", page.locator(".modal").last)
            self.close_modals(page)
            self.bilingual(page, sfx)
        with self.context(scheme, assistant=True) as page:
            page.locator("aside.side .mpick-btn").click()
            self.settle(page, 0.9)
            self.shot(page, f"model-picker-open{sfx}", "Model picker open in the assistant header, next to answers that name the model that wrote them")

    def bilingual(self, page: Page, sfx: str) -> None:
        """The paper's whole-paper translation, side by side and Chinese only (if it has been made)."""
        st = page.evaluate(f"fetch('/api/bilingual/{PAPER}').then(r => r.json())")
        if st.get("state") != "done":
            print(f"  ! bilingual PDF not ready ({st.get('state')})")
            return
        for label, name, what in [("双语", "reader-bilingual", "Reader · 双语: the whole paper side by side, original and Chinese (pdf2zh-next)"),
                                  ("译文", "reader-mono", "Reader · 译文: the translated paper only")]:
            page.locator(".reader .toolbar .seg button", has_text=label).click()
            page.wait_for_selector(".pdfViewer .page canvas, .pdfViewer .page .canvasWrapper", timeout=30000)
            self.settle(page, 1.5)
            self.goto_page(page, 3)
            self.settle(page, 1.5)
            self.shot(page, f"{name}{sfx}", what)
        page.locator(".reader .toolbar .seg button", has_text="原文").click()
        self.settle(page, 1.0)

    def extras_live(self, scheme: str) -> None:
        """Clips and states that ask Claude; they add to the demo's chats and plan."""
        sfx = f"-{scheme}"
        with self.context(scheme) as page:
            self.open_paper(page)
            self.side_tab(page, "Claude 伴读")
            with self.recording(page, f"clip-explain{sfx}", "Select a passage → 讲解: Claude's answer streams in with page links"):
                self.settle(page, 0.6)
                self.select_text(page, SCALING)
                self.settle(page, 0.8)
                page.locator(".reader .pop button", has_text="讲解").click()
                self.wait_answer(page)
                self.settle(page, 1.5)
            with self.recording(page, f"clip-crop-ask{sfx}", "Crop Figure 1 → 问 Claude: the image is attached and the question typed"):
                self.settle(page, 0.6)
                self.crop(page, 3, FIGURE1)
                self.settle(page, 0.6)
                page.locator(".reader .pop button", has_text="问 Claude").click()
                self.settle(page, 0.4)
                page.keyboard.type("decoder 的第一个子层为什么要 mask？", delay=60)
                self.settle(page, 1.2)
            page.keyboard.press("Meta+a")
            page.keyboard.press("Backspace")
        with self.context(scheme, assistant=True) as page:
            page.locator("aside.side textarea").click()
            page.keyboard.type("帮我加一个里程碑：11 月 8 日前完成 Beacon kernel 的 tile 调参，算 Gate，研究线 Project Beacon", delay=8)
            page.keyboard.press("Enter")
            self.wait_answer(page)
            self.scroll_chat(page, "bottom")
            self.shot(page, f"live-assistant-milestone{sfx}", "⌘J assistant adding a milestone to the plan (real answer; the change is shown in the chat)")

    # ---------- clips ----------

    @contextmanager
    def recording(self, page: Page, name: str, what: str) -> Iterator[None]:
        frames: list[tuple[float, bytes]] = []
        cdp = page.context.new_cdp_session(page)

        def on_frame(ev: dict) -> None:
            frames.append((ev["metadata"]["timestamp"], base64.b64decode(ev["data"])))
            try:
                cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
            except Exception:  # noqa: BLE001 - the session may be closing
                pass

        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": W * 2, "maxHeight": H * 2})
        try:
            yield
            self.settle(page, 1.2)
        finally:
            cdp.send("Page.stopScreencast")
            page.wait_for_timeout(300)
        self.encode(frames, name, what)

    def encode(self, frames: list[tuple[float, bytes]], name: str, what: str) -> None:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if len(frames) < 2:
            print(f"  ! {name}: no frames")
            return
        tmp = self.out / f".{name}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        lines = []
        for k, (t, data) in enumerate(frames):
            f = tmp / f"{k:05d}.jpg"
            f.write_bytes(data)
            dur = (frames[k + 1][0] - t) if k + 1 < len(frames) else 1.5
            lines += [f"file '{f.name}'", f"duration {min(max(dur, 0.02), 1.6):.3f}"]  # long idle stretches are cut to 1.6 s
        lines.append(f"file '{tmp / f'{len(frames) - 1:05d}.jpg'}'")
        (tmp / "list.txt").write_text("\n".join(lines) + "\n")
        mp4, gif = self.out / f"{name}.mp4", self.out / f"{name}.gif"
        base = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt")]
        subprocess.run([*base, "-vf", "fps=30,scale=1920:-2:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-crf", "20",
                        "-preset", "slow", "-movflags", "+faststart", str(mp4)], check=True)
        subprocess.run([*base, "-vf", "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];"
                        "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", str(gif)], check=True)
        shutil.rmtree(tmp)
        self.index += [(mp4.name, what + " (MP4, 1920 wide)"), (gif.name, what + " (GIF, 960 wide)")]
        print(f"  {mp4.name}  {gif.name}")

    def clips(self, scheme: str) -> None:
        sfx = f"-{scheme}"
        with self.context(scheme) as page:
            with self.recording(page, f"clip-cmdk-open{sfx}", "⌘K → type → Enter opens the paper in the reader"):
                self.settle(page, 0.8)
                self.open_paper(page, "Flash", keycap=True)
                self.settle(page, 1.2)
        with self.context(scheme) as page:
            self.open_paper(page)
            with self.recording(page, f"clip-select-translate{sfx}", "Select a sentence → 翻译 → the translation appears in place"):
                self.settle(page, 0.6)
                self.select_text(page, DEFINITION)
                self.settle(page, 0.8)
                page.locator(".reader .pop button", has_text="翻译").click()
                page.wait_for_selector(".reader .pop .trans .meta", timeout=30000)
                self.settle(page, 2.5)
        with self.context(scheme) as page:
            with self.recording(page, f"clip-assistant{sfx}", "⌘J opens the workbench assistant; it answers from the timeline, list and ideas"):
                self.settle(page, 0.8)
                page.keyboard.press("Meta+j")
                self.keycap(page, "⌘J")
                self.settle(page, 1.2)
                page.locator("aside.side button", has_text="最近 Deadline").click()
                self.wait_answer(page, timeout=180)
                self.scroll_chat(page, "bottom")
                self.settle(page, 2.0)


GROUPS = [("page-", "Full pages (1440×900 @2x = 2880×1800)"), ("closeup-", "Close-ups of single features"),
          ("reader-", "Reader states"), ("live-", "Real model answers on the public demo paper (dark)"),
          ("clip-", "Clips (MP4 + GIF)"), ("", "More full-window states (2880×1800)")]


def write_index(out: Path, index: list[tuple[str, str]]) -> None:
    """INDEX.json (merged with earlier runs) and INDEX.md, the human-readable list."""
    f = out / "INDEX.json"
    old = dict(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else {}
    old.update(dict(index))
    old = {k: v for k, v in old.items() if (out / k).exists()}
    f.write_text(json.dumps(sorted(old.items()), ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# PhD Studio screenshots", "",
             "Generated from the fictional demo workspace (`scripts/demo/make_demo.py`) by `scripts/demo/screenshots.py`.",
             "Everything personal in them is invented: the profile, research lines, ideas, milestones, scholars, labs and their",
             "papers. The papers on the reading list are well-known public arXiv papers; the reader opens them on page 3, not on",
             "the author list. Model answers (`live-*`, the co-reader panels and the assistant) are real Claude output on that",
             "public data. Browser: system Chrome, Asia/Shanghai time, zh-CN.", "",
             "Names end in `-light` / `-dark` for the color scheme.", ""]
    known = [g for g, _ in GROUPS if g]
    for prefix, title in GROUPS:
        rows = [(k, v) for k, v in sorted(old.items())
                if (k.startswith(prefix) if prefix else not any(k.startswith(g) for g in known))]
        if rows:
            lines += [f"## {title}", "", "| File | What it shows |", "|---|---|"]
            lines += [f"| `{k}` | {v.replace('|', '/')} |" for k, v in rows] + [""]
    (out / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8790")
    ap.add_argument("--out", type=Path, required=True, help="output folder (keep it outside the repository)")
    ap.add_argument("--only", default="pages,closeups", help="comma list of: live, pages, closeups, extras, extras_live, clips")
    ap.add_argument("--scheme", default="light,dark")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        s = Shots(browser, a.url, a.out)
        try:
            for part in a.only.split(","):
                for scheme in a.scheme.split(","):
                    print(f"{part} · {scheme}")
                    getattr(s, part)(scheme)
        finally:
            write_index(a.out, s.index)
            browser.close()
    print(f"{len(s.index)} files in {a.out} (descriptions in INDEX.json)")


if __name__ == "__main__":
    main()
