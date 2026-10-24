"""PhD Studio local server: http://127.0.0.1:8770"""
from __future__ import annotations

import asyncio
import base64
import os
import signal
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import bilingual, direction, excerpt, idea_ops, library, line_chat, notes, plan, reading, tracking, venues
from .claude_chat import claude_status, draft_takeaway, history, registry
from .config import CROPS_DIR, IDEA_RADAR, PROFILE, STUDIO_DIR, load_settings, save_settings
from .translate import translate
from .thumbs import router as thumbs_router

WEB_DIR = STUDIO_DIR / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    reaper = asyncio.create_task(registry.reap_idle())
    venues.refresh_in_background()
    yield
    reaper.cancel()
    bilingual.stop_all()
    await registry.close_all()


app = FastAPI(title="PhD Studio", lifespan=lifespan)
app.include_router(thumbs_router)
from .bilingual_api import router as bilingual_queue_router  # noqa: E402  (bilingual PDFs made ahead of time)
app.include_router(bilingual_queue_router)


@app.middleware("http")
async def revalidate_app_files(request, call_next):
    """App JS/CSS changes between versions; make browsers revalidate instead of using a stale copy."""
    response = await call_next(request)
    path = request.url.path
    if not path.startswith(("/api/", "/vendor/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _stem_or_404(stem: str) -> str:
    if stem == "studio":  # the workbench-wide assistant
        return stem
    if line_chat.is_line_key(stem):  # a research line's own chat
        if not line_chat.known(stem):
            raise HTTPException(404, "没有这条研究线")
        return stem
    try:
        library.validate(stem)
    except FileNotFoundError:
        raise HTTPException(404, f"没有这篇论文的笔记：{stem}")
    return stem


@app.get("/api/papers")
def papers() -> dict[str, Any]:
    """Paper notes; queue_rank follows the reading list (reading, T1-T3, then inbox)."""
    tiers = {"T1": 0, "T2": 1, "T3": 2}
    items = [i for i in reading.load() if i.get("paper")]
    ranked = ([i for i in items if i["status"] == "reading"]
              + sorted((i for i in items if i["status"] == "queued"), key=lambda i: tiers.get(i.get("tier"), 3))
              + sorted((i for i in items if i["status"] == "inbox"), key=lambda i: i.get("pushed_at", ""),
                       reverse=True))
    rank = {i["paper"]: n for n, i in enumerate(ranked)}
    out = library.list_papers()
    for p in out:
        p["queue_rank"] = rank.get(p["stem"])
    return {"papers": out}


@app.get("/api/papers/{stem}")
def paper(stem: str) -> dict[str, Any]:
    _stem_or_404(stem)
    info = library.get_paper(stem)
    info["bilingual"] = bilingual.status(stem)
    item = reading.find(reading.load(), stem)
    info["reading"] = item
    if item and library.is_external(stem) and item.get("title"):
        info["title"] = item["title"]
    return info


@app.get("/api/pdf/{stem}")
async def pdf(stem: str, variant: str = "original") -> FileResponse:
    _stem_or_404(stem)
    if variant == "original":
        try:
            path = await run_in_threadpool(library.ensure_pdf, stem)
        except FileNotFoundError as e:
            raise HTTPException(404, f"找不到 PDF：{e}")
    else:
        path = bilingual.output(stem, variant)
        if path is None:
            raise HTTPException(404, "双语版本还没生成")
    return FileResponse(path, media_type="application/pdf")


@app.get("/api/pdf/{stem}/page/{page}")
async def page_text(stem: str, page: int) -> dict[str, str]:
    _stem_or_404(stem)
    return {"text": await run_in_threadpool(library.page_text, stem, page)}


class TranslateReq(BaseModel):
    text: str


@app.post("/api/translate")
async def translate_text(req: TranslateReq) -> dict[str, str]:
    try:
        return await run_in_threadpool(translate, req.text)
    except RuntimeError as e:
        raise HTTPException(502, f"翻译失败：{e}")


class CropReq(BaseModel):
    png_base64: str


@app.post("/api/crops")
def save_crop(req: CropReq) -> dict[str, str]:
    data = base64.b64decode(req.png_base64.split(",", 1)[-1])
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise HTTPException(400, "不是 PNG")
    name = f"{uuid.uuid4().hex[:12]}.png"
    (CROPS_DIR / name).write_bytes(data)
    return {"name": name}


class ExcerptReq(BaseModel):
    text: str
    page: int | None = None
    section: str = excerpt.NEW_SECTION
    comment: str | None = None


@app.get("/api/excerpt/{stem}")
def excerpt_sections(stem: str) -> dict[str, Any]:
    _stem_or_404(stem)
    try:
        text = library.note_path(stem).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(400, "这篇还没有笔记，先建笔记再摘录")
    return {"sections": excerpt.headings(text), "default": excerpt.NEW_SECTION}


@app.post("/api/excerpt/{stem}")
def excerpt_add(stem: str, req: ExcerptReq) -> dict[str, Any]:
    _stem_or_404(stem)
    try:
        return excerpt.append(stem, req.text, req.page, req.section, req.comment)
    except FileNotFoundError:
        raise HTTPException(400, "这篇还没有笔记，先建笔记再摘录")
    except KeyError:
        raise HTTPException(400, f"笔记里没有「{req.section}」这一节")
    except ValueError as e:
        raise HTTPException(400, str(e))


class ExcerptUndoReq(BaseModel):
    line: str
    inserted: str | None = None  # exactly what append() inserted, for a byte-exact undo
    section: str = excerpt.NEW_SECTION
    created: bool = False
    mtime_ns: int | None = None


@app.post("/api/excerpt/{stem}/undo")
def excerpt_undo(stem: str, req: ExcerptUndoReq) -> dict[str, bool]:
    _stem_or_404(stem)
    try:
        return excerpt.undo(stem, req.line, req.inserted, req.section, req.created, req.mtime_ns)
    except FileNotFoundError:
        raise HTTPException(404, "没有这篇笔记")


@app.get("/api/crops/{name}")
def get_crop(name: str) -> FileResponse:
    p = CROPS_DIR / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


@app.post("/api/bilingual/{stem}")
def start_bilingual(stem: str) -> dict[str, Any]:
    _stem_or_404(stem)
    return bilingual.start(stem)


@app.get("/api/bilingual/{stem}")
def bilingual_status(stem: str) -> dict[str, Any]:
    _stem_or_404(stem)
    return bilingual.status(stem)


@app.get("/api/reading")
def reading_list() -> dict[str, Any]:
    items = reading.load()
    return {"items": items, "summary": reading.summary(items)}


class PushReq(BaseModel):
    paper: str | None = None
    arxiv: str | None = None
    ext: str | None = None  # doi:<doi> / openreview:<id> / acl:<id>, for a paper without a note or arXiv id
    reason: str = "自己加的"
    tier: str | None = None
    lines: list[str] = []


@app.post("/api/reading")
def reading_push(req: PushReq) -> dict[str, Any]:
    try:
        item, what = reading.push(paper=req.paper, arxiv=req.arxiv, ext=req.ext, reason=req.reason, source="self",
                                  tier=req.tier, lines=req.lines)
    except reading.ReadingError as e:
        raise HTTPException(400, str(e))
    return {"item": item, "result": what}


@app.patch("/api/reading/{item_id}")
def reading_update(item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"item": reading.update(item_id, patch)}
    except reading.ReadingError as e:
        raise HTTPException(400, str(e))


class OpenedReq(BaseModel):
    key: str


@app.post("/api/reading/opened")
def reading_opened(req: OpenedReq) -> dict[str, Any]:
    return {"item": reading.mark_opened(req.key)}


@app.post("/api/reading/draft/{stem}")
async def reading_draft(stem: str) -> dict[str, str]:
    _stem_or_404(stem)
    try:
        return {"takeaway": await draft_takeaway(stem)}
    except Exception as e:  # noqa: BLE001 - shown in the dialog
        raise HTTPException(502, str(e))


@app.post("/api/shutdown")
async def shutdown() -> dict[str, bool]:
    """Quit button in the UI. In the desktop window this closes the window (which stops the
    server); when the server runs on its own, it stops the server."""
    quit_ = getattr(app.state, "quit", None)
    loop = asyncio.get_running_loop()
    if quit_:
        loop.call_later(0.3, quit_)
    else:
        loop.call_later(0.3, os.kill, os.getpid(), signal.SIGTERM)
    return {"ok": True, "window": bool(quit_)}


@app.post("/api/debug/eval")
async def debug_eval(body: dict[str, Any]) -> dict[str, Any]:
    """Only exists when the desktop window was started with STUDIO_DEBUG=1."""
    run = getattr(app.state, "eval_js", None)
    if run is None:
        raise HTTPException(404)
    try:
        return {"result": await run_in_threadpool(run, body.get("code", ""))}
    except Exception as e:  # noqa: BLE001 - report the JS error to the test script
        return {"error": f"{e.__class__.__name__}: {e}"}


@app.get("/api/debug/menus")
def debug_menus() -> dict[str, Any]:
    """Debug only: the native menu bar as titles and shortcuts."""
    if getattr(app.state, "eval_js", None) is None:
        raise HTTPException(404)
    from AppKit import NSApplication
    out = {}
    main = NSApplication.sharedApplication().mainMenu()
    for i in range(main.numberOfItems() if main else 0):
        item = main.itemAtIndex_(i)
        sub = item.submenu()
        out[str(item.title() or sub.title() if sub else item.title())] = [
            f"{sub.itemAtIndex_(j).title()} ⌘{sub.itemAtIndex_(j).keyEquivalent()}" for j in range(sub.numberOfItems() if sub else 0)
            if not sub.itemAtIndex_(j).isSeparatorItem()]
    return out


@app.post("/api/focus")
async def focus() -> dict[str, bool]:
    """Launcher double-clicked while running: bring the desktop window forward."""
    show = getattr(app.state, "focus", None)
    if show is None:
        raise HTTPException(404, "no desktop window")
    show()
    return {"ok": True}


# ---------- venues, plan, ideas, overview ----------


@app.get("/api/venues")
def venue_list(q: str = "", ccf: str = "", core: str = "", scope: str = "all", limit: int = 300) -> dict[str, Any]:
    if scope == "watched":
        keys = plan.watched_keys()
        rows = [venues.summary(venues.catalog()[k]) for k in keys if k in venues.catalog()]
    else:
        rows = venues.search(q, ccf=ccf, core=core, limit=limit)
    watched = set(plan.watched_keys())
    for r in rows:
        r["watched"] = r["key"] in watched
    return {"venues": rows, "stats": venues.stats()}


@app.get("/api/venues/{key}")
def venue_detail(key: str) -> dict[str, Any]:
    v = venues.get(key)
    if not v:
        raise HTTPException(404, "没有这个会议")
    v["watched"] = v["key"] in set(plan.watched_keys())
    return v


@app.post("/api/venues/refresh")
async def venue_refresh() -> dict[str, Any]:
    try:
        return await run_in_threadpool(venues.refresh, True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"更新失败：{e}")


class WatchReq(BaseModel):
    on: bool = True


@app.post("/api/venues/{key}/watch")
def venue_watch(key: str, req: WatchReq) -> dict[str, Any]:
    try:
        return plan.watch(key, req.on)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/plan")
def plan_timeline() -> dict[str, Any]:
    return plan.timeline()


@app.post("/api/plan/{collection}")
def plan_add(collection: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        if collection == "milestones":
            return plan.add_milestone(body["title"], body["date"], line=body.get("line"),
                                      kind=body.get("kind") or "milestone", notes=body.get("notes"))
        if collection == "targets":
            return plan.add_target(body["title"], body["venue"], line=body.get("line"),
                                   alternatives=body.get("alternatives"), notes=body.get("notes"))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    raise HTTPException(404)


@app.patch("/api/plan/{collection}/{rid}")
def plan_patch(collection: str, rid: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return plan.update(collection, rid, body)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.delete("/api/plan/{collection}/{rid}")
def plan_delete(collection: str, rid: str) -> dict[str, bool]:
    try:
        plan.delete(collection, rid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.get("/api/ideas")
def idea_list(merged: bool = False) -> dict[str, Any]:
    return {"ideas": idea_ops.listing(include_merged=merged)}


@app.post("/api/ideas")
def idea_add(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return plan.add_idea(body.get("text", ""), line=body.get("line"), paper=body.get("paper"),
                             page=body.get("page"), tags=body.get("tags"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch("/api/ideas/{rid}")
def idea_patch(rid: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return idea_ops.update(rid, body)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/note")
def vault_note(path: str) -> dict[str, Any]:
    try:
        n = notes.read(notes.resolve(path))
    except FileNotFoundError:
        raise HTTPException(404, "没有这篇笔记")
    p = notes.resolve(path)
    out = {k: n[k] for k in ("path", "name", "frontmatter", "body", "obsidian_url")}
    out["paper"] = p.stem if p.parent == library.PAPERS_DIR.resolve() else None
    return out


@app.get("/api/note/find")
def vault_note_find(name: str) -> dict[str, Any]:
    p = notes.find_by_name(name)
    if not p:
        raise HTTPException(404, "vault 里没有这篇笔记")
    rel = notes.rel(p)
    paper = p.parent.resolve() == library.PAPERS_DIR.resolve()
    return {"path": rel, "paper": p.stem if paper else None}


@app.get("/api/direction")
def direction_page() -> dict[str, Any]:
    return {"lines": direction.lines(), "radar": direction.radar(), "roadmap": direction.roadmap(),
            "courses": direction.courses()}


@app.get("/api/track/runs")
def track_runs() -> dict[str, Any]:
    return {"runs": tracking.runs()}


@app.get("/api/track/runs/{run_id}")
def track_run(run_id: str) -> dict[str, Any]:
    try:
        return tracking.run(run_id)
    except FileNotFoundError:
        raise HTTPException(404, "没有这次运行")


@app.get("/api/track/labs")
def track_labs() -> dict[str, Any]:
    return {"labs": tracking.labs()}


@app.get("/api/track/follows")
def track_follows() -> dict[str, Any]:
    return {"follows": tracking.follows()}


@app.post("/api/track/follows")
def track_follow_add(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return tracking.add_follow(body.get("kind", "keyword"), body.get("value", ""), body.get("note"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/track/follows/{fid}")
def track_follow_remove(fid: str) -> dict[str, bool]:
    try:
        tracking.remove_follow(fid)
    except KeyError:
        raise HTTPException(404)
    return {"ok": True}


@app.get("/api/track/feed")
async def track_feed(refresh: bool = False, days: int = 30) -> dict[str, Any]:
    return {"feed": await run_in_threadpool(tracking.feed, days, 8, refresh)}


@app.post("/api/track/capture")
def track_capture(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return tracking.capture(body.get("text", ""), body.get("reason", ""), body.get("tier"), body.get("lines"))
    except reading.ReadingError as e:
        raise HTTPException(400, str(e))


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    tl = plan.timeline(days_back=0, days_ahead=120)
    items = reading.load()
    s = reading.summary(items)
    upcoming = [e for e in tl["events"] if e["days"] >= 0]
    return {
        "next_target": next((e for e in upcoming if e["type"] == "target"), None),
        "next_any": next((e for e in upcoming if e["type"] != "milestone"), None),
        "next_milestone": next((e for e in upcoming if e["type"] == "milestone"), None),
        "events": tl["events"],
        "targets": tl["targets"],
        "reading": {k: s[k] for k in ("inbox", "T1", "queued", "reading", "done_week", "caps")},
        "ideas_new": sum(1 for i in plan.ideas() if i.get("status") == "new"),
        "lines": PROFILE.get("lines", []),
    }


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return {"claude": await run_in_threadpool(claude_status), "settings": load_settings(),
            "focus": PROFILE.get("current_focus", ""), "lines": PROFILE.get("lines", []),
            "idea_radar": notes.rel(IDEA_RADAR) if IDEA_RADAR.exists() else None}


@app.post("/api/settings")
async def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    s = save_settings(patch)
    await registry.close_all()  # new model applies to the next turn
    return s


@app.get("/api/chat/{stem}")
def chat_history(stem: str) -> dict[str, Any]:
    _stem_or_404(stem)
    return {"messages": history(stem)}


@app.delete("/api/chat/{stem}")
async def chat_reset(stem: str) -> dict[str, bool]:
    _stem_or_404(stem)
    await registry.get(stem).reset()
    return {"ok": True}


@app.websocket("/ws/chat/{stem}")
async def chat_ws(ws: WebSocket, stem: str) -> None:
    await ws.accept()
    try:
        chat = registry.get(stem)
    except FileNotFoundError:
        await ws.close(code=4404)
        return
    chat.ws = ws
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "send":
                if chat.lock.locked():
                    await ws.send_json({"type": "error", "message": "上一条还在回答中"})
                    continue
                chat.task = asyncio.create_task(chat.send(
                    text=msg.get("text", ""),
                    page=msg.get("page"),
                    selection=msg.get("selection"),
                    images=msg.get("images") or [],
                    include_page_text=bool(msg.get("include_page_text")),
                    display=msg.get("display"),
                ))
            elif kind == "permission":
                chat.answer_permission(msg.get("id", ""), bool(msg.get("allow")))
            elif kind == "stop":
                await chat.interrupt()
    except WebSocketDisconnect:
        pass
    finally:
        if chat.ws is ws:
            chat.ws = None
            # An unanswered approval cannot be answered any more.
            for pid in list(chat.pending):
                chat.answer_permission(pid, False)


from .track_api import router as track_router  # noqa: E402  (追踪 page: scholars, matrix, 立即巡检)
app.include_router(track_router)
from .idea_api import router as idea_router  # noqa: E402  (想法: tags, merge, restore)
app.include_router(idea_router)
from .line_api import router as line_router  # noqa: E402  (方向 page: research-line chats, advisor packets)
app.include_router(line_router)
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
