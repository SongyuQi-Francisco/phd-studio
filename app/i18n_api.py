"""Interface language: the message tables for web/i18n.js (#49)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import i18n
from .config import load_settings

router = APIRouter()


@router.get("/api/i18n")
def messages() -> dict[str, Any]:
    """Both languages at once, so switching in Settings re-renders without another request."""
    return {"setting": load_settings().get("ui_language", "system"), "lang": i18n.ui_lang(),
            "system": i18n.system_lang(), "messages": i18n.web_messages()}
