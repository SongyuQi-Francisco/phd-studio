"""In-process MCP tools that let the Studio assistant change the workbench itself:
reading list, milestones, submission targets, watched venues, ideas.

These write only the app's own YAML stores (direct, no approval card). Every
change is reported to the UI through `emit` so the pages refresh and the chat
shows what was done.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import plan, reading, venues

SERVER = "studio"


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


S = {"type": "string"}
SA = {"type": "array", "items": {"type": "string"}}


def _ok(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"错误：{msg}"}], "is_error": True}


def build(emit: Callable[[dict[str, Any]], Awaitable[None]]):
    async def changed(area: str, summary: str, record: Any) -> None:
        await emit({"type": "studio_changed", "area": area, "summary": summary, "record": record})

    @tool("reading_push", "把论文加入阅读清单收件箱。paper=论文笔记名，或 arxiv=arXiv 号（没有笔记时）。reason 必填。",
          _obj({"paper": S, "arxiv": S, "title": S, "reason": S, "tier": {"type": "string", "enum": ["T1", "T2", "T3"]},
                "lines": SA, "due": S}, ["reason"]))
    async def reading_push(a):
        try:
            item, what = reading.push(paper=a.get("paper"), arxiv=a.get("arxiv"), title=a.get("title"),
                                      reason=a["reason"], source="claude", pushed_by="工作台助手",
                                      tier=a.get("tier"), lines=a.get("lines"), due=a.get("due"))
        except reading.ReadingError as e:
            return _err(str(e))
        await changed("reading", f"阅读清单：{reading.key_of(item)}（{what}）", item)
        return _ok({"result": what, "item": item})

    @tool("reading_list", "查看阅读清单，可按 status 过滤（inbox/queued/reading/done/snoozed/dismissed）。",
          _obj({"status": S}, []))
    async def reading_list(a):
        items = reading.load()
        reading.summary(items)
        sel = [{k: i.get(k) for k in ("id", "key", "title", "status", "tier", "lines", "reason", "pushed_at")}
               for i in items if not a.get("status") or i["status"] == a["status"]]
        return _ok(sel[:80])

    @tool("reading_update", "修改阅读清单条目：status、tier、lines、due、takeaway、verdict(key/useful/irrelevant)、depth(skim/deep)。",
          _obj({"id": S, "status": S, "tier": S, "lines": SA, "due": S, "takeaway": S, "verdict": S, "depth": S}, ["id"]))
    async def reading_update(a):
        try:
            item = reading.update(a["id"], {k: v for k, v in a.items() if k != "id"})
        except reading.ReadingError as e:
            return _err(str(e))
        await changed("reading", f"阅读清单：已更新 {reading.key_of(item)}", item)
        return _ok(item)

    @tool("milestone_add", "添加个人里程碑 / Gate / Kill criteria 检查点。date 用 YYYY-MM-DD；kind: milestone|gate|kill|deadline。",
          _obj({"title": S, "date": S, "line": S, "kind": S, "notes": S}, ["title", "date"]))
    async def milestone_add(a):
        try:
            rec = plan.add_milestone(a["title"], a["date"], line=a.get("line"), kind=a.get("kind") or "milestone",
                                     notes=a.get("notes"))
        except ValueError as e:
            return _err(str(e))
        await changed("plan", f"里程碑：{rec['title']}（{rec['date']}）", rec)
        return _ok(rec)

    @tool("milestone_update", "修改里程碑：title、date、status(todo/doing/done/warn)、kind、line、notes；delete=true 删除。",
          _obj({"id": S, "title": S, "date": S, "status": S, "kind": S, "line": S, "notes": S,
                "delete": {"type": "boolean"}}, ["id"]))
    async def milestone_update(a):
        try:
            if a.get("delete"):
                plan.delete("milestones", a["id"])
                await changed("plan", "里程碑：已删除", {"id": a["id"]})
                return _ok({"deleted": a["id"]})
            rec = plan.update("milestones", a["id"], {k: v for k, v in a.items() if k not in ("id", "delete")})
        except (KeyError, ValueError) as e:
            return _err(str(e))
        await changed("plan", f"里程碑：已更新 {rec['title']}", rec)
        return _ok(rec)

    @tool("target_add", "为一篇在研论文设定目标会议（可带备选）。venue 用会议简称，如 SIGIR、ACL。",
          _obj({"title": S, "venue": S, "line": S, "alternatives": SA, "notes": S}, ["title", "venue"]))
    async def target_add(a):
        try:
            rec = plan.add_target(a["title"], a["venue"], line=a.get("line"), alternatives=a.get("alternatives"),
                                  notes=a.get("notes"))
        except ValueError as e:
            return _err(str(e))
        await changed("plan", f"目标：{rec['title']} → {rec['venue'].upper()}", rec)
        return _ok(rec)

    @tool("target_update", "修改投稿目标：venue、alternatives、status(planning/writing/submitted/accepted/rejected/dropped)、notes；delete=true 删除。",
          _obj({"id": S, "title": S, "venue": S, "alternatives": SA, "status": S, "line": S, "notes": S,
                "delete": {"type": "boolean"}}, ["id"]))
    async def target_update(a):
        try:
            if a.get("delete"):
                plan.delete("targets", a["id"])
                await changed("plan", "目标：已删除", {"id": a["id"]})
                return _ok({"deleted": a["id"]})
            rec = plan.update("targets", a["id"], {k: v for k, v in a.items() if k not in ("id", "delete")})
        except (KeyError, ValueError) as e:
            return _err(str(e))
        await changed("plan", f"目标：已更新 {rec['title']}", rec)
        return _ok(rec)

    @tool("venue_search", "在 CORE(ICORE2026)+CCF 会议库里查会议：等级、下一个截稿（UTC）、链接、录用率；有官方核实的赛道/页数信息也一并返回。",
          _obj({"query": S}, ["query"]))
    async def venue_search(a):
        exact = venues.get(a["query"])
        if exact:
            return _ok(exact)
        return _ok(venues.search(a["query"], limit=15))

    @tool("venue_watch", "关注 / 取消关注一个会议（关注的会议会出现在时间线上）。", _obj({"venue": S, "watch": {"type": "boolean"}}, ["venue"]))
    async def venue_watch(a):
        try:
            res = plan.watch(a["venue"], a.get("watch", True))
        except ValueError as e:
            return _err(str(e))
        await changed("plan", f"{'关注' if res['watched'] else '取消关注'}：{res['venue'].upper()}", res)
        return _ok(res)

    @tool("idea_add", "记一个想法到想法收件箱。", _obj({"text": S, "line": S, "tags": SA, "paper": S}, ["text"]))
    async def idea_add(a):
        try:
            rec = plan.add_idea(a["text"], line=a.get("line"), tags=a.get("tags"), paper=a.get("paper"), source="assistant")
        except ValueError as e:
            return _err(str(e))
        await changed("ideas", f"想法：{rec['text'][:40]}", rec)
        return _ok(rec)

    @tool("idea_list", "查看想法收件箱。", _obj({}, []))
    async def idea_list(a):
        return _ok(plan.ideas()[:50])

    @tool("timeline", "查看时间线：关注会议的截稿、投稿目标、里程碑（days 天内，默认 120）。",
          _obj({"days": {"type": "integer"}}, []))
    async def timeline(a):
        days = int(a.get("days") or 120)
        tl = plan.timeline(days_ahead=days)
        now = datetime.now(timezone.utc).date().isoformat()
        return _ok({"today": now, "events": tl["events"], "targets": tl["targets"]})

    tools = [reading_push, reading_list, reading_update, milestone_add, milestone_update, target_add,
             target_update, venue_search, venue_watch, idea_add, idea_list, timeline]
    names = [f"mcp__{SERVER}__{t.name}" for t in tools]
    return create_sdk_mcp_server(SERVER, tools=tools), names
