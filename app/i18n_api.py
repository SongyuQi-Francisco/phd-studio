"""Interface language: the message tables for web/i18n.js (#49)."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request

from . import i18n
from .config import load_settings

router = APIRouter()
APP_PORT = 8770  # the workbench itself (start.sh, app/desktop.py); any other port is a development server


@router.get("/api/i18n")
def messages(request: Request) -> dict[str, Any]:
    """Both languages at once, so switching in Settings re-renders without another request."""
    dev = os.environ.get("STUDIO_DEBUG") == "1" or request.url.port not in (None, APP_PORT)
    return {"setting": load_settings().get("ui_language", "system"), "lang": i18n.ui_lang(),
            "system": i18n.system_lang(), "dev": dev, "messages": i18n.web_messages()}
