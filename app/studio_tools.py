"""In-process MCP tools that let the Studio assistant change the workbench itself:
reading list, milestones, submission targets, watched venues, ideas.

These write only the app's own YAML stores (direct, no approval card), except venue_details_propose, which
only records a proposal the user confirms in the venue sheet. Every
change is reported to the UI through `emit` so the pages refresh and the chat
shows what was done.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import idea_ops, plan, reading, tracking, venue_check, venues

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

    @tool("reading_push", "把论文加入阅读清单收件箱。paper=论文笔记名，或 arxiv=arXiv 号（没有笔记时），"
                          "或 ext=doi:<DOI> / openreview:<forum id> / acl:<Anthology id>（没有 arXiv 版本时）。reason 必填。",
          _obj({"paper": S, "arxiv": S, "ext": S, "url": S, "title": S, "reason": S, "tier": {"type": "string", "enum": ["T1", "T2", "T3"]},
                "lines": SA, "due": S}, ["reason"]))
    async def reading_push(a):
        try:
            item, what = reading.push(paper=a.get("paper"), arxiv=a.get("arxiv"), ext=a.get("ext"), url=a.get("url"),
                                      title=a.get("title"),
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

    @tool("venue_details_propose",
          "提交会议投稿信息（赛道、页数、模板、审稿规则、截稿）的更新提案。只提交提案，不会改任何数据：用户在会议详情页看差异后确认才生效。"
          "venue=会议简称；details 只写有变化的字段（可用：status=confirmed|previous-edition、edition、edition_year、dates、location、homepage、cfp_url、"
          "deadlines=[{track,abstract,paper}]、tracks=[{name,page_limit,notes}]、format={template,review_model,notable_rules[]}、notes）。"
          "列表字段要写完整的新列表；核对后没有变化就让 details 为空（只更新核对日期）。sources 必填：你实际读过的官方网址。summary 一句话说明改了什么。",
          _obj({"venue": S, "details": {"type": "object"}, "sources": SA, "summary": S}, ["venue", "sources"]))
    async def venue_details_propose(a):
        try:
            prop = venue_check.propose(a["venue"], a.get("details") or {}, a.get("sources") or [], a.get("summary") or "")
        except ValueError as e:
            return _err(str(e))
        what = f"{prop['changes']} 处改动" if prop["changes"] else "没有变化，只更新核对日期"
        await changed("venues", f"会议核对：{prop['acronym']} 的更新提案待你确认（{what}）", prop)
        return _ok({"proposal": prop["id"], "changes": prop["diff"],
                    "note": "已提交，等用户在会议详情页确认后才会写入；你不能应用它。"})

    @tool("idea_add", "记一个想法到想法收件箱。tags 是自由标签（先 idea_list 看看已有的，尽量复用）。",
          _obj({"text": S, "line": S, "tags": SA, "paper": S}, ["text"]))
    async def idea_add(a):
        try:
            rec = plan.add_idea(a["text"], line=a.get("line"), tags=a.get("tags"), paper=a.get("paper"), source="assistant")
        except ValueError as e:
            return _err(str(e))
        await changed("ideas", f"想法：{rec['text'][:40]}", rec)
        return _ok(rec)

    @tool("idea_update", "修改想法：status(new/kept/promoted/dropped)、line、text、note（整理成的 idea 笔记的 vault 路径）；"
          "标签用 tags_add / tags_remove 增删；restore=true 把被合并掉的想法恢复。",
          _obj({"id": S, "status": S, "line": S, "text": S, "note": S, "tags_add": SA, "tags_remove": SA,
                "restore": {"type": "boolean"}}, ["id"]))
    async def idea_update(a):
        patch = {k: v for k, v in a.items() if k not in ("id", "tags_add", "tags_remove", "restore")}
        tags = a.get("tags_add") or a.get("tags_remove")
        if not (patch or tags or a.get("restore")):
            return _err("没有要改的内容")
        try:  # one step: a rejected change leaves the restore and the tags undone too
            rec = idea_ops.update(a["id"], patch, restore=bool(a.get("restore")), tags_add=a.get("tags_add") or [],
                                  tags_remove=a.get("tags_remove") or [])
        except KeyError:
            return _err(f"no idea {a['id']}")
        except ValueError as e:
            return _err(str(e))
        await changed("ideas", f"想法：已更新（{rec.get('status')}）", rec)
        return _ok(rec)

    @tool("idea_merge", "把两条以上的想法合并成一条（原想法标为 merged，保留来源，可恢复）。ids 至少两个；"
          "text 是合并后的文字（不填就用第一条的）；line 不填会取多数。已整理成笔记（promoted）的不能合并。",
          _obj({"ids": SA, "text": S, "line": S}, ["ids"]))
    async def idea_merge(a):
        try:
            rec = idea_ops.merge(a["ids"], text=a.get("text"), line=a.get("line"), source="assistant")
        except KeyError as e:
            return _err(f"no idea {e.args[0]}")
        except ValueError as e:
            return _err(str(e))
        await changed("ideas", f"想法：已合并 {len(rec['merged_from'])} 条", rec)
        return _ok(rec)

    @tool("follow", "关注一个关键词或学者（追踪页会实时查 arXiv 新论文）；remove=true 取消关注。kind: keyword|person。",
          _obj({"kind": S, "value": S, "note": S, "remove": {"type": "boolean"}}, ["kind", "value"]))
    async def follow(a):
        try:
            if a.get("remove"):
                hit = next((f for f in tracking.follows() if f["kind"] == a["kind"] and f["value"].lower() == a["value"].lower()), None)
                if not hit:
                    return _err("没有这个关注")
                tracking.remove_follow(hit["id"])
                await changed("track", f"取消关注：{a['value']}", hit)
                return _ok({"removed": hit["id"]})
            rec = tracking.add_follow(a["kind"], a["value"], a.get("note"))
        except ValueError as e:
            return _err(str(e))
        await changed("track", f"关注{'学者' if a['kind'] == 'person' else '关键词'}：{a['value']}", rec)
        return _ok(rec)

    @tool("tracker_latest", "最近一次每周实验室追踪的结果：Tier 统计、Claude 周报分析、论文列表。", _obj({}, []))
    async def tracker_latest(a):
        rs = tracking.runs()
        if not rs:
            return _err("没有追踪记录")
        r = tracking.run(rs[0]["run"])
        r["papers"] = [{k: p[k] for k in ("arxiv", "title", "tier", "labs", "published", "note", "listed")} for p in r["papers"]]
        return _ok(r)

    @tool("idea_list", "查看想法收件箱（最新 50 条），可按 status、line、tags（必须全部带上）过滤；"
          "被合并掉的想法默认不列，status=merged 或 include_merged=true 才显示。",
          _obj({"status": S, "line": S, "tags": SA, "include_merged": {"type": "boolean"}}, []))
    async def idea_list(a):
        if a.get("status") and a["status"] not in plan.I_STATUS_ALL:
            return _err(f"status must be one of {plan.I_STATUS_ALL}")
        try:
            return _ok(idea_ops.listing(a.get("status"), a.get("line"), a.get("tags"), bool(a.get("include_merged")))[:50])
        except ValueError as e:
            return _err(str(e))

    @tool("timeline", "查看时间线：关注会议的截稿、投稿目标、里程碑（days 天内，默认 120）。",
          _obj({"days": {"type": "integer"}}, []))
    async def timeline(a):
        days = int(a.get("days") or 120)
        tl = plan.timeline(days_ahead=days)
        now = datetime.now(timezone.utc).date().isoformat()
        return _ok({"today": now, "events": tl["events"], "targets": tl["targets"]})

    tools = [reading_push, reading_list, reading_update, milestone_add, milestone_update, target_add,
             target_update, venue_search, venue_watch, idea_add, idea_update, idea_merge, idea_list, follow,
             tracker_latest, timeline, venue_details_propose]
    names = [f"mcp__{SERVER}__{t.name}" for t in tools]
    return create_sdk_mcp_server(SERVER, tools=tools), names
