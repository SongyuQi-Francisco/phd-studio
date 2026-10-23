"""PhD Studio local server: http://127.0.0.1:8770"""
from __future__ import annotations

import asyncio
import base64
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import bilingual, library, reading
from .claude_chat import claude_status, draft_takeaway, history, registry
from .config import CROPS_DIR, PROFILE, STUDIO_DIR, load_settings, save_settings
from .translate import translate

WEB_DIR = STUDIO_DIR / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    reaper = asyncio.create_task(registry.reap_idle())
    yield
    reaper.cancel()
    await registry.close_all()


app = FastAPI(title="PhD Studio", lifespan=lifespan)


def _stem_or_404(stem: str) -> str:
    try:
        library.validate(stem)
    except FileNotFoundError:
        raise HTTPException(404, f"没有这篇论文的笔记：{stem}")
    return stem


@app.get("/api/papers")
def papers() -> dict[str, Any]:
    return {"papers": library.list_papers()}


@app.get("/api/papers/{stem}")
def paper(stem: str) -> dict[str, Any]:
    _stem_or_404(stem)
    info = library.get_paper(stem)
    info["bilingual"] = bilingual.status(stem)
    item = reading.find(reading.load(), stem)
    info["reading"] = item
    if item and library.arxiv_key(stem) and item.get("title"):
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
    reason: str = "自己加的"
    tier: str | None = None
    lines: list[str] = []


@app.post("/api/reading")
def reading_push(req: PushReq) -> dict[str, Any]:
    try:
        item, what = reading.push(paper=req.paper, arxiv=req.arxiv, reason=req.reason, source="self",
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


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return {"claude": await run_in_threadpool(claude_status), "settings": load_settings(),
            "focus": PROFILE.get("current_focus", "")}


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


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
