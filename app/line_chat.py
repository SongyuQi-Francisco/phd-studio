"""Research-line chats: one long-running Claude conversation per research line, and the
advisor review packet (导师汇报包) drafted from it.

A line's chat key comes from its name, so it survives restarts and is the same on every
machine; the session id and the chat log stay per device, like paper chats. The system
prompt names the line's note and the folders around it (roadmap, idea radar, plan,
evidence, review packets); Claude reads them with tools when it needs them.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import config, library, notes
from .config import AGENTS_MD, CHATS_DIR, IDEA_RADAR, IDEA_TEMPLATE, PLAN_FILE, PROFILE, ROADMAP, VAULT_DIR

PREFIX = "line:"
_KEY = re.compile(r"^line:(?:[a-z0-9]+(?:-[a-z0-9]+)*-)?[0-9a-f]{12}$")
_DATE = re.compile(r"(20\d\d-\d\d-\d\d)")
DEFAULT_TEMPLATE = Path(__file__).with_name("templates") / "review_packet.md"
# Advisor packet templates looked for in the templates folder, in this order (case-insensitive).
TEMPLATE_NAMES = ("review packet template", "advisor packet template", "advisor review packet template",
                  "导师汇报包模板", "汇报包模板")
KILL_LABELS = {"clear": "未触发", "partial": "部分触发", "triggered": "已触发"}


def _norm(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(name)).split()).casefold()


def _squash(name: str) -> str:
    return re.sub(r"[\s_\-–—·.]+", "", _norm(name))


def line_key(name: str) -> str:
    """Chat key for a line name. Ignores case, spacing and Unicode normalization; safe in URLs and file names."""
    n = _norm(name)
    digest = hashlib.sha1(n.encode("utf-8")).hexdigest()[:12]
    slug = "-".join(re.findall(r"[a-z0-9]+", re.sub(r"^research line\s*-\s*", "", n)))[:32].strip("-")
    return f"{PREFIX}{slug}-{digest}" if slug else f"{PREFIX}{digest}"


def is_line_key(key: str) -> bool:
    return bool(_KEY.match(key or ""))


def short(name: str) -> str:
    return re.sub(r"^Research Line\s*-\s*", "", name)


def label(line: dict[str, Any]) -> str:
    return short(line["name"])


def assign_keys(items: list[dict[str, Any]]) -> None:
    """Give every research line a chat key: its configured line name, else its note name.

    Two notes on the same configured line keep apart: the later one is keyed by its note name.
    """
    seen: set[str] = set()
    for item in items:
        if item.get("kind") != "research-line":
            continue
        key = line_key(item.get("line") or item["name"])
        if key in seen:
            key = line_key(item["name"])
        seen.add(key)
        item["chat_key"] = key


def resolve(key: str) -> dict[str, Any]:
    """The research line (as on the 方向 page) a chat key belongs to."""
    if not is_line_key(key):
        raise FileNotFoundError(key)
    from . import direction  # direction imports this module

    for item in direction.lines():
        if item.get("chat_key") == key:
            return item
    raise FileNotFoundError(f"没有这条研究线：{key}")


def chat_stats(key: str) -> dict[str, Any]:
    """Turns in a line's current chat log and when it was last written."""
    p = CHATS_DIR / f"{library.safe_name(key)}.jsonl"
    try:
        return {"turns": p.read_text(encoding="utf-8").count('"role": "user"'), "last": p.stat().st_mtime}
    except OSError:
        return {"turns": 0, "last": None}


# ---------------- vault folders ----------------


def vault_folder(setting: str, name: str) -> Path:
    """`setting` (vault-relative) when given, else a top-level vault folder called `name`,
    with or without a number prefix ("08 Review Packets"), else <vault>/<name>."""
    if setting:
        return VAULT_DIR / setting
    want = _norm(name)
    if VAULT_DIR.is_dir():
        for p in sorted(VAULT_DIR.iterdir()):
            if p.is_dir() and _norm(re.sub(r"^\d+[\s._-]*", "", p.name)) == want:
                return p
    return VAULT_DIR / name


def evidence_dir() -> Path:
    return vault_folder(config.EVIDENCE, "Evidence")


def packets_dir() -> Path:
    return vault_folder(config.REVIEW_PACKETS, "Review Packets")


def templates_dir() -> Path:
    if config.TEMPLATES:
        return VAULT_DIR / config.TEMPLATES
    parent = (VAULT_DIR / IDEA_TEMPLATE).parent
    return parent if parent != VAULT_DIR and parent.is_dir() else vault_folder("", "Templates")


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(VAULT_DIR))
    except ValueError:
        return str(p)


def _md_count(d: Path) -> int:
    return sum(1 for _ in d.rglob("*.md")) if d.is_dir() else 0


# ---------------- review packets ----------------


def _frontmatter(p: Path) -> dict[str, Any]:
    try:
        return notes.split(p.read_text(encoding="utf-8", errors="replace"))[0]
    except OSError:
        return {}


def _packet_date(p: Path, fm: dict[str, Any]) -> str:
    for k in ("created", "date"):
        v = fm.get(k)
        m = _DATE.match(v.isoformat() if hasattr(v, "isoformat") else str(v or ""))
        if m:
            return m.group(1)
    m = _DATE.search(p.stem)
    if m:
        return m.group(1)
    return datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()


def packets() -> list[dict[str, Any]]:
    """Every note in the review packets folder, with its date and frontmatter."""
    d = packets_dir()
    out = []
    for p in sorted(d.glob("*.md")) if d.is_dir() else []:
        fm = _frontmatter(p)
        out.append({"path": _rel(p), "name": p.stem, "date": _packet_date(p, fm), "fm": fm})
    return out


def _names(line: dict[str, Any]) -> set[str]:
    names = {line["name"], short(line["name"]), line.get("line") or ""}
    return {_squash(n) for n in names if len(_squash(n)) >= 3}


def _values(v: Any) -> list[str]:
    vals = v if isinstance(v, list) else [v]
    return [notes.WIKILINK.sub(lambda m: m.group(1), str(x)).strip() for x in vals if x]


def _about(packet: dict[str, Any], names: set[str]) -> bool:
    fm = packet["fm"]
    for k in ("line", "lines", "research_line", "tags"):
        if any(_squash(v) in names for v in _values(fm.get(k))):
            return True
    stem = _squash(packet["name"])
    return any(n in stem for n in names)


def _is_advisor(packet: dict[str, Any]) -> bool:
    name = packet["name"].casefold()
    return str(packet["fm"].get("packet", "")).casefold() == "advisor" or "advisor" in name or "导师" in name


def previous_packet(line: dict[str, Any], index: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The latest review packet about this line: an advisor packet if there is one, else any review packet."""
    names = _names(line)
    mine = [p for p in (packets() if index is None else index) if _about(p, names)]
    if not mine:
        return None
    advisor = [p for p in mine if _is_advisor(p)]
    best = max(advisor or mine, key=lambda p: (p["date"], p["name"]))
    return {"path": best["path"], "name": best["name"], "date": best["date"]}


def annotate(items: list[dict[str, Any]]) -> None:
    """Chat key, chat size and last review packet for each research line on the 方向 page."""
    assign_keys(items)
    index = packets()
    for item in items:
        if "chat_key" in item:
            item["chat"] = chat_stats(item["chat_key"])
            item["packet"] = previous_packet(item, index)


def _safe_title(text: str) -> str:
    return " ".join(re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", text).split())


def packet_path(line: dict[str, Any], day: str) -> Path:
    """A new file in the review packets folder; never one that exists."""
    base = f"Advisor Packet - {_safe_title(label(line))} {day}"
    d = packets_dir()
    p, n = d / f"{base}.md", 2
    while p.exists():
        p, n = d / f"{base} ({n}).md", n + 1
    return p


# ---------------- template ----------------


def _vault_template(p: Path) -> dict[str, Any]:
    return {"source": "vault", "path": _rel(p), "text": p.read_text(encoding="utf-8")}


def template() -> dict[str, Any]:
    """The advisor packet template: paths.review_template, else one in the vault's templates
    folder, else the generic default kept in the repo."""
    if config.REVIEW_TEMPLATE:
        p = VAULT_DIR / config.REVIEW_TEMPLATE
        if p.is_file():
            return _vault_template(p)
    d = templates_dir()
    if d.is_dir():
        files = sorted(d.glob("*.md"))
        by_name = {_norm(p.stem): p for p in files}
        for n in TEMPLATE_NAMES:
            if n in by_name:
                return _vault_template(by_name[n])
        for p in files:
            s = _norm(p.stem)
            if ("packet" in s or "汇报" in s) and ("template" in s or "模板" in s):
                return _vault_template(p)
    return {"source": "default", "path": None, "text": DEFAULT_TEMPLATE.read_text(encoding="utf-8")}


def fill(text: str, values: dict[str, str]) -> str:
    """Replace {{name}} (and Obsidian's {{date:FORMAT}}) placeholders we know; leave the rest."""
    return re.sub(r"\{\{\s*(\w+)(?::[^}]*)?\s*\}\}", lambda m: values.get(m.group(1).lower(), m.group(0)), text)


# ---------------- prompts ----------------


def context(line: dict[str, Any]) -> dict[str, Any]:
    """What a line's chat is pointed at (vault-relative paths; None when missing)."""
    ev, pk = evidence_dir(), packets_dir()
    tpl = template()
    return {
        "line": label(line), "key": line.get("chat_key"), "configured": line.get("line"), "note": line.get("path"),
        "roadmap": _rel(ROADMAP) if ROADMAP.exists() else None,
        "idea_radar": _rel(IDEA_RADAR) if IDEA_RADAR.exists() else None,
        "plan": _rel(PLAN_FILE) if PLAN_FILE.exists() else None,
        "evidence": _rel(ev) if ev.is_dir() else None, "evidence_notes": _md_count(ev),
        "packets": _rel(pk) if pk.is_dir() else None, "packet_notes": _md_count(pk),
        "previous_packet": previous_packet(line),
        "template": {"source": tpl["source"], "path": tpl["path"]},
    }


def _facts(line: dict[str, Any]) -> list[str]:
    out = []
    head = [f"状态 {line['status']}" if line.get("status") else "",
            f"优先级 P{line['priority']}" if line.get("priority") is not None else "",
            f"周期 {line['horizon']}" if line.get("horizon") else ""]
    if any(head):
        out.append(" · ".join(x for x in head if x))
    for t in line.get("targets") or []:
        ev = next((e for e in line.get("deadlines") or [] if (e.get("target") or {}).get("id") == t.get("id")), None)
        alt = f"（备选 {', '.join(t['alternatives'])}）" if t.get("alternatives") else ""
        when = f"{ev['title']} {ev['at'][:10]}，还剩 {ev['days']} 天" if ev else "截稿未公布"
        out.append(f"投稿目标：{str(t.get('venue', '')).upper()}{alt} · {when}")
    open_ms = [m for m in line.get("milestones") or [] if m.get("status") != "done"][:4]
    if open_ms:
        out.append("未完成的里程碑：" + "；".join(
            f"{m['at'][:10]} {m['title']}" + ("（Gate）" if m.get("kind") == "gate" else "") for m in open_ms))
    if line.get("kills"):
        out.append("Kill criteria：" + " · ".join(
            f"{k.get('id') or 'K' + str(i + 1)} {KILL_LABELS.get(k.get('state'), k.get('state'))}"
            for i, k in enumerate(line["kills"])))
    out.append(f"阅读清单里标了这条线的论文 {line.get('papers', 0)} 篇，想法 {line.get('ideas', 0)} 个")
    return out


def system_prompt(line: dict[str, Any], today: date | None = None) -> str:
    ctx = context(line)
    today = today or date.today()
    lang = PROFILE.get("answer_language", "中文")
    agents = AGENTS_MD.read_text(encoding="utf-8") if AGENTS_MD.exists() else ""
    name = ctx["line"]
    note = (f"- 研究线笔记：`{ctx['note']}`。现状、Kill Criteria、First Experiment 和按日期记的进展都在这里，"
            "讨论前先读最新版本（它可能在别处被改过）。"
            if ctx["note"] else
            f"- 这条线还没有单独的研究线笔记（它是配置里的研究线「{ctx['configured'] or name}」）；需要时用 Grep 在 vault 里找相关笔记。")
    prev = ctx["previous_packet"]
    reads = [
        f"- PhD 路线图：`{ctx['roadmap']}`" if ctx["roadmap"] else "",
        f"- Idea Radar：`{ctx['idea_radar']}`" if ctx["idea_radar"] else "",
        "- 规划与时间线：timeline 工具（里程碑、投稿目标、截稿）"
        + (f"；原始文件 `{ctx['plan']}` 只读，改动用 milestone_* / target_* 工具" if ctx["plan"] else ""),
        f"- 证据：`{ctx['evidence']}/`（{ctx['evidence_notes']} 篇：核实记录、来源摘要、Claim Gate）" if ctx["evidence"] else "",
        (f"- 评审材料：`{ctx['packets']}/`（{ctx['packet_notes']} 篇）" if ctx["packets"] else "")
        + (f"；这条线上一份汇报包：`{prev['path']}`（{prev['date']}）" if prev else ""),
    ]
    tag = (f"涉及这条线时 line 字段填「{ctx['configured']}」。" if ctx["configured"]
           else "这条线不在配置的研究线列表里，line 字段可以留空。")
    facts = "\n".join(f"- {f}" for f in _facts(line))
    return f"""
# 你的角色
你是用户 PhD 工作台里研究线「{name}」的专属研究搭档。这是一段长期对话：跨天、跨重启都接着聊，围绕这一条线的进展、证据、风险、实验、投稿和导师汇报。别的研究线和杂事交给工作台助手。
关于用户：{PROFILE.get("about", "")}
- 一律用{lang}回答，先给结论再给依据；简洁，不要客套。
- 今天是 {today.isoformat()}。用户说"下周三""月底"之类，换算成具体日期。
- 不确定就直说；实验数字、发表状态、会议日期以 vault 记录、一手来源或工具返回为准，不要编造。

# 这条研究线（应用读到的摘要，细节去笔记里读）
{note}
{facts}

# 需要时去读（用 Read / Grep 按需读，不要一次全读）
{chr(10).join(r for r in reads if r)}
- vault 根目录就是你的工作目录。

# 工作台工具
studio 工具（reading_* / milestone_* / target_* / idea_* / timeline / venue_search / tracker_latest / follow）直接改工作台数据，立即生效，界面会显示变更卡片。{tag}

# 写入规则
- 只有用户明确要求时才修改 vault 文件；做最小、精准的修改，保持笔记现有语言与结构。
- 每次 Edit / Write 都会以 diff 形式交给用户确认；被拒绝时不要换个方式重试，问用户想怎么改。
- 严格遵守下面的 vault 规则（AGENTS.md）。

<agents_md>
{agents}
</agents_md>
""".strip()


def packet_prompt(line: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """The 「起草导师汇报包」 request: where to write, what to compare against, and the filled template."""
    today = today or date.today()
    ctx = context(line)
    prev = ctx["previous_packet"]
    tpl = template()
    target = packet_path(line, today.isoformat())
    note_name = Path(ctx["note"]).stem if ctx["note"] else ""
    created = str(line.get("created") or "")[:10]
    filled = fill(tpl["text"], {
        "line": ctx["line"], "title": target.stem, "date": today.isoformat(),
        "note": note_name, "note_link": f"[[{note_name}]]" if note_name else "",
        "previous": prev["name"] if prev else "", "previous_link": f"[[{prev['name']}]]" if prev else "",
        "previous_date": prev["date"] if prev else (created or "the start of this line"),
    })
    packets = ctx["packets"] or _rel(packets_dir())
    if prev:
        base = (f"上一份汇报包是 `{prev['path']}`（{prev['date']}）。先读它，「自上次以来的进展」从它之后算，"
                f"frontmatter 的 previous_packet 链到 [[{prev['name']}]]。")
    else:
        base = (f"应用没在 `{packets}/` 里找到这条线的汇报包。先用 Grep 按研究线名字和研究线笔记的 [[链接]] 搜一遍；"
                "确实没有，就当作第一份" + (f"，进展从这条线创建（{created}）算起。" if created else "，进展从这条线开始算起。"))
    sources = (f"读研究线笔记 `{ctx['note']}`（按日期记的进展、Kill Criteria、First Experiment）" if ctx["note"]
               else "这条线没有研究线笔记，先用 Grep 找相关笔记")
    src = f"`{tpl['path']}`" if tpl["path"] else "工作台默认模板"
    prompt = f"""起草研究线「{ctx['line']}」的导师汇报包，写成 vault 里的一篇新笔记：`{_rel(target)}`。用 Write 新建（我会先看差异再决定是否写入），不要改别的文件。

1. 基线：{base}
2. {sources}；用 timeline 看这条线的里程碑和投稿目标，用 reading_list / idea_list 看相关论文和想法；需要时查 `{ctx['evidence'] or '证据文件夹'}` 和路线图。
3. 按下面的模板写，保留它的标题和顺序，把 <!-- --> 里的说明换成内容（说明本身删掉），frontmatter 的 type 用 review-packet：
   - 自上次以来的进展：具体、带日期，链接到对应笔记。
   - 证据：每条结论链接到出处（[[证据笔记]]、[[论文笔记]] 或实验记录）；没有出处的标 unverified，不要编造数字。
   - 风险：逐条对照研究线笔记里的 Kill criteria（K1、K2…），写状态（clear / partially triggered / triggered）、依据和应对。
   - 想请导师给意见的：具体、能回答，最多 5 条。
   - 接下来 2 周：按周写，每周一个可检查的交付物，和 timeline 里的里程碑对齐。
4. 这是给导师看的：不写 agent 的工作过程，遵守 AGENTS.md 的 Review Packets 和 Claim Gate 规则。语言跟上一份汇报包一致；没有上一份就用英文。
5. 写完用两三句话告诉我要点和你最没把握的地方。

模板（{src}）：
<template>
{filled.strip()}
</template>"""
    return {"prompt": prompt, "label": "起草导师汇报包", "path": _rel(target),
            "template": {"source": tpl["source"], "path": tpl["path"]}, "previous": prev}
