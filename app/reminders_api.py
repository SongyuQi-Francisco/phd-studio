"""Routes for Settings → Deadline reminders: reminder settings, the test notification and the LaunchAgent."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from . import notify, remind_agent, reminders

router = APIRouter(prefix="/api/reminders")


@router.get("")
def reminder_status() -> dict[str, Any]:
    return reminders.status()


@router.put("/settings")
def reminder_settings(patch: dict[str, Any]) -> dict[str, Any]:
    try:
        reminders.save_settings(patch)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e))
    reminders.mark_device()  # this Mac becomes a choice for 「提醒设备」 on the other one
    return reminders.status()


@router.post("/test")
def reminder_test() -> dict[str, Any]:
    return reminders.test_notification()


@router.post("/notification-settings")
def reminder_notification_settings() -> dict[str, bool]:
    return {"ok": notify.open_settings()}


@router.post("/agent")
def reminder_agent(body: dict[str, Any]) -> dict[str, Any]:
    """Install or remove the background check. Only ever called from the button in Settings."""
    try:
        if body.get("install"):
            remind_agent.install()
        else:
            remind_agent.uninstall()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    reminders.mark_device(agent=bool(body.get("install")), force=True)
    return reminders.status()
