"""Routes for the model picker (#15): provider status for Settings and the panels, and binding a
conversation to a provider and model. The turns themselves run over /ws/chat/{key}."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import providers
from .claude_chat import registry

router = APIRouter()


@router.get("/api/providers")
async def provider_list() -> dict[str, Any]:
    """Every provider with its login state, the exact fix, and its models."""
    return {"providers": await run_in_threadpool(providers.describe_all)}


class ModelReq(BaseModel):
    provider: str
    model: str = ""


@router.post("/api/chat/{stem}/model")
async def set_chat_model(stem: str, req: ModelReq) -> dict[str, Any]:
    try:
        chat = registry.get(stem)
    except FileNotFoundError:
        raise HTTPException(404, "没有这个对话")
    p = providers.get(req.provider)
    if p is None:
        raise HTTPException(400, f"没有这个模型来源：{req.provider}")
    if not await run_in_threadpool(p.valid_model, req.model):
        raise HTTPException(400, f"{p.label} 没有模型 {req.model}")
    try:
        return await chat.set_model(req.provider, req.model)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
