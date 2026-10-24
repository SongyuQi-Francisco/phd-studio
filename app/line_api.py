"""Routes for research-line chats (方向 page). The chat itself runs over /ws/chat/{key}."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from . import line_chat

router = APIRouter(prefix="/api/lines")


def _line(key: str) -> dict[str, Any]:
    try:
        return line_chat.resolve(key)
    except FileNotFoundError:
        raise HTTPException(404, "没有这条研究线")


@router.get("/{key}")
def line_context(key: str) -> dict[str, Any]:
    """What the line's chat is pointed at: note, roadmap, radar, plan, evidence, packets, template."""
    line = _line(key)
    return {"context": line_chat.context(line), "chat": line_chat.chat_stats(key)}


@router.get("/{key}/packet")
def packet_prompt(key: str) -> dict[str, Any]:
    """The 「起草导师汇报包」 request for this line, with the template filled in."""
    return line_chat.packet_prompt(_line(key))
