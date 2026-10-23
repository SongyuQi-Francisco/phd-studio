"""Per-paper Claude co-reader sessions over the Claude Agent SDK.

One long-lived ClaudeSDKClient per open paper, resumed from the saved session id,
so the conversation keeps its context across turns and server restarts.
Read-only tools run freely; Edit/Write are forwarded to the browser as a diff and
only run after the user approves. No silent fallback to another model: if Claude
is unavailable the UI says so.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)
from fastapi import WebSocket

from . import library
from .config import (
    AGENTS_MD,
    CHATS_DIR,
    CLAUDE_CLI,
    CROPS_DIR,
    EXTRA_READ_DIRS,
    PROFILE,
    STATE_DIR,
    TEXT_DIR,
    VAULT_DIR,
    load_secrets,
    load_settings,
)

SESSIONS_FILE = STATE_DIR / "sessions.json"
READ_TOOLS = ["Read", "Glob", "Grep", "WebFetch", "WebSearch"]
WRITE_TOOLS = {"Edit", "Write", "MultiEdit"}
IDLE_SECONDS = 30 * 60


def _claude_env() -> dict[str, str]:
    return {k: v for k, v in load_secrets("claude").items() if k == "CLAUDE_CODE_OAUTH_TOKEN"}


def claude_status() -> dict[str, Any]:
    env = _claude_env()
    if env:
        return {"ok": True, "method": "长期令牌 (claude.env)"}
    try:
        out = subprocess.run(
            [str(CLAUDE_CLI), "auth", "status"], capture_output=True, text=True, timeout=15
        )
        st = json.loads(out.stdout or "{}")
        if st.get("loggedIn"):
            return {"ok": True, "method": st.get("authMethod", "login")}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return {"ok": False, "method": "", "hint": "运行 claude setup-token 并存入 ~/.config/secrets/claude.env"}


def _load_sessions() -> dict[str, str]:
    try:
        return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_session(stem: str, session_id: str | None) -> None:
    s = _load_sessions()
    if session_id:
        s[stem] = session_id
    else:
        s.pop(stem, None)
    SESSIONS_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")


def _log_path(stem: str) -> Path:
    return CHATS_DIR / f"{stem}.jsonl"


def history(stem: str) -> list[dict[str, Any]]:
    p = _log_path(stem)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_log(stem: str, entry: dict[str, Any]) -> None:
    with _log_path(stem).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _system_prompt(stem: str) -> str:
    paper = library.get_paper(stem)
    pdf = library.local_pdf(stem)
    try:
        text = library.text_file(stem)
    except FileNotFoundError:
        text = None
    agents = AGENTS_MD.read_text(encoding="utf-8") if AGENTS_MD.exists() else ""
    notes = "、".join(f"`{n}`" for n in PROFILE.get("research_notes", [])) or "vault 里的路线图和 idea 笔记"
    lang = PROFILE.get("answer_language", "中文")
    return f"""
# 你的角色
你是用户的论文伴读与科研搭档，运行在用户本地的 PhD 工作台里。
关于用户：{PROFILE.get("about", "")}
- 一律用{lang}回答，先给结论，再给依据；简洁，不要客套和重复问题。
- 专业术语第一次出现写成“中文（English）”；公式用 $...$ 或 $$...$$。
- 引用论文内容时标注页码，如 [p.5]，用户点击可跳转。
- 不确定就直说，不要编造实验数字、会议或代码是否开源。

# 当前论文
- 笔记：{paper["note_rel"]}（Obsidian vault 根目录即你的工作目录）
- 标题：{paper["title"]}
- PDF：{pdf or "尚未下载"}
- 全文文本（带 <!-- page N --> 标记，需要时用 Read 读取，可按页定位）：{text or "暂无"}

# 用户的课题
需要联系用户课题时，先读 {notes}。用户当前在做：{PROFILE.get("current_focus", "（未配置）")}。

# 写入规则
- 只有用户明确要求时才修改文件；只改 vault 内的文件；做最小、精准的修改，保持笔记现有语言与结构。
- 每次 Edit/Write 都会以 diff 形式交给用户确认，被拒绝时不要换个方式重试，询问用户想怎么改。
- 严格遵守下面的 vault 规则（AGENTS.md）。

<agents_md>
{agents}
</agents_md>
""".strip()


def _diff_preview(tool: str, inp: dict[str, Any]) -> tuple[str, str]:
    path = Path(inp.get("file_path", ""))
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    new = old
    if tool == "Write":
        new = inp.get("content", "")
    else:
        edits = inp.get("edits") or [inp]
        for e in edits:
            a, b = e.get("old_string", ""), e.get("new_string", "")
            new = new.replace(a, b) if e.get("replace_all") else new.replace(a, b, 1)
    rel = str(path.relative_to(VAULT_DIR)) if path.is_relative_to(VAULT_DIR) else str(path)
    diff = "\n".join(
        difflib.unified_diff(old.splitlines(), new.splitlines(), "当前", "修改后", n=2, lineterm="")
    )
    return rel, diff


def _friendly_error(err: Any, content: list[Any] | None = None) -> str:
    detail = " ".join(b.text for b in content or [] if isinstance(b, TextBlock)) or str(err)
    if "auth" in str(err).lower() or "auth" in detail.lower() or "login" in detail.lower():
        return ("Claude 未登录或登录已过期。在终端运行 claude setup-token，把令牌存进 "
                "~/.config/secrets/claude.env，然后重新发送。")
    if "rate" in str(err).lower():
        return f"Claude 用量达到上限，稍后再试。（{detail}）"
    return f"Claude 出错：{detail}"


def _tool_summary(block: ToolUseBlock) -> str:
    inp = block.input or {}
    target = inp.get("file_path") or inp.get("pattern") or inp.get("url") or inp.get("query") or ""
    target = str(target).replace(str(VAULT_DIR) + "/", "").replace(str(TEXT_DIR) + "/", "全文：")
    return f"{block.name} {target}".strip()


class PaperChat:
    def __init__(self, stem: str):
        self.stem = stem
        self.client: ClaudeSDKClient | None = None
        self.ws: WebSocket | None = None
        self.lock = asyncio.Lock()
        self.pending: dict[str, asyncio.Future[bool]] = {}
        self.last_used = time.time()
        self.task: asyncio.Task | None = None

    async def emit(self, payload: dict[str, Any]) -> None:
        if self.ws is not None:
            try:
                await self.ws.send_json(payload)
            except Exception:  # noqa: BLE001 - socket gone; the turn still completes and is logged
                self.ws = None

    async def _can_use_tool(
        self, tool: str, inp: dict[str, Any], ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        if tool in READ_TOOLS:
            return PermissionResultAllow()
        if tool not in WRITE_TOOLS:
            return PermissionResultDeny(message=f"工作台不允许 {tool}，只能读文件和改 vault 笔记。")
        path = Path(inp.get("file_path", "")).resolve()
        if not path.is_relative_to(VAULT_DIR.resolve()):
            return PermissionResultDeny(message="只能修改 Obsidian vault 内的文件。")
        if self.ws is None:
            return PermissionResultDeny(message="用户不在线，无法确认修改。")
        rel, diff = _diff_preview(tool, inp)
        pid = uuid.uuid4().hex[:8]
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.pending[pid] = fut
        await self.emit({"type": "permission", "id": pid, "tool": tool, "path": rel, "diff": diff,
                         "is_new": not path.exists()})
        try:
            ok = await asyncio.wait_for(fut, timeout=30 * 60)
        except asyncio.TimeoutError:
            ok = False
        finally:
            self.pending.pop(pid, None)
        await self.emit({"type": "permission_resolved", "id": pid, "allowed": ok})
        if ok:
            return PermissionResultAllow()
        return PermissionResultDeny(message="用户拒绝了这次修改。")

    async def _ensure_client(self) -> ClaudeSDKClient:
        if self.client is not None:
            return self.client
        settings = load_settings()
        opts = ClaudeAgentOptions(
            cwd=str(VAULT_DIR),
            add_dirs=[str(TEXT_DIR), str(CROPS_DIR), *EXTRA_READ_DIRS],
            tools=READ_TOOLS + sorted(WRITE_TOOLS),
            allowed_tools=READ_TOOLS,
            can_use_tool=self._can_use_tool,
            system_prompt={"type": "preset", "preset": "claude_code", "append": _system_prompt(self.stem)},
            setting_sources=[],
            include_partial_messages=True,
            resume=_load_sessions().get(self.stem),
            model=settings.get("claude_model") or None,
            env=_claude_env(),
            cli_path=str(CLAUDE_CLI) if CLAUDE_CLI.exists() else None,
        )
        client = ClaudeSDKClient(options=opts)
        await client.connect()
        self.client = client
        return client

    async def close(self) -> None:
        for fut in self.pending.values():
            if not fut.done():
                fut.set_result(False)
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.client = None

    async def reset(self) -> None:
        await self.close()
        _save_session(self.stem, None)
        log = _log_path(self.stem)
        if log.exists():
            log.rename(log.with_name(f"{self.stem}.{int(time.time())}.archived.jsonl"))

    def answer_permission(self, pid: str, allow: bool) -> None:
        fut = self.pending.get(pid)
        if fut and not fut.done():
            fut.set_result(allow)

    async def interrupt(self) -> None:
        if self.client is not None:
            await self.client.interrupt()

    async def send(self, text: str, page: int | None, selection: str | None,
                   images: list[str], include_page_text: bool, display: str | None = None) -> None:
        async with self.lock:
            self.last_used = time.time()
            context = []
            if page:
                context.append(f"[用户正在看第 {page} 页]")
            if include_page_text and page:
                context.append(f"<page_text page=\"{page}\">\n{library.page_text(self.stem, page)}\n</page_text>")
            if selection:
                context.append(f"<selected_text page=\"{page or '?'}\">\n{selection}\n</selected_text>")
            full_text = "\n".join(context + [text])
            content: list[dict[str, Any]] = []
            for name in images:
                img = (CROPS_DIR / Path(name).name)
                if img.exists():
                    content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                    "data": base64.b64encode(img.read_bytes()).decode()}})
            content.append({"type": "text", "text": full_text})
            _append_log(self.stem, {"role": "user", "text": display or text, "selection": selection, "page": page,
                                    "images": images, "ts": time.time()})

            segments: list[dict[str, Any]] = []
            model = ""
            try:
                client = await self._ensure_client()

                async def one_message():
                    yield {"type": "user", "message": {"role": "user", "content": content},
                           "parent_tool_use_id": None}

                await client.query(one_message())
                await self.emit({"type": "start"})
                async for msg in client.receive_response():
                    if isinstance(msg, StreamEvent):
                        ev = msg.event
                        if ev.get("type") == "content_block_delta" and msg.parent_tool_use_id is None:
                            delta = ev.get("delta", {})
                            if delta.get("type") == "text_delta":
                                await self.emit({"type": "delta", "text": delta.get("text", "")})
                    elif isinstance(msg, AssistantMessage):
                        if msg.error:
                            text = _friendly_error(msg.error, msg.content)
                            segments.append({"type": "error", "text": text})
                            await self.emit({"type": "error", "message": text})
                            continue
                        if msg.model and not msg.model.startswith("<"):
                            model = msg.model
                        for block in msg.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                segments.append({"type": "text", "text": block.text})
                            elif isinstance(block, ToolUseBlock):
                                seg = {"type": "tool", "name": block.name, "summary": _tool_summary(block)}
                                segments.append(seg)
                                await self.emit({"type": "tool", **seg})
                    elif isinstance(msg, ResultMessage):
                        if msg.session_id:
                            _save_session(self.stem, msg.session_id)
                        done = {"type": "done", "model": model, "duration_ms": msg.duration_ms,
                                "segments": segments, "is_error": msg.is_error}
                        if msg.is_error and not any(x["type"] == "error" for x in segments):
                            err = _friendly_error(msg.result or (msg.errors and str(msg.errors)) or msg.subtype)
                            segments.append({"type": "error", "text": err})
                        await self.emit(done)
            except Exception as e:  # noqa: BLE001 - report to UI, never fall back silently
                await self.close()
                msg_text = _friendly_error(str(e))
                await self.emit({"type": "error", "message": msg_text})
                segments.append({"type": "error", "text": msg_text})
            _append_log(self.stem, {"role": "assistant", "segments": segments, "model": model,
                                    "ts": time.time()})
            self.last_used = time.time()


class ChatRegistry:
    def __init__(self) -> None:
        self.chats: dict[str, PaperChat] = {}

    def get(self, stem: str) -> PaperChat:
        library.note_path(stem)  # validates the stem
        if stem not in self.chats:
            self.chats[stem] = PaperChat(stem)
        return self.chats[stem]

    async def reap_idle(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for chat in list(self.chats.values()):
                if chat.client and not chat.lock.locked() and now - chat.last_used > IDLE_SECONDS:
                    await chat.close()

    async def close_all(self) -> None:
        for chat in self.chats.values():
            await chat.close()


registry = ChatRegistry()
