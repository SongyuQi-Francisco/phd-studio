"""Model providers for the chats: Claude, Codex, Gemini, 智谱 GLM and DeepSeek.

Claude runs through the Agent SDK in claude_chat, and it is the only provider that may write (behind
the diff approval); here it only reports its status and models. The others implement `stream()`:
- Codex and Gemini run through their own CLIs, logged in with the user's ChatGPT / Google plan. The
  CLIs are found by explicit path (the desktop app starts with launchd's short PATH) and run without
  the native stub's PYTHONHOME / PYTHONNOUSERSITE. They are read-only: Codex in its read-only sandbox
  without the user's CLI config (plugins, MCP servers); Gemini headless with the default approval
  mode, which leaves out the tools that write files or run commands.
- GLM and DeepSeek go through their OpenAI-compatible chat APIs with keys from ~/.config/secrets,
  plus the workbench's own read-only tools (ro_tools).

No provider stands in for another: an unavailable provider fails the turn with its own error and
the exact fix (the command to run or the file to write). Login state is read from the CLI or the
key files; passwords are never asked for.

`stream(turn)` yields events: {"type": "thread", "id"}, {"type": "model", "model"},
{"type": "delta", "text"} (a chunk), {"type": "text", "text"} (a whole message),
{"type": "tool", "name", "summary"}, {"type": "thread_lost"} (the CLI no longer has the thread),
{"type": "error", "message"} (ends the turn).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .config import SECRETS_DIR, VAULT_DIR, TEXT_DIR, load_secrets

ENV_DROP = ("PYTHONHOME", "PYTHONNOUSERSITE", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__")
HOME = Path.home()
# Where installers put CLIs; the desktop app's PATH has none of these.
BIN_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", str(HOME / ".local" / "bin"), str(HOME / ".npm-global" / "bin"),
            str(HOME / ".bun" / "bin"), str(HOME / ".volta" / "bin")]
MAX_TOOL_ROUNDS = 12
LINE_LIMIT = 16 * 1024 * 1024  # one JSONL event can carry a whole file a CLI read
# Tests swap in an httpx.MockTransport here.
HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None


def _tilde(p: Path) -> str:
    return str(p).replace(str(HOME), "~", 1)


def find_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in BIN_DIRS:
        p = Path(d) / name
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return None


def child_env(binary: str | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """The CLI's environment: ours without the stub's Python variables, PATH with the install dirs."""
    env = {k: v for k, v in os.environ.items() if k not in ENV_DROP}
    dirs = ([str(Path(binary).parent)] if binary else []) + BIN_DIRS + env.get("PATH", "/usr/bin:/bin").split(os.pathsep)
    env["PATH"] = os.pathsep.join(dict.fromkeys(d for d in dirs if d))
    env.update(extra or {})
    return env


def _state(state: str, detail: str, fix: str = "", cmd: str = "") -> dict[str, Any]:
    """ok | no_cli | no_login | no_key | error, what was found, and how to fix it (cmd: a command to copy)."""
    return {"ok": state == "ok", "state": state, "detail": detail, "fix": fix, "cmd": cmd}


@dataclass
class Turn:
    text: str                     # this turn's message, with its context blocks
    instructions: str             # role and context: the system message, or the head of a CLI thread's first message
    model: str
    cwd: Path = VAULT_DIR
    read_dirs: list[Path] = field(default_factory=list)  # readable besides cwd
    thread: str | None = None     # CLI providers: the thread to continue
    history: list[dict[str, Any]] = field(default_factory=list)  # API providers: this thread's earlier messages
    images: list[Path] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)  # API providers: ro_tools.Tool

    def first_message(self) -> str:
        """A CLI thread gets the instructions once, at its start; resumed turns carry only the message."""
        if self.thread:
            return self.text
        return f"<instructions>\n{self.instructions}\n</instructions>\n\n{self.text}"


class Provider:
    id = ""
    label = ""
    kind = ""  # "sdk" | "cli" | "api"

    def status(self) -> dict[str, Any]:
        raise NotImplementedError

    def models(self) -> list[dict[str, Any]]:
        """[{id, label, vision}], the default first."""
        raise NotImplementedError

    def default_model(self) -> str:
        ms = self.models()
        return ms[0]["id"] if ms else ""

    def vision(self, model: str) -> bool:
        return any(m["id"] == model and m.get("vision") for m in self.models())

    def valid_model(self, model: str) -> bool:
        return model == "" or any(m["id"] == model for m in self.models())

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "kind": self.kind, "models": self.models(),
                "default_model": self.default_model(), **self.status()}

    def stream(self, turn: Turn) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError


# ---------------- Claude (status and models only; its turns run in claude_chat) ----------------


class Claude(Provider):
    id, label, kind = "claude", "Claude", "sdk"

    def status(self) -> dict[str, Any]:
        from .claude_chat import claude_status

        st = claude_status()
        if st["ok"]:
            return _state("ok", f"已连接 · {st['method']}")
        return _state("no_login", "未登录", st.get("hint", ""), "claude setup-token")

    def models(self) -> list[dict[str, Any]]:
        return [{"id": "", "label": "默认（设置里的伴读模型）", "vision": True},
                {"id": "opus", "label": "Opus", "vision": True}, {"id": "sonnet", "label": "Sonnet", "vision": True},
                {"id": "haiku", "label": "Haiku（快）", "vision": True}]


# ---------------- CLI providers ----------------


@dataclass
class Run:
    """What a CLI left besides its events: the last lines of stderr and the exit code."""
    tail: list[str] = field(default_factory=list)
    code: int | None = None

    def detail(self) -> str:
        lines = [t for t in self.tail if "Reading additional input from stdin" not in t]
        return " ".join(lines[-3:])[:400] or f"没有输出（退出码 {self.code}）"


async def run_jsonl(args: list[str], stdin: str, env: dict[str, str], cwd: Path, run: Run) -> AsyncIterator[dict[str, Any]]:
    """Run a CLI that prints JSON lines and yield each event. The process is killed when the turn is
    stopped (the generator is closed)."""
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT)

    async def drain_stderr() -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                run.tail.append(line)
                del run.tail[:-20]

    errs = asyncio.create_task(drain_stderr())
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(stdin.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
        await errs
        run.code = await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        errs.cancel()


def _clean(detail: str) -> str:
    return detail.strip().rstrip("。.")


def _inner_message(msg: str) -> str:
    """CLIs pass the API's JSON error through as a string: pull out its message."""
    try:
        data = json.loads(msg)
    except (json.JSONDecodeError, TypeError):
        return msg
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if data.get("message"):
            return str(data["message"])
    return msg


def _short_cmd(command: str) -> str:
    m = re.match(r"^\S*sh -l?c (['\"])(.*)\1$", command, re.S)
    cmd = m.group(2) if m else command
    return cmd.replace(str(VAULT_DIR) + "/", "").replace(str(TEXT_DIR) + "/", "全文：")[:80]


class Codex(Provider):
    id, label, kind = "codex", "Codex", "cli"

    def __init__(self) -> None:
        self.home = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
        self._models: tuple[float, list[dict[str, Any]]] | None = None

    def binary(self) -> str | None:
        return find_bin("codex")

    def status(self) -> dict[str, Any]:
        b = self.binary()
        if not b:
            return _state("no_cli", "没装 Codex CLI",
                          "安装后在终端运行 codex login，用 ChatGPT 账号登录", "npm install -g @openai/codex")
        try:
            out = subprocess.run([b, "login", "status"], capture_output=True, text=True, timeout=15, env=child_env(b))
        except (OSError, subprocess.TimeoutExpired) as e:
            return _state("error", f"codex 运行不了：{e}", "在终端运行 codex login status 看看", "codex login status")
        text = f"{out.stdout}\n{out.stderr}".strip().lower()
        if out.returncode == 0 and "logged in" in text and "not logged in" not in text:
            how = "ChatGPT 账号" if "chatgpt" in text else "API key"
            return _state("ok", f"已登录（{how}）")
        return _state("no_login", "Codex 没登录", "在终端运行 codex login，用 ChatGPT 账号登录", "codex login")

    def _configured_model(self) -> str:
        try:
            cfg = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return ""
        m = cfg.get("model")
        return m if isinstance(m, str) else ""

    def _catalog(self) -> list[dict[str, Any]]:
        """The models this CLI offers (`codex debug models`), cached a few minutes. Not the shared
        models_cache.json: other Codex clients on the Mac rewrite it with models this CLI can't use."""
        now = time.monotonic()
        if self._models and now - self._models[0] < 300:
            return self._models[1]
        b = self.binary()
        try:
            out = subprocess.run([b, "debug", "models"], capture_output=True, text=True, timeout=15, env=child_env(b)) if b else None
            found = json.loads(out.stdout).get("models") or [] if out and out.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, AttributeError):
            found = []
        found = [m for m in found if isinstance(m, dict) and m.get("slug")]
        if found:
            self._models = (now, found)
        return found

    def models(self) -> list[dict[str, Any]]:
        listed = [{"id": m["slug"], "label": m.get("display_name") or m["slug"],
                   "vision": "image" in (m.get("input_modalities") or ["image"])}
                  for m in self._catalog() if m.get("visibility", "list") == "list"]
        default = self._configured_model()
        if default:
            listed = sorted(listed, key=lambda m: m["id"] != default)
            if not any(m["id"] == default for m in listed):
                listed.insert(0, {"id": default, "label": default, "vision": True})
        return listed or [{"id": "", "label": "Codex 默认", "vision": True}]

    def args(self, turn: Turn, binary: str) -> list[str]:
        # No user config: the read-only sandbox and built-in tools only, without the user's plugins and
        # MCP servers. The vault's AGENTS.md is already in the instructions (project docs off).
        args = [binary, "exec", "--json", "--skip-git-repo-check", "-s", "read-only", "--ignore-user-config",
                "--ignore-rules", "-c", "project_doc_max_bytes=0", "-C", str(turn.cwd)]
        if turn.model:
            args += ["-m", turn.model]
        if turn.thread:
            args.append("resume")
        for img in turn.images:
            args += ["--image", str(img)]
        args.append("--")
        if turn.thread:
            args.append(turn.thread)
        return args + ["-"]

    def friendly(self, detail: str) -> str:
        detail = _clean(detail)
        low = detail.lower()
        if "no rollout found" in low or "thread/resume failed" in low:
            return (f"Codex 里找不到这个对话之前的线程（{detail}）。旧线程已清掉，再发一次会用 Codex 开新线程，"
                    "看不到之前的内容。")
        if any(k in low for k in ("not logged in", "unauthorized", "401", "please log in", "authentication", "token expired")):
            return f"Codex 未登录或登录已过期：在终端运行 codex login 后重新发送。（{detail}）"
        if any(k in low for k in ("usage limit", "rate limit", "429", "quota")):
            return f"Codex 用量达到上限（ChatGPT 套餐额度），稍后再试。（{detail}）"
        if "model" in low and any(k in low for k in ("not supported", "not found", "does not exist")):
            return f"Codex 用不了这个模型：{detail}。在面板顶部换一个 Codex 模型。"
        return f"Codex 出错：{detail}"

    async def stream(self, turn: Turn) -> AsyncIterator[dict[str, Any]]:
        b = self.binary()
        if not b:
            st = self.status()
            yield {"type": "error", "message": f"Codex 不可用：{st['detail']}。{st['fix']}"}
            return
        yield {"type": "model", "model": turn.model or "默认模型"}
        run = Run()
        seen: set[str] = set()
        done = failed = False
        last_error = ""
        async for ev in run_jsonl(self.args(turn, b), turn.first_message(), child_env(b), turn.cwd, run):
            kind = ev.get("type")
            item = ev.get("item") or {}
            if kind == "thread.started" and ev.get("thread_id"):
                yield {"type": "thread", "id": ev["thread_id"]}
            elif kind in ("item.started", "item.completed"):
                it = item.get("type")
                if it == "agent_message" and kind == "item.completed" and (item.get("text") or "").strip():
                    yield {"type": "text", "text": item["text"].strip()}
                elif it in ("command_execution", "web_search", "mcp_tool_call") and item.get("id") not in seen:
                    seen.add(item.get("id"))
                    what = (_short_cmd(item.get("command", "")) if it == "command_execution"
                            else item.get("query") or item.get("tool") or "")
                    yield {"type": "tool", "name": it, "summary": f"{'命令' if it == 'command_execution' else it} {what}".strip()}
            elif kind == "error":
                last_error = _inner_message(ev.get("message", ""))
            elif kind == "turn.failed":
                failed = True
                yield {"type": "error", "message": self.friendly(_inner_message((ev.get("error") or {}).get("message", "")) or last_error)}
            elif kind == "turn.completed":
                done = True
        if not done and not failed:
            detail = last_error or run.detail()
            if turn.thread and "no rollout found" in detail.lower():
                yield {"type": "thread_lost"}
            yield {"type": "error", "message": self.friendly(detail)}


class Gemini(Provider):
    id, label, kind = "gemini", "Gemini", "cli"

    def __init__(self) -> None:
        self.home = HOME / ".gemini"

    def binary(self) -> str | None:
        return find_bin("gemini")

    def _api_key(self) -> str:
        return os.environ.get("GEMINI_API_KEY") or load_secrets("gemini").get("GEMINI_API_KEY", "")

    def status(self) -> dict[str, Any]:
        if not self.binary():
            return _state("no_cli", "没装 Gemini CLI",
                          "安装后在终端运行 gemini，选 Login with Google 登录一次", "brew install gemini-cli")
        if self._api_key():
            return _state("ok", "已配置 API key（gemini.env）")
        if (self.home / "oauth_creds.json").exists():
            return _state("ok", "已登录（Google 账号）")
        return _state("no_login", "Gemini CLI 没登录",
                      f"在终端运行 gemini，选 Login with Google 登录一次（或把 GEMINI_API_KEY=… 写进 {_tilde(SECRETS_DIR / 'gemini.env')}）",
                      "gemini")

    def models(self) -> list[dict[str, Any]]:
        return [{"id": "auto", "label": "自动（Gemini CLI 选）", "vision": True},
                {"id": "pro", "label": "Pro", "vision": True}, {"id": "flash", "label": "Flash", "vision": True},
                {"id": "flash-lite", "label": "Flash-Lite（快）", "vision": True}]

    def args(self, turn: Turn, binary: str) -> list[str]:
        args = [binary, "--output-format", "stream-json", "--model", turn.model or "auto"]
        dirs = [str(d) for d in turn.read_dirs if Path(d).exists()]
        if dirs:
            args += ["--include-directories", ",".join(dirs)]
        if turn.thread:
            args += ["--resume", turn.thread]
        return args  # the message comes on stdin, which also makes the CLI headless

    def message(self, turn: Turn) -> str:
        text = turn.first_message()
        if turn.images:  # @path attaches a file; spaces in the path are escaped
            text += "\n\n" + "\n".join("@" + str(p).replace(" ", "\\ ") for p in turn.images)
        return text

    def friendly(self, detail: str) -> str:
        detail = _clean(detail)
        low = detail.lower()
        if any(k in low for k in ("auth method", "login", "credential", "unauthenticated", "401", "authentication")):
            return f"Gemini 未登录或登录已过期：在终端运行 gemini 重新登录后再发。（{detail}）"
        if any(k in low for k in ("quota", "429", "resource_exhausted", "rate limit")):
            return f"Gemini 用量达到上限，稍后再试。（{detail}）"
        if "model" in low and any(k in low for k in ("not found", "not supported", "invalid")):
            return f"Gemini 用不了这个模型：{detail}。在面板顶部换一个 Gemini 模型。"
        return f"Gemini 出错：{detail}"

    async def stream(self, turn: Turn) -> AsyncIterator[dict[str, Any]]:
        b = self.binary()
        if not b:
            st = self.status()
            yield {"type": "error", "message": f"Gemini 不可用：{st['detail']}。{st['fix']}"}
            return
        key = self._api_key()
        env = child_env(b, {"GEMINI_API_KEY": key} if key else None)
        run = Run()
        done = failed = False
        last_error = ""
        async for ev in run_jsonl(self.args(turn, b), self.message(turn), env, turn.cwd, run):
            kind = ev.get("type")
            if kind == "init":
                if ev.get("session_id"):
                    yield {"type": "thread", "id": ev["session_id"]}
                if ev.get("model"):
                    yield {"type": "model", "model": ev["model"]}
            elif kind == "message" and ev.get("role") == "assistant" and ev.get("content"):
                yield {"type": "delta" if ev.get("delta") else "text", "text": ev["content"]}
            elif kind == "tool_use":
                p = ev.get("parameters") or {}
                what = next((str(p[k]) for k in ("file_path", "absolute_path", "path", "dir_path", "pattern", "query", "url")
                             if p.get(k)), "")
                what = what.replace(str(VAULT_DIR) + "/", "").replace(str(TEXT_DIR) + "/", "全文：")
                yield {"type": "tool", "name": ev.get("tool_name", "tool"), "summary": f"{ev.get('tool_name', '')} {what[:80]}".strip()}
            elif kind == "error" and ev.get("severity") == "error":
                last_error = ev.get("message", "")
            elif kind == "result":
                if ev.get("status") == "success":
                    done = True
                else:
                    failed = True
                    yield {"type": "error", "message": self.friendly((ev.get("error") or {}).get("message") or last_error or "失败")}
        if not done and not failed:
            yield {"type": "error", "message": self.friendly(last_error or run.detail())}


# ---------------- API providers (OpenAI-compatible chat completions) ----------------


class ApiProvider(Provider):
    kind = "api"
    key_file = ""
    key_names: tuple[str, ...] = ()
    base_url = ""
    base_env = ""

    def _secrets(self) -> dict[str, str]:
        return load_secrets(self.key_file)

    def key(self) -> str:
        found = self._secrets()
        return next((os.environ.get(k) or found.get(k, "") for k in self.key_names if os.environ.get(k) or found.get(k)), "")

    def base(self) -> str:
        return (self._secrets().get(self.base_env) or self.base_url).rstrip("/")

    def status(self) -> dict[str, Any]:
        where = _tilde(SECRETS_DIR / f"{self.key_file}.env")
        if self.key():
            return _state("ok", f"已配置 API Key（{self.key_file}.env）")
        return _state("no_key", f"缺 {self.label} API Key", f"把 {self.key_names[0]}=… 写进 {where}")

    def friendly(self, status: int, body: str, model: str) -> str:
        try:
            err = json.loads(body).get("error") or {}
            detail = str(err.get("message") or err) if isinstance(err, dict) else str(err)
        except (json.JSONDecodeError, AttributeError):
            detail = body.strip()[:300]
        detail = _clean(detail) or f"HTTP {status}"
        where = _tilde(SECRETS_DIR / f"{self.key_file}.env")
        if status in (401, 403):
            return f"{self.label} API Key 无效或没有权限：检查 {where} 里的 {self.key_names[0]}。（{detail}）"
        if status == 402 or "余额" in detail or "balance" in detail.lower():
            return f"{self.label} 账户余额不足。（{detail}）"
        if status == 429:
            return f"{self.label} 限流或额度用完，稍后再试。（{detail}）"
        if "model" in detail.lower() or "模型" in detail:
            return f"{self.label} 用不了模型 {model}：{detail}。在面板顶部换一个模型。"
        if status >= 500:
            return f"{self.label} 服务暂时出错（HTTP {status}），稍后再试。（{detail}）"
        return f"{self.label} 出错（HTTP {status}）：{detail}"

    def _user_content(self, turn: Turn) -> Any:
        if not turn.images:
            return turn.text
        parts: list[dict[str, Any]] = [{"type": "text", "text": turn.text}]
        for p in turn.images:
            data = base64.b64encode(p.read_bytes()).decode()
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
        return parts

    async def stream(self, turn: Turn) -> AsyncIterator[dict[str, Any]]:
        key = self.key()
        if not key:
            st = self.status()
            yield {"type": "error", "message": f"{self.label} 不可用：{st['detail']}。{st['fix']}"}
            return
        messages: list[dict[str, Any]] = [{"role": "system", "content": turn.instructions}, *turn.history,
                                          {"role": "user", "content": self._user_content(turn)}]
        tools = {t.name: t for t in turn.tools}
        specs = [t.spec() for t in turn.tools]
        reported = turn.model
        yield {"type": "model", "model": turn.model}
        try:
            async with httpx.AsyncClient(transport=HTTP_TRANSPORT, timeout=httpx.Timeout(180, connect=15)) as client:
                for _ in range(MAX_TOOL_ROUNDS):
                    body: dict[str, Any] = {"model": turn.model, "messages": messages, "stream": True}
                    if specs:
                        body["tools"] = specs
                    text, reasoning, calls = "", "", {}
                    async with client.stream("POST", f"{self.base()}/chat/completions", json=body,
                                             headers={"Authorization": f"Bearer {key}"}) as r:
                        if r.status_code >= 400:
                            raw = (await r.aread()).decode("utf-8", "replace")
                            yield {"type": "error", "message": self.friendly(r.status_code, raw, turn.model)}
                            return
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            if chunk.get("error"):
                                yield {"type": "error", "message": self.friendly(200, json.dumps(chunk), turn.model)}
                                return
                            if chunk.get("model") and chunk["model"] != reported:
                                reported = chunk["model"]
                                yield {"type": "model", "model": reported}
                            for choice in chunk.get("choices") or []:
                                delta = choice.get("delta") or {}
                                if delta.get("content"):
                                    text += delta["content"]
                                    yield {"type": "delta", "text": delta["content"]}
                                reasoning += delta.get("reasoning_content") or ""
                                for tc in delta.get("tool_calls") or []:
                                    slot = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "args": ""})
                                    fn = tc.get("function") or {}
                                    slot["id"] = slot["id"] or tc.get("id") or ""
                                    slot["name"] = slot["name"] or fn.get("name") or ""
                                    slot["args"] += fn.get("arguments") or ""
                    if not calls:
                        return
                    ordered = [calls[i] for i in sorted(calls)]
                    for i, c in enumerate(ordered):
                        c["id"] = c["id"] or f"call_{i}"
                    asked: dict[str, Any] = {"role": "assistant", "content": text or None, "tool_calls": [
                        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                        for c in ordered]}
                    if reasoning:  # thinking models want their reasoning back within the same turn
                        asked["reasoning_content"] = reasoning
                    messages.append(asked)
                    for c in ordered:
                        tool = tools.get(c["name"])
                        try:
                            args = json.loads(c["args"] or "{}")
                        except json.JSONDecodeError:
                            args = None
                        if tool is None or not isinstance(args, dict):
                            result = f"错误：没有工具 {c['name']}，或参数不是 JSON 对象。"
                            yield {"type": "tool", "name": c["name"], "summary": c["name"]}
                        else:
                            yield {"type": "tool", "name": c["name"], "summary": tool.summary(args)}
                            result = await tool.call(args)
                        messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                yield {"type": "error", "message": f"{self.label} 连续调用工具超过 {MAX_TOOL_ROUNDS} 轮，已停下。换个问法再试。"}
        except httpx.HTTPError as e:
            yield {"type": "error", "message": f"连不上 {self.label}：{type(e).__name__} {e}"}


class GLM(ApiProvider):
    id, label = "glm", "智谱 GLM"
    key_file = "zhipu"
    key_names = ("ZHIPU_API_KEY", "ZHIPUAI_API_KEY", "GLM_API_KEY")
    base_url = "https://open.bigmodel.cn/api/paas/v4"
    base_env = "ZHIPU_BASE_URL"  # e.g. the international endpoint

    def models(self) -> list[dict[str, Any]]:
        return [{"id": "glm-5.1", "label": "GLM-5.1"}, {"id": "glm-5.3", "label": "GLM-5.3"},
                {"id": "glm-4.7", "label": "GLM-4.7"}, {"id": "glm-4.7-flash", "label": "GLM-4.7-Flash（免费）"},
                {"id": "glm-5v-turbo", "label": "GLM-5V-Turbo（看图）", "vision": True},
                {"id": "glm-4.6v", "label": "GLM-4.6V（看图）", "vision": True}]

    def translate_model(self) -> str:
        return self._secrets().get("ZHIPU_TRANSLATE_MODEL") or "glm-4.7-flash"


class DeepSeek(ApiProvider):
    id, label = "deepseek", "DeepSeek"
    key_file = "deepseek"
    key_names = ("DEEPSEEK_API_KEY",)
    base_url = "https://api.deepseek.com"
    base_env = "DEEPSEEK_BASE_URL"

    def models(self) -> list[dict[str, Any]]:
        return [{"id": "deepseek-chat", "label": "DeepSeek 对话"}, {"id": "deepseek-reasoner", "label": "DeepSeek 推理"}]


PROVIDERS: dict[str, Provider] = {p.id: p for p in (Claude(), Codex(), Gemini(), GLM(), DeepSeek())}


def get(pid: str) -> Provider | None:
    return PROVIDERS.get(pid)


def describe_all() -> list[dict[str, Any]]:
    return [p.describe() for p in PROVIDERS.values()]


def display(pid: str, model: str) -> str:
    """How an answer or a divider names its model: 'Codex · gpt-x', 'Claude · 默认模型'."""
    p = get(pid)
    label = p.label if p else pid
    return f"{label} · {model or '默认模型'}"
