"""Read-only tools for the API models (GLM, DeepSeek), as OpenAI-style function tools.

Files: read, list and search inside the vault, the papers' full text and the configured extra read
folders; nothing outside them, nothing hidden (.obsidian, .git, .trash). Workbench data: the
read-only studio queries (reading list, ideas, timeline, venues, the weekly tracker). Nothing here
writes; vault edits stay with Claude behind the diff approval.
"""
from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import i18n
from .config import EXTRA_READ_DIRS, TEXT_DIR, VAULT_DIR

TEXT_SUFFIXES = {".md", ".txt", ".yaml", ".yml", ".json", ".jsonl", ".py", ".tex", ".bib", ".csv", ".toml", ".js", ".ts",
                 ".html", ".css", ".sh", ".ipynb", ".rst", ".org", ".cfg", ".ini"}
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv"}
MAX_CHARS = 60_000
MAX_FILE = 2 * 1024 * 1024
MAX_SCAN = 6_000
RESULT_CHARS = 30_000


class ToolError(Exception):
    pass


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[str]]
    summary: Callable[[dict[str, Any]], str]  # the line the chat shows for a call

    def spec(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}

    async def call(self, args: dict[str, Any]) -> str:
        try:
            out = await self.run(args)
        except ToolError as e:
            return f"错误：{e}"
        except Exception as e:  # noqa: BLE001 - a tool failure is the model's to handle, not the turn's
            return f"错误：{type(e).__name__}: {e}"
        return out if len(out) <= RESULT_CHARS else out[:RESULT_CHARS] + "\n…（结果太长，已截断）"


def roots() -> list[Path]:
    return [VAULT_DIR.resolve(), TEXT_DIR.resolve(), *(Path(d).resolve() for d in EXTRA_READ_DIRS)]


def _hidden(p: Path, root: Path) -> bool:
    return any(part.startswith(".") or part in SKIP_DIRS for part in p.relative_to(root).parts)


def resolve(path: str) -> Path:
    """A path the model gave (vault-relative, or absolute) → a real path inside the readable roots."""
    raw = Path(os.path.expanduser((path or ".").strip()))
    p = (raw if raw.is_absolute() else VAULT_DIR / raw).resolve()
    for root in roots():
        if p.is_relative_to(root):
            if _hidden(p, root):
                raise ToolError("隐藏文件夹不可读")
            return p
    raise ToolError("只能读 vault、论文全文和配置的额外目录里的文件")


def show(p: Path, fulltext: str = "全文：") -> str:
    """A path as shown in tool results (the model reads Chinese labels); the chat's tool line passes `fulltext` in the interface language."""
    for root, prefix in ((VAULT_DIR.resolve(), ""), (TEXT_DIR.resolve(), fulltext)):
        if p.is_relative_to(root):
            return prefix + (str(p.relative_to(root)) if p != root else ".")
    return str(p)


def read_file(path: str, offset: int = 1, limit: int = 400) -> str:
    p = resolve(path)
    if not p.is_file():
        raise ToolError(f"没有这个文件：{path}")
    if p.suffix.lower() not in TEXT_SUFFIXES and p.suffix:
        raise ToolError(f"不是文本文件：{p.suffix}")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(offset or 1))
    limit = max(1, min(int(limit or 400), 2000))
    chunk = lines[start - 1:start - 1 + limit]
    out, size = [], 0
    for i, line in enumerate(chunk, start):
        row = f"{i}\t{line}"
        size += len(row) + 1
        if size > MAX_CHARS:
            out.append(f"…（到第 {i - 1} 行为止，超过 {MAX_CHARS} 字；用 offset={i} 接着读）")
            break
        out.append(row)
    end = start + len(chunk) - 1
    head = f"{show(p)}：第 {start}–{end} 行，共 {len(lines)} 行"
    if end < len(lines):
        head += f"（还没读完，用 offset={end + 1} 接着读）"
    return head + "\n" + "\n".join(out)


def list_dir(path: str = ".") -> str:
    p = resolve(path)
    if not p.is_dir():
        raise ToolError(f"不是文件夹：{path}")
    rows = []
    for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if c.name.startswith(".") or c.name in SKIP_DIRS:
            continue
        rows.append(c.name + ("/" if c.is_dir() else ""))
        if len(rows) >= 300:
            rows.append("…（只列前 300 项）")
            break
    return f"{show(p)}/\n" + "\n".join(rows)


def search_text(pattern: str, path: str = ".", glob: str = "") -> str:
    base = resolve(path)
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    files = [base] if base.is_file() else []
    if base.is_dir():
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
            for name in sorted(filenames):
                f = Path(dirpath) / name
                if name.startswith(".") or f.suffix.lower() not in TEXT_SUFFIXES or (glob and not f.match(glob)):
                    continue
                files.append(f)
                scanned += 1
            if scanned >= MAX_SCAN:
                break
    hits: list[str] = []
    for f in files:
        try:
            if f.stat().st_size > MAX_FILE:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{show(f)}:{i}: {line.strip()[:200]}")
                if len(hits) >= 80:
                    return "\n".join(hits) + "\n…（只列前 80 处）"
    return "\n".join(hits) or "没有找到"


def _int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def file_tools() -> list[Tool]:
    async def _read(a: dict[str, Any]) -> str:
        return await asyncio.to_thread(read_file, str(a.get("path", "")), _int(a.get("offset"), 1), _int(a.get("limit"), 400))

    async def _list(a: dict[str, Any]) -> str:
        return await asyncio.to_thread(list_dir, str(a.get("path") or "."))

    async def _search(a: dict[str, Any]) -> str:
        return await asyncio.to_thread(search_text, str(a.get("pattern", "")), str(a.get("path") or "."), str(a.get("glob") or ""))

    s = {"type": "string"}
    return [
        Tool("read_file", "读一个文本文件（带行号）。path 相对 vault 根目录，或是绝对路径（论文全文、额外目录）。"
             "长文件用 offset/limit 分段读。", {"type": "object", "properties": {
                 "path": s, "offset": {"type": "integer", "description": "从第几行开始，默认 1"},
                 "limit": {"type": "integer", "description": "读多少行，默认 400"}}, "required": ["path"]},
             _read, lambda a: i18n.t("tools.summary.read", path=_shown(a.get("path")))),
        Tool("list_dir", "列出文件夹内容。path 相对 vault 根目录，默认根目录。",
             {"type": "object", "properties": {"path": s}, "required": []},
             _list, lambda a: i18n.t("tools.summary.list", path=_shown(a.get("path") or "."))),
        Tool("search_text", "在文件里搜文本（正则，不区分大小写），返回 文件:行号: 内容。path 默认整个 vault；"
             "glob 可限定文件名，如 *.md。", {"type": "object", "properties": {"pattern": s, "path": s, "glob": s},
                                           "required": ["pattern"]},
             _search, lambda a: i18n.t("tools.summary.search", pattern=str(a.get("pattern", ""))[:40])),
    ]


def _shown(path: Any) -> str:
    """A path for the chat's tool line (the interface language)."""
    try:
        return show(resolve(str(path or ".")), i18n.t("chat.fulltext"))
    except ToolError:
        return str(path)[:60]


def studio_tools() -> list[Tool]:
    """The studio assistant's read-only queries, wrapped from studio_tools."""
    from . import studio_tools as st
    from .claude_chat import studio_tool_label

    async def no_emit(_: dict[str, Any]) -> None:  # read-only tools never report changes
        return None

    out = []
    for t in st.read_only(no_emit):
        async def run(a: dict[str, Any], handler=t.handler) -> str:
            res = await handler(a)
            return "\n".join(c.get("text", "") for c in res.get("content", []))

        schema = t.input_schema if isinstance(t.input_schema, dict) else {"type": "object", "properties": {}}
        out.append(Tool(t.name, t.description, schema, run,
                        lambda a, name=t.name: f"{studio_tool_label(name)} {str(a.get('query') or a.get('status') or '')[:40]}".strip()))
    return out


def for_chat(studio: bool) -> list[Tool]:
    return file_tools() + (studio_tools() if studio else [])
