"""In-process MCP tools that let the Studio assistant change the workbench itself:
reading list, milestones, submission targets, watched venues, ideas.

These write only the app's own YAML stores (direct, no approval card), except venue_details_propose,
map_propose and map_peer_propose, which only record a proposal the user confirms (venue sheet, research map page). Every
change is reported to the UI through `emit` so the pages refresh and the chat
shows what was done.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool

from . import idea_ops, plan, reading, tracking, venue_check, venues
from .rmap import lines as rmap_lines, peers as rmap_peers, project as rmap_project, proposals as rmap_proposals

SERVER = "studio"


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


S = {"type": "string"}
SA = {"type": "array", "items": {"type": "string"}}


def _ok(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"错误：{msg}"}], "is_error": True}


# What the non-Claude models may call (through ro_tools): queries only.
READ_ONLY = ("reading_list", "idea_list", "timeline", "venue_search", "tracker_latest", "map_read", "map_gaps",
             "map_peer_suggest")

MAP_OPS = ("ops 是一批操作（按顺序；新节点用 ref=\"$名字\"，后面的操作可用 $名字 指它）："
           "{op:node.add, ref, parent:<节点 id 或 $名字>, edge:refines|depends, set:{type, title, status, body}}；"
           "{op:node.update, id, set:{status|title|body|type}}；{op:node.delete, id}；"
           "{op:edge.add, from, to, kind:refines|depends|alternative|conflicts, label}；{op:edge.delete, id}；"
           "{op:attach.add, kind:evidence, node, set:{type:paper|note|file|url, target, title, note, stance:supports|contradicts|context}}；"
           "同行用 {op:attach.add, kind:peer, node, set:{type:paper|scholar|lab, title, relation:same|competing|builds_on, url, date, entity}}"
           "（挂同行更方便的是 map_peer_propose）。"
           "type: question 问题 / hypothesis 路径或假设 / subline 子线 / experiment 实验 / result 结果 / decision 决策 / kill Kill criteria / open 开放问题；"
           "status: todo 未开始 / active 进行中 / verified 已验证 / blocked 没打通 / refuted 证伪 / dropped 放弃。"
           "证据 target：paper=论文笔记名，note/file=vault 相对路径，url=链接。")


def _map_slug(key: str) -> str | None:
    """A map by slug, title or research line name."""
    slugs = rmap_project.slugs()
    if key in slugs:
        return key
    key = key.strip().lower()
    for x in slugs:
        sm = rmap_project.summary(rmap_project.get(x))
        if key in {str(v).lower() for v in (sm.get("title"), sm.get("line")) if v}:
            return x
    return None


def _map_brief(slug: str) -> dict[str, Any]:
    """A compact map for the model: nodes with their parents, other edges, evidence, the latest log lines."""
    m = rmap_project.get(slug).read()
    parents: dict[str, list[str]] = {}
    other = []
    for e in m["edges"]:
        if e["kind"] == "refines":
            parents.setdefault(e["to"], []).append(e["from"])
        else:
            other.append({k: e[k] for k in ("id", "from", "to", "kind") if k in e} | ({"label": e["label"]} if e.get("label") else {}))
    ev: dict[str, list[str]] = {}
    for a in m["attachments"].get("evidence", []):
        ev.setdefault(a["node"], []).append(a.get("title") or a["target"])
    peers: dict[str, list[str]] = {}
    for a in m["attachments"].get("peer", []):
        peers.setdefault(a["node"], []).append(f"{a['title']} ({a['relation']})")
    nodes = [{"id": n["id"], "type": n["type"], "status": n.get("status", "todo"), "title": n["title"],
              **({"parents": parents[n["id"]]} if n["id"] in parents else {}),
              **({"evidence": ev[n["id"]]} if n["id"] in ev else {}),
              **({"peers": peers[n["id"]]} if n["id"] in peers else {}),
              **({"body": n["body"][:300]} if n.get("body") else {})} for n in m["nodes"]]
    log = [{"at": e["at"], "who": e.get("who"), "text": e.get("summary")}
           for e in rmap_project.get(slug).events(kind="log", limit=8)["events"]]
    return {"project": {"slug": slug, **{k: m["project"].get(k) for k in ("title", "line", "note")}},
            "nodes": nodes, "edges": other, "recent_log": log}


def build(emit: Callable[[dict[str, Any]], Awaitable[None]]):
    tools = _tools(emit)
    names = [f"mcp__{SERVER}__{t.name}" for t in tools]
    return create_sdk_mcp_server(SERVER, tools=tools), names


def read_only(emit: Callable[[dict[str, Any]], Awaitable[None]]) -> list[SdkMcpTool]:
    return [t for t in _tools(emit) if t.name in READ_ONLY]


def _tools(emit: Callable[[dict[str, Any]], Awaitable[None]]) -> list[SdkMcpTool]:
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

    @tool("target_add", "为一篇在研论文设定目标会议和届次（可带备选）。venue 用会议简称，如 SIGIR、ACL；edition 是会议年份（整数，如 2027），"
                        "不填就取最近一届；deadline 可选，指定某一轮截稿（该轮全文截止的 UTC 日期 YYYY-MM-DD，查 venue_search）。"
                        "同一会议可以有多个目标（不同论文、不同届次）。",
          _obj({"title": S, "venue": S, "line": S, "alternatives": SA, "notes": S, "edition": {"type": "integer"},
                "deadline": S}, ["title", "venue"]))
    async def target_add(a):
        try:
            rec = plan.add_target(a["title"], a["venue"], line=a.get("line"), alternatives=a.get("alternatives"),
                                  notes=a.get("notes"), edition=a.get("edition"), deadline=a.get("deadline"))
        except ValueError as e:
            return _err(str(e))
        await changed("plan", f"目标：{rec['title']} → {rec['venue'].upper()}" + (f" {rec['edition']}" if rec.get("edition") else ""), rec)
        return _ok(rec)

    @tool("target_update", "修改投稿目标：venue、edition（年份，0 表示改回自动）、deadline（某一轮截稿的日期，空字符串表示整届）、alternatives、"
                           "status(planning/writing/submitted/accepted/rejected/dropped)、notes；delete=true 删除。"
                           "标为 submitted/accepted 后，该届剩下的截稿不再为这篇论文提醒，其他论文的目标和之后的届次照常提醒。",
          _obj({"id": S, "title": S, "venue": S, "edition": {"type": "integer"}, "deadline": S, "alternatives": SA,
                "status": S, "line": S, "notes": S, "delete": {"type": "boolean"}}, ["id"]))
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
          "列表字段要写完整的新列表；官网已经没有的旧字段用 remove 列出来删掉（可删：dates、location、homepage、cfp_url、deadlines、tracks、notes、"
          "format.template、format.review_model、format.notable_rules）；核对后没有变化就让 details 为空（只更新核对日期）。"
          "sources 必填：你实际读过的官方网址。summary 一句话说明改了什么。",
          _obj({"venue": S, "details": {"type": "object"}, "sources": SA, "summary": S, "remove": SA}, ["venue", "sources"]))
    async def venue_details_propose(a):
        try:
            prop = venue_check.propose(a["venue"], a.get("details") or {}, a.get("sources") or [], a.get("summary") or "",
                                       a.get("remove") or [])
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

    @tool("map_read", "读研究地图。不给 project 就列出所有课题地图（slug、标题、研究线、各状态节点数）；"
                      "给 project（slug 或标题或研究线名）就返回这张图：节点（id、type、status、title、parents、证据）、"
                      "细分之外的连线、最近几条日志。改地图前先读。",
          _obj({"project": S}, []))
    async def map_read(a):
        slugs = rmap_project.slugs()
        if not a.get("project"):
            return _ok([rmap_project.summary(rmap_project.get(x)) for x in slugs])
        hit = _map_slug(a["project"])
        if not hit:
            return _err(f"没有这张地图：{a['project']}（现有：{', '.join(slugs) or '无'}）")
        return _ok(_map_brief(hit))

    @tool("map_propose",
          "提交研究地图的改动提案。只记成提案，不会改地图：用户在研究地图页看清单确认后才写入。"
          "一句话进展（如「消融做完了，工具调用在长会话上没帮助」）：先 map_read，再拆成节点改动（改状态、加实验或结果节点、连线）"
          "+ 证据（attach.add evidence，挂论文笔记或 vault 笔记）+ summary（这句进展整理成一句日志）。"
          "给已有地图用 project=slug；起草一张新地图用 new_project={title, line, note}（note=研究线笔记的 vault 路径）。"
          + MAP_OPS + " 节点 id 只能用 map_read 里的或本批 $名字，不要编。",
          _obj({"project": S, "new_project": {"type": "object"}, "summary": S, "ops": {"type": "array", "items": {"type": "object"}}},
               ["summary", "ops"]))
    async def map_propose(a):
        slug = None
        if a.get("project"):
            slug = _map_slug(a["project"])
            if not slug:
                return _err(f"没有这张地图：{a['project']}（新地图用 new_project）")
        try:
            prop = rmap_proposals.propose(ops_=a.get("ops") or [], summary=a.get("summary") or "", slug=slug,
                                          create=a.get("new_project") or None,
                                          kind="seed" if a.get("new_project") else "update")
        except (KeyError, ValueError) as e:
            return _err(e.args[0] if e.args else str(e))
        n = sum(1 for e in prop["events"] if e["kind"] != "log")
        title = (prop.get("create") or {}).get("title") or rmap_project.summary(rmap_project.get(prop["slug"]))["title"]
        await changed("rmap", f"研究地图：{title} 的{'第一版草稿' if prop['create'] else '更新提议'}待你确认（{n} 处改动）", prop)
        return _ok({"proposal": prop["id"], "changes": rmap_proposals.describe(prop),
                    "note": "已提交，等用户在研究地图页确认后才会写入；你不能应用它。"})

    @tool("map_seed_draft", "从一条研究线的笔记（One-line Pitch、Hypotheses、First Experiment、Kill Criteria、Related、"
                            "带日期的状态小节）生成第一版地图的草稿 ops，不提交。你再读笔记和 Evidence 补全、改短标题、按最新状态改节点状态，"
                            "然后用 map_propose(new_project=…, ops=…) 提交给用户确认。line=研究线名或笔记名。",
          _obj({"line": S}, ["line"]))
    async def map_seed_draft(a):
        try:
            return _ok(rmap_lines.draft(a["line"]))
        except KeyError as e:
            return _err(e.args[0])

    @tool("map_gaps", "研究地图的同行分析：空白分支（进行中的假设/子线没有任何同行在做）和撞车风险（节点进行中，"
                      "且做同一或竞争做法的同行 ≥ 3，或同一做法 ≥ 2），以及每个节点的同行数。不给 project 就看所有课题。",
          _obj({"project": S}, []))
    async def map_gaps(a):
        if a.get("project"):
            slug = _map_slug(a["project"])
            if not slug:
                return _err(f"没有这张地图：{a['project']}")
            r = rmap_peers.analyze(slug)
            return _ok({"project": r["title"], "slug": slug, "rule": {"dense": rmap_peers.DENSE, "same_dense": rmap_peers.SAME_DENSE},
                        "gaps": [{k: g[k] for k in ("id", "title", "type", "status")} for g in r["gaps"]],
                        "risks": [{k: g[k] for k in ("id", "title", "status", "same", "competing", "builds_on")} | {"peers": [p["title"] for p in g["peers"]]}
                                  for g in r["risks"]],
                        "nodes": [{k: n[k] for k in ("id", "title", "type", "status", "n")} for n in r["nodes"] if n["n"]]})
        o = rmap_peers.overview()
        return _ok({"rule": o["rule"], "gaps": o["gaps"], "risks": o["risks"],
                    "shared_peers": [{"title": c["title"], "projects": c["projects"]} for c in o["columns"] if len(c["projects"]) > 1]})

    @tool("map_peer_suggest", "同行推荐的候选：追踪页本周雷达、关注的关键词和学者、阅读清单里的新论文，按关键词配到可能相关的节点"
                              "（matches 里有 slug、node、共同的词）。这是粗配：读标题和摘要判断真正该挂哪个节点、什么关系，"
                              "不相关的跳过，再用 map_peer_propose 提交。project 可选。",
          _obj({"project": S}, []))
    async def map_peer_suggest(a):
        slug = None
        if a.get("project"):
            slug = _map_slug(a["project"])
            if not slug:
                return _err(f"没有这张地图：{a['project']}")
        rows = rmap_peers.suggestions(slug)
        return _ok([{k: s[k] for k in ("key", "title", "arxiv", "ext", "url", "authors", "date", "venue", "paper", "sources", "abstract")}
                    | {"matches": [{k: m[k] for k in ("slug", "node", "title", "status", "terms")} for m in s["matches"]]} for s in rows])

    @tool("map_peer_propose",
          "把同行挂到研究地图节点上，记成提案，用户在研究地图页确认后才写入。peers 每项：node（节点 id，来自 map_read / map_gaps / "
          "map_peer_suggest）、type（paper|scholar|lab）、title、relation（same 同一做法 | competing 竞争做法 | builds_on 在其基础上），"
          "可选 arxiv、ext（doi:/openreview:/acl: 键）、url、authors、venue、date（YYYY-MM）、note（一句为什么）。"
          "同一篇论文在几个课题里会被认成同一个同行（按 arXiv 号等）。",
          _obj({"project": S, "summary": S, "peers": {"type": "array", "items": {"type": "object"}}}, ["project", "summary", "peers"]))
    async def map_peer_propose(a):
        slug = _map_slug(a.get("project") or "")
        if not slug:
            return _err(f"没有这张地图：{a.get('project')}")
        ops = []
        for p in a.get("peers") or []:
            if not isinstance(p, dict):
                return _err("peers 每项应为对象")
            kind = p.get("type") or "paper"
            url = p.get("url") or (f"https://arxiv.org/abs/{p['arxiv']}" if p.get("arxiv") else None)
            entity = p.get("entity") or rmap_peers.entity_for(kind, title=p.get("title"), arxiv=p.get("arxiv"), ext=p.get("ext"),
                                                             lab=p.get("title") if kind == "lab" else None)
            ops.append({"op": "attach.add", "kind": "peer", "node": p.get("node"),
                        "set": {k: v for k, v in {"type": kind, "title": p.get("title"), "relation": p.get("relation"), "url": url,
                                                  "authors": p.get("authors"), "venue": p.get("venue"), "date": p.get("date"),
                                                  "note": p.get("note"), "source": "assistant", "entity": entity}.items() if v}})
        try:
            prop = rmap_proposals.propose(ops_=ops, summary=a.get("summary") or "", slug=slug)
        except (KeyError, ValueError) as e:
            return _err(e.args[0] if e.args else str(e))
        title = rmap_project.summary(rmap_project.get(slug))["title"]
        await changed("rmap", f"研究地图：{title} 有 {len(ops)} 个同行待你确认", prop)
        return _ok({"proposal": prop["id"], "changes": rmap_proposals.describe(prop),
                    "note": "已提交，等用户在研究地图页确认后才会写入；你不能应用它。"})

    tools = [reading_push, reading_list, reading_update, milestone_add, milestone_update, target_add,
             target_update, venue_search, venue_watch, idea_add, idea_update, idea_merge, idea_list, follow,
             tracker_latest, timeline, venue_details_propose, map_read, map_propose, map_seed_draft, map_gaps,
             map_peer_suggest, map_peer_propose]
    return tools
