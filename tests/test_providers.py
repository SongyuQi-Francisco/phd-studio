"""Other models in the chats (#15): providers, read-only tools, and how a conversation is bound to one.

Nothing here runs a real model: the CLIs are a shell script that replays recorded JSON lines, the
APIs an httpx.MockTransport, and the chat-level tests use a fake provider.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import claude_chat, config, model_chat, providers, ro_tools, translate

# ---------------- helpers ----------------


def run(gen) -> list[dict[str, Any]]:
    async def collect():
        return [e async for e in gen]
    return asyncio.run(collect())


def turn(**kw) -> providers.Turn:
    base = {"text": "问题", "instructions": "INSTR", "model": "m-1", "cwd": config.VAULT_DIR}
    return providers.Turn(**{**base, **kw})


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """A stand-in CLI: records its args and stdin, prints FAKE_OUT, writes FAKE_ERR to stderr, exits FAKE_EXIT."""
    script = tmp_path / "bin" / "fakecli"
    script.parent.mkdir()
    script.write_text('#!/bin/sh\ncat > "$FAKE_STDIN"\nprintf \'%s\\n\' "$@" > "$FAKE_ARGS"\npwd > "$FAKE_CWD"\n'
                      'cat "$FAKE_OUT"\nprintf \'%s\\n\' "$FAKE_ERR" >&2\nexit "${FAKE_EXIT:-0}"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "out.jsonl"
    out.write_text("")
    monkeypatch.setenv("FAKE_STDIN", str(tmp_path / "stdin.txt"))
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args.txt"))
    monkeypatch.setenv("FAKE_CWD", str(tmp_path / "cwd.txt"))
    monkeypatch.setenv("FAKE_OUT", str(out))
    monkeypatch.setenv("FAKE_ERR", "")
    monkeypatch.setattr(providers, "find_bin", lambda name: str(script))

    class Cli:
        def events(self, *evs: dict[str, Any]) -> None:
            out.write_text("".join(json.dumps(e) + "\n" for e in evs))

        def args(self) -> list[str]:
            return (tmp_path / "args.txt").read_text().splitlines()

        def stdin(self) -> str:
            return (tmp_path / "stdin.txt").read_text()

        def cwd(self) -> Path:
            return Path((tmp_path / "cwd.txt").read_text().strip()).resolve()

    return Cli()


def write_secret(name: str, text: str) -> None:
    config.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    (config.SECRETS_DIR / f"{name}.env").write_text(text, encoding="utf-8")


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    """No key from the environment, and an empty secrets folder for every test."""
    for k in ("DEEPSEEK_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY", "GLM_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for f in config.SECRETS_DIR.glob("*.env") if config.SECRETS_DIR.exists() else []:
        f.unlink()


# ---------------- Codex ----------------


def test_codex_streams_messages_tools_and_the_thread(fake_cli):
    fake_cli.events(
        {"type": "thread.started", "thread_id": "th-1"},
        {"type": "item.completed", "item": {"id": "w", "type": "error", "message": "Under-development features enabled"}},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "i0", "type": "agent_message", "text": "先看看。"}},
        {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "/bin/zsh -lc 'ls'"}},
        {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "/bin/zsh -lc 'ls'"}},
        {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "有三个文件夹。"}},
        {"type": "turn.completed", "usage": {}},
    )
    evs = run(providers.PROVIDERS["codex"].stream(turn(model="gpt-x")))
    assert [e["type"] for e in evs] == ["model", "thread", "text", "tool", "text"]
    assert evs[0]["model"] == "gpt-x" and evs[1]["id"] == "th-1" and evs[3]["summary"] == "命令 ls"
    args = fake_cli.args()
    # read-only sandbox, none of the user's CLI config (plugins, MCP), the model asked for, message on stdin
    assert args[:2] == ["exec", "--json"] and "read-only" in args and "--ignore-user-config" in args
    assert args[args.index("-m") + 1] == "gpt-x" and args[-2:] == ["--", "-"] and "resume" not in args
    assert fake_cli.stdin().startswith("<instructions>\nINSTR\n</instructions>") and fake_cli.stdin().endswith("问题")


def test_codex_resumes_its_thread_with_images_and_only_the_message(fake_cli, tmp_path):
    img = tmp_path / "crop one.png"
    img.write_bytes(b"png")
    fake_cli.events({"type": "turn.completed"})
    run(providers.PROVIDERS["codex"].stream(turn(thread="th-1", images=[img])))
    args = fake_cli.args()
    assert args[args.index("resume"):] == ["resume", "--image", str(img), "--", "th-1", "-"]
    assert fake_cli.stdin() == "问题"  # the instructions went in with the thread's first message


def test_codex_failure_is_its_own_error(fake_cli):
    inner = json.dumps({"type": "error", "status": 400, "error": {
        "message": "The 'gpt-z' model is not supported when using Codex with a ChatGPT account."}})
    fake_cli.events({"type": "thread.started", "thread_id": "t"}, {"type": "error", "message": inner},
                    {"type": "turn.failed", "error": {"message": inner}})
    evs = run(providers.PROVIDERS["codex"].stream(turn(model="gpt-z")))
    err = evs[-1]
    assert err["type"] == "error" and err["message"].startswith("Codex 用不了这个模型：The 'gpt-z' model is not supported")
    assert "。。" not in err["message"] and ".。" not in err["message"]


def test_codex_that_dies_reports_stderr(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_ERR", "Error: Not logged in. Please run codex login")
    monkeypatch.setenv("FAKE_EXIT", "1")
    evs = run(providers.PROVIDERS["codex"].stream(turn()))
    assert evs[-1]["type"] == "error" and "codex login" in evs[-1]["message"] and "未登录" in evs[-1]["message"]


def test_a_codex_thread_that_is_gone_is_dropped(fake_cli, monkeypatch, no_claude):
    monkeypatch.setenv("FAKE_ERR", "Error: thread/resume: thread/resume failed: no rollout found for thread id abc (code -32600)")
    monkeypatch.setenv("FAKE_EXIT", "1")
    monkeypatch.setattr(providers.Codex, "status", lambda self: providers._state("ok", "已登录"))
    monkeypatch.setattr(providers.Codex, "models", lambda self: [{"id": "gpt-x", "label": "X", "vision": True}])
    model_chat.save("studio", "codex", "gpt-x", "abc")
    chat = chat_for()
    sent = send(chat)
    assert sent[-2]["type"] == "error" and "找不到这个对话之前的线程" in sent[-2]["message"]
    assert model_chat.binding("studio")["thread"] is None and fake_cli.args()[-2:] == ["abc", "-"]


def test_codex_status_and_models(fake_cli, monkeypatch, tmp_path):
    codex = providers.Codex()
    monkeypatch.setattr(codex, "home", tmp_path / "codex-home")
    (tmp_path / "codex-home").mkdir()
    (tmp_path / "codex-home" / "config.toml").write_text('model = "gpt-b"\n')
    out = Path(os.environ["FAKE_OUT"])
    # `codex debug models`: the CLI's own catalog (kept a few minutes)
    out.write_text(json.dumps({"models": [
        {"slug": "gpt-a", "display_name": "GPT-A", "visibility": "list", "input_modalities": ["text", "image"]},
        {"slug": "gpt-b", "display_name": "GPT-B", "visibility": "list", "input_modalities": ["text"]},
        {"slug": "hidden", "visibility": "hide"}]}))
    assert [m["id"] for m in codex.models()] == ["gpt-b", "gpt-a"]  # the configured default first
    assert codex.default_model() == "gpt-b" and codex.vision("gpt-a") and not codex.vision("gpt-b")
    assert codex.valid_model("gpt-a") and not codex.valid_model("hidden")
    assert fake_cli.args() == ["debug", "models"]
    # `codex login status`
    out.write_text("Logged in using ChatGPT\n")
    assert codex.status()["ok"] and "ChatGPT" in codex.status()["detail"]
    out.write_text("Not logged in\n")
    monkeypatch.setenv("FAKE_EXIT", "1")
    st = codex.status()
    assert st["state"] == "no_login" and st["cmd"] == "codex login"


def test_missing_and_logged_out_clis_say_how_to_fix(monkeypatch, fake_cli, tmp_path):
    monkeypatch.setattr(providers, "find_bin", lambda name: None)
    st = providers.PROVIDERS["codex"].status()
    assert st["state"] == "no_cli" and "codex login" in st["fix"] and st["cmd"]
    evs = run(providers.PROVIDERS["codex"].stream(turn()))
    assert evs == [{"type": "error", "message": "Codex 不可用：没装 Codex CLI。安装后在终端运行 codex login，用 ChatGPT 账号登录"}]

    gemini = providers.Gemini()
    monkeypatch.setattr(gemini, "home", tmp_path / "gemini-home")
    assert gemini.status()["state"] == "no_cli"
    monkeypatch.setattr(providers, "find_bin", lambda name: "/usr/bin/true")
    st = gemini.status()
    assert st["state"] == "no_login" and "Login with Google" in st["fix"]
    (tmp_path / "gemini-home").mkdir()
    (tmp_path / "gemini-home" / "oauth_creds.json").write_text("{}")
    assert gemini.status()["ok"]


# ---------------- Gemini ----------------


def test_gemini_runs_locked_to_read_only_outside_the_vault(fake_cli, tmp_path):
    fake_cli.events({"type": "result", "status": "success", "stats": {}})
    img = tmp_path / "my crop.png"
    img.write_bytes(b"png")
    gemini = providers.PROVIDERS["gemini"]
    run(gemini.stream(turn(model="pro", images=[img], read_dirs=[tmp_path])))
    # every read-only guarantee is an explicit flag, not a CLI default the user's settings could change
    assert fake_cli.args() == [
        "--output-format", "stream-json", "--model", "pro", "--approval-mode", "default",
        "--admin-policy", str(gemini.policy), "--extensions", "none",
        "--allowed-mcp-server-names", providers.NO_MCP, "--skip-trust",
        "--include-directories", f"{config.VAULT_DIR},{tmp_path}"]
    # run from an empty folder of our own, so no .gemini settings in the vault apply
    assert fake_cli.cwd() == gemini.workspace.resolve() and list(gemini.workspace.iterdir()) == []
    assert not fake_cli.cwd().is_relative_to(config.VAULT_DIR)
    policy = gemini.policy.read_text(encoding="utf-8")
    import tomllib
    rules = tomllib.loads(policy)["rule"]
    assert rules[0] == {"toolName": "*", "decision": "deny", "priority": 100, "denyMessage": rules[0]["denyMessage"]}
    assert rules[1]["decision"] == "allow" and rules[1]["priority"] > rules[0]["priority"]
    allowed = set(rules[1]["toolName"])
    assert {"read_file", "grep_search", "list_directory", "glob"} <= allowed
    assert not allowed & {"write_file", "replace", "run_shell_command", "save_memory", "activate_skill"}
    assert fake_cli.stdin().endswith("@" + str(img).replace(" ", "\\ "))


def test_gemini_labels_the_model_that_answered(fake_cli):
    gemini = providers.PROVIDERS["gemini"]
    fake_cli.events(
        {"type": "init", "session_id": "s-9", "model": "auto"},
        {"type": "tool_use", "tool_name": "read_file", "tool_id": "1", "parameters": {"absolute_path": str(config.VAULT_DIR / "a.md")}},
        {"type": "message", "role": "assistant", "content": "第一段", "delta": True},
        {"type": "message", "role": "assistant", "content": "，第二段", "delta": True},
        {"type": "result", "status": "success", "stats": {"models": {
            "gemini-x-flash-lite": {"output_tokens": 12}, "gemini-x-pro": {"output_tokens": 400}}}})
    evs = run(gemini.stream(turn(model="auto")))
    assert [e["type"] for e in evs] == ["model", "thread", "tool", "delta", "delta", "model"]
    assert evs[0]["model"] == "自动" and evs[2]["summary"] == "read_file a.md"  # "auto" in init is not a model
    assert evs[-1]["model"] == "gemini-x-pro + gemini-x-flash-lite"
    fake_cli.events({"type": "init", "session_id": "s-9", "model": "gemini-x-pro"}, {"type": "result", "status": "success"})
    assert [e.get("model") for e in run(gemini.stream(turn(model="pro"))) if e["type"] == "model"] == ["Pro", "gemini-x-pro"]


def test_an_old_gemini_cli_says_to_upgrade(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_ERR", "Unknown arguments: admin-policy, adminPolicy, skip-trust, skipTrust")
    monkeypatch.setenv("FAKE_EXIT", "1")
    evs = run(providers.PROVIDERS["gemini"].stream(turn()))
    assert evs[-1]["type"] == "error" and "版本太旧" in evs[-1]["message"] and "brew upgrade gemini-cli" in evs[-1]["message"]


def test_gemini_error_result_and_resume(fake_cli):
    fake_cli.events({"type": "init", "session_id": "s-9", "model": "g"},
                    {"type": "result", "status": "error", "error": {"type": "api", "message": "RESOURCE_EXHAUSTED: quota"}})
    evs = run(providers.PROVIDERS["gemini"].stream(turn(thread="s-9")))
    assert evs[-1]["type"] == "error" and evs[-1]["message"].startswith("Gemini 用量达到上限")
    args = fake_cli.args()
    assert args[args.index("--resume") + 1] == "s-9"


# ---------------- API providers ----------------


def sse(*chunks: dict[str, Any]) -> bytes:
    return "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


@pytest.fixture
def api_mock(monkeypatch):
    calls: list[dict[str, Any]] = []
    replies: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        return replies.pop(0)

    monkeypatch.setattr(providers, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    return calls, replies


def test_deepseek_streams_and_reports_the_served_model(api_mock):
    calls, replies = api_mock
    write_secret("deepseek", "DEEPSEEK_API_KEY=sk-test-123\n")
    replies.append(httpx.Response(200, content=sse(
        {"model": "deepseek-v9", "choices": [{"delta": {"content": "你"}}]},
        {"model": "deepseek-v9", "choices": [{"delta": {"content": "好"}}]})))
    history = [{"role": "user", "content": "早"}, {"role": "assistant", "content": "早上好"}]
    evs = run(providers.PROVIDERS["deepseek"].stream(turn(model="deepseek-chat", history=history)))
    assert evs == [{"type": "model", "model": "deepseek-chat"}, {"type": "model", "model": "deepseek-v9"},
                   {"type": "delta", "text": "你"}, {"type": "delta", "text": "好"}]
    body = calls[0]["body"]
    assert calls[0]["url"] == "https://api.deepseek.com/chat/completions" and calls[0]["auth"] == "Bearer sk-test-123"
    assert body["stream"] and body["model"] == "deepseek-chat"
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["messages"][0]["content"] == "INSTR" and "tools" not in body


def test_api_tool_calls_run_read_only_tools_then_answer(api_mock, write_vault_file):
    calls, replies = api_mock
    write_secret("zhipu", "ZHIPU_API_KEY=zk-test\n")
    write_vault_file("Atlas/Roadmap.md", "# Roadmap\nGoal: ship paper 1\n")
    replies.append(httpx.Response(200, content=sse(
        {"choices": [{"delta": {"reasoning_content": "想一下"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": "{\"path\": "}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"Atlas/Roadmap.md\"}"}}]}}]})))
    replies.append(httpx.Response(200, content=sse({"choices": [{"delta": {"content": "目标是 paper 1。"}}]})))
    evs = run(providers.PROVIDERS["glm"].stream(turn(model="glm-5.1", tools=ro_tools.file_tools())))
    assert [e["type"] for e in evs] == ["model", "tool", "delta"] and evs[1]["summary"] == "读 Atlas/Roadmap.md"
    assert calls[0]["url"].startswith("https://open.bigmodel.cn/api/paas/v4/") and len(calls) == 2
    msgs = calls[1]["body"]["messages"]
    assert msgs[-2]["tool_calls"][0]["function"] == {"name": "read_file", "arguments": "{\"path\": \"Atlas/Roadmap.md\"}"}
    assert msgs[-2]["reasoning_content"] == "想一下"
    assert msgs[-1]["role"] == "tool" and msgs[-1]["tool_call_id"] == "c1" and "Goal: ship paper 1" in msgs[-1]["content"]
    assert {t["function"]["name"] for t in calls[0]["body"]["tools"]} == {"read_file", "list_dir", "search_text"}


@pytest.mark.parametrize("status,body,expect", [
    (401, {"error": {"message": "Authentication Fails"}}, "API Key 无效"),
    (402, {"error": {"message": "Insufficient Balance"}}, "余额不足"),
    (429, {"error": {"message": "Rate limit reached"}}, "限流"),
    (400, {"error": {"message": "Model Not Exist"}}, "用不了模型 deepseek-chat"),
])
def test_api_errors_are_explained_without_the_key(api_mock, status, body, expect):
    calls, replies = api_mock
    write_secret("deepseek", "DEEPSEEK_API_KEY=sk-secret-999\n")
    replies.append(httpx.Response(status, json=body))
    evs = run(providers.PROVIDERS["deepseek"].stream(turn(model="deepseek-chat")))
    assert evs[-1]["type"] == "error" and expect in evs[-1]["message"] and "sk-secret-999" not in evs[-1]["message"]
    assert len(calls) == 1


def test_api_without_a_key_never_calls_out(api_mock):
    calls, _ = api_mock
    st = providers.PROVIDERS["glm"].status()
    assert st["state"] == "no_key" and "ZHIPU_API_KEY" in st["fix"] and "zhipu.env" in st["fix"]
    evs = run(providers.PROVIDERS["glm"].stream(turn(model="glm-5.1")))
    assert evs[0]["type"] == "error" and "缺 智谱 GLM API Key" in evs[0]["message"] and calls == []


def test_vision_models_get_the_image_inline(api_mock, tmp_path):
    calls, replies = api_mock
    write_secret("zhipu", "ZHIPU_API_KEY=zk\n")
    img = tmp_path / "c.png"
    img.write_bytes(b"\x89PNG")
    replies.append(httpx.Response(200, content=sse({"choices": [{"delta": {"content": "图里是公式"}}]})))
    run(providers.PROVIDERS["glm"].stream(turn(model="glm-4.6v", images=[img])))
    content = calls[0]["body"]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "问题"} and content[1]["image_url"]["url"].startswith("data:image/png;base64,")


# ---------------- read-only tools ----------------


def test_file_tools_stay_inside_the_readable_folders(write_vault_file, tmp_path):
    write_vault_file("Notes/a.md", "alpha\nbeta Zephyr\ngamma\n")
    write_vault_file(".obsidian/workspace.json", "{}")
    (config.TEXT_DIR / "Paper 2026.md").write_text("<!-- page 1 -->\nZephyr appears here\n", encoding="utf-8")
    assert "2\tbeta Zephyr" in ro_tools.read_file("Notes/a.md")
    assert "第 2–2 行" in ro_tools.read_file("Notes/a.md", 2, 1) and "offset=3" in ro_tools.read_file("Notes/a.md", 2, 1)
    assert ro_tools.read_file(str(config.TEXT_DIR / "Paper 2026.md")).startswith("全文：Paper 2026.md")
    for bad in ("../outside.md", "/etc/hosts", str(tmp_path / "x.md"), ".obsidian/workspace.json"):
        with pytest.raises(ro_tools.ToolError):
            ro_tools.read_file(bad)
    assert "Notes/" in ro_tools.list_dir(".") and ".obsidian" not in ro_tools.list_dir(".")
    hits = ro_tools.search_text("zephyr")
    assert "Notes/a.md:2: beta Zephyr" in hits
    assert "全文：Paper 2026.md:2" in ro_tools.search_text("Zephyr", str(config.TEXT_DIR))
    assert ro_tools.search_text("([unclosed") == "没有找到"  # an invalid regex is searched literally


def test_studio_queries_for_api_models_are_read_only():
    names = [t.name for t in ro_tools.for_chat(studio=True)]
    assert names[:3] == ["read_file", "list_dir", "search_text"]
    assert set(names[3:]) == {"reading_list", "idea_list", "timeline", "venue_search", "tracker_latest", "map_read"}
    assert [t.name for t in ro_tools.for_chat(studio=False)] == ["read_file", "list_dir", "search_text"]
    out = asyncio.run(next(t for t in ro_tools.studio_tools() if t.name == "reading_list").call({}))
    assert json.loads(out) == []


# ---------------- conversations bound to a provider ----------------


class Fake(providers.Provider):
    id, label, kind = "fake", "Fake", "cli"

    def __init__(self, events=(), ok=True, pid="fake", kind="cli"):
        self.id, self.kind = pid, kind
        self.label = pid.capitalize()
        self.events, self.ok, self.turns = list(events), ok, []

    def status(self):
        return providers._state("ok", "已登录") if self.ok else providers._state("no_login", f"{self.label} 没登录", "运行 fake login", "fake login")

    def models(self):
        return [{"id": "f-1", "label": "F1"}, {"id": "f-2", "label": "F2", "vision": True}]

    async def stream(self, t):
        self.turns.append(t)
        for e in self.events:
            yield e


class Socket:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.fixture
def no_claude(monkeypatch):
    """Claude must not be touched when a conversation is bound elsewhere."""
    def boom(*a, **k):
        raise AssertionError("Claude was used for a conversation bound to another model")
    monkeypatch.setattr(claude_chat, "ClaudeSDKClient", boom)


def chat_for(key="studio"):
    chat = claude_chat.StudioChat(key)
    chat.ws = Socket()
    return chat


def send(chat, text="你好", images=()):
    asyncio.run(chat.send(text=text, page=None, selection=None, images=list(images), include_page_text=False))
    return chat.ws.sent


def test_a_bound_conversation_streams_and_labels_every_answer(monkeypatch, no_claude):
    fake = Fake([{"type": "model", "model": "f-1-real"}, {"type": "thread", "id": "th-7"},
                 {"type": "text", "text": "第一段"}, {"type": "tool", "name": "x", "summary": "命令 ls"},
                 {"type": "delta", "text": "第"}, {"type": "delta", "text": "二段"}, {"type": "text", "text": "第三段"}])
    monkeypatch.setitem(providers.PROVIDERS, "fake", fake)
    chat = chat_for()
    asyncio.run(chat.set_model("fake", "f-1"))
    sent = send(chat)
    assert [e["type"] for e in sent] == ["start", "delta", "tool", "delta", "delta", "delta", "done"]
    done = sent[-1]
    assert done["provider"] == "fake" and done["model"] == "f-1-real" and not done["is_error"]
    assert done["segments"] == [{"type": "text", "text": "第一段"}, {"type": "tool", "name": "x", "summary": "命令 ls"},
                                {"type": "text", "text": "第二段\n\n第三段"}]
    log = claude_chat.history("studio")
    assert log[-1]["role"] == "assistant" and (log[-1]["provider"], log[-1]["model"]) == ("fake", "f-1-real")
    assert model_chat.binding("studio")["thread"] == "th-7"
    # the read-only note is part of the instructions, and the next turn continues the CLI's thread
    assert "Fake · f-1（只读）" in fake.turns[0].instructions and fake.turns[0].thread is None
    send(chat, "再问")
    assert fake.turns[1].thread == "th-7"


def test_an_unavailable_provider_fails_without_falling_back(monkeypatch, no_claude):
    down = Fake([{"type": "text", "text": "不该出现"}], ok=False)
    other = Fake([{"type": "text", "text": "也不该出现"}], pid="other")
    monkeypatch.setitem(providers.PROVIDERS, "fake", down)
    monkeypatch.setitem(providers.PROVIDERS, "other", other)
    chat = chat_for()
    asyncio.run(chat.set_model("fake", "f-1"))
    sent = send(chat)
    assert [e["type"] for e in sent] == ["error", "done"]
    assert sent[0]["message"] == "Fake 不可用：Fake 没登录。运行 fake login。没有换用其他模型。"
    assert down.turns == [] and other.turns == [] and sent[-1]["is_error"] and sent[-1]["provider"] == "fake"
    assert claude_chat.history("studio")[-1]["segments"] == [{"type": "error", "text": sent[0]["message"]}]


def test_a_provider_error_mid_turn_ends_it(monkeypatch, no_claude):
    fake = Fake([{"type": "delta", "text": "半句"}, {"type": "error", "message": "Fake 出错：断了"}, {"type": "delta", "text": "不该到"}])
    monkeypatch.setitem(providers.PROVIDERS, "fake", fake)
    chat = chat_for()
    asyncio.run(chat.set_model("fake", "f-1"))
    sent = send(chat)
    assert [e["type"] for e in sent] == ["start", "delta", "error", "done"] and sent[-1]["is_error"]


def test_images_need_a_model_that_can_see(monkeypatch, no_claude):
    fake = Fake([{"type": "text", "text": "看到了"}])
    monkeypatch.setitem(providers.PROVIDERS, "fake", fake)
    (config.CROPS_DIR / "c1.png").write_bytes(b"png")
    chat = chat_for()
    asyncio.run(chat.set_model("fake", "f-1"))
    sent = send(chat, images=["c1.png"])
    assert sent[0]["type"] == "error" and "Fake · f-1 不能看图" in sent[0]["message"] and "F2" in sent[0]["message"]
    assert fake.turns == []
    asyncio.run(chat.set_model("fake", "f-2"))
    send(chat, images=["c1.png"])
    assert fake.turns[0].images == [config.CROPS_DIR / "c1.png"]


def test_switching_provider_starts_a_new_thread_and_labels_the_transcript(monkeypatch, no_claude):
    a = Fake([{"type": "thread", "id": "th-a"}, {"type": "text", "text": "A 答"}])
    b = Fake([{"type": "text", "text": "B 答"}], pid="bee", kind="api")
    monkeypatch.setitem(providers.PROVIDERS, "fake", a)
    monkeypatch.setitem(providers.PROVIDERS, "bee", b)
    claude_chat._save_session("studio", "claude-session-1")
    chat = chat_for()
    r = asyncio.run(chat.set_model("fake", "f-1"))
    assert r["event"] is None  # nothing to divide yet
    assert "studio" not in claude_chat._load_sessions()  # leaving Claude ends its thread
    send(chat, "一")
    r = asyncio.run(chat.set_model("fake", "f-2"))  # same provider, other model: same thread
    assert r["event"]["kind"] == "model" and model_chat.binding("studio")["thread"] == "th-a"
    r = asyncio.run(chat.set_model("bee", ""))
    assert r == {"binding": {"provider": "bee", "model": ""}, "event": r["event"]}
    assert r["event"]["kind"] == "provider" and r["event"]["label"] == "Bee · f-1"
    assert model_chat.binding("studio")["thread"] is None
    send(chat, "二")
    send(chat, "三")
    # the API provider's own history: only its thread, not A's
    assert [m["content"] for m in b.turns[1].history] == ["二", "B 答"]
    roles = [(m["role"], m.get("kind"), m.get("provider")) for m in claude_chat.history("studio")]
    assert roles == [("user", None, None), ("assistant", None, "fake"), ("event", "model", "fake"),
                     ("event", "provider", "bee"), ("user", None, None), ("assistant", None, "bee"),
                     ("user", None, None), ("assistant", None, "bee")]
    # a new conversation keeps the provider but forgets its thread
    asyncio.run(chat.reset())
    assert model_chat.public("studio") == {"provider": "bee", "model": ""} and model_chat.load_thread("studio", "bee") == []
    # back to Claude: binding cleared
    asyncio.run(chat.set_model("claude", ""))
    assert model_chat.binding("studio") == {"provider": "claude", "model": "", "thread": None}


def test_no_switch_while_answering(monkeypatch):
    chat = chat_for()

    async def busy():
        async with chat.lock:
            await chat.set_model("deepseek", "")
    with pytest.raises(RuntimeError, match="正在回答"):
        asyncio.run(busy())


def test_stop_cancels_the_stream(monkeypatch, no_claude):
    class Slow(Fake):
        async def stream(self, t):
            yield {"type": "delta", "text": "开始"}
            await asyncio.sleep(30)
            yield {"type": "delta", "text": "不该到"}

    monkeypatch.setitem(providers.PROVIDERS, "fake", Slow())
    chat = chat_for()
    asyncio.run(chat.set_model("fake", "f-1"))

    async def go():
        task = asyncio.create_task(chat.send(text="x", page=None, selection=None, images=[], include_page_text=False))
        while not any(e["type"] == "delta" for e in chat.ws.sent):
            await asyncio.sleep(0.01)
        await chat.interrupt()
        await asyncio.wait_for(task, 5)
    asyncio.run(go())
    assert chat.ws.sent[-2] == {"type": "error", "message": "已停止。"} and chat.ws.sent[-1]["type"] == "done"
    assert chat.other is None


def test_claude_turns_use_the_conversations_model(monkeypatch):
    monkeypatch.setattr(claude_chat, "_claude_env", lambda: {})
    chat = claude_chat.StudioChat("studio")
    assert chat._options().model is None
    model_chat.save("studio", "claude", "haiku", None)
    assert chat._options().model == "haiku"


# ---------------- routes ----------------


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_routes(client, monkeypatch):
    monkeypatch.setitem(providers.PROVIDERS, "claude", Fake(pid="claude"))
    monkeypatch.setitem(providers.PROVIDERS, "fake", Fake(ok=False))
    monkeypatch.setattr(providers, "PROVIDERS", {k: providers.PROVIDERS[k] for k in ("claude", "fake")})
    got = client.get("/api/providers").json()["providers"]
    assert [p["id"] for p in got] == ["claude", "fake"]
    assert got[1] | {"models": None} == {"id": "fake", "label": "Fake", "kind": "cli", "models": None, "default_model": "f-1",
                                         "ok": False, "state": "no_login", "detail": "Fake 没登录", "fix": "运行 fake login",
                                         "cmd": "fake login"}
    assert client.get("/api/chat/studio").json()["binding"] == {"provider": "claude", "model": ""}
    assert client.post("/api/chat/studio/model", json={"provider": "nope"}).status_code == 400
    assert client.post("/api/chat/studio/model", json={"provider": "fake", "model": "--rm"}).status_code == 400
    assert client.post("/api/chat/no-such-paper/model", json={"provider": "fake"}).status_code == 404
    r = client.post("/api/chat/studio/model", json={"provider": "fake", "model": "f-2"})
    assert r.status_code == 200 and r.json()["binding"] == {"provider": "fake", "model": "f-2"}
    assert client.get("/api/chat/studio").json()["binding"] == {"provider": "fake", "model": "f-2"}


# ---------------- GLM translation ----------------


def test_glm_translates_with_its_fast_model(monkeypatch):
    write_secret("zhipu", "ZHIPU_API_KEY=zk-1\nZHIPU_TRANSLATE_MODEL=glm-fast\n")
    monkeypatch.setattr(translate, "load_settings", lambda: {"translate_engine": "glm"})
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, model=json["model"], auth=headers["Authorization"])
        return httpx.Response(200, json={"choices": [{"message": {"content": " 译文 "}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(translate.httpx, "post", post)
    assert translate.translate("Hello") == {"text": "译文", "engine": "智谱 GLM"}
    assert seen == {"url": "https://open.bigmodel.cn/api/paas/v4/chat/completions", "model": "glm-fast", "auth": "Bearer zk-1"}


def test_bilingual_uses_glm_when_chosen(monkeypatch):
    from app import bilingual
    monkeypatch.setattr(bilingual, "load_settings", lambda: {"translate_engine": "glm"})
    assert bilingual.engine() == ("Google 翻译", ["--google"])  # no key: the label says what is used
    write_secret("zhipu", "ZHIPU_API_KEY=zk-2\n")
    label, args, keys = bilingual._engine()
    assert (label, args) == ("智谱 GLM", ["--zhipu", "--zhipu-model", "glm-4.7-flash"])
    assert keys == {"PDF2ZH_ZHIPU_API_KEY": "zk-2"}  # in pdf2zh's environment, not on its command line


# ---------------- review fixes: stopping, resets, blocking calls, labels, long contexts ----------------


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_stopping_a_cli_ends_its_whole_process_group(tmp_path, monkeypatch):
    """Like codex: a wrapper whose child keeps the pipes and ignores SIGTERM. Stop must end both, quickly."""
    monkeypatch.setattr(providers, "GRACE", 0.5)
    script = tmp_path / "wrapper"
    script.write_text('#!/bin/sh\n( trap "" TERM; sleep 60 ) &\necho $! > "$1"\necho \'{"type": "x"}\'\nwait\n')
    script.chmod(0o755)
    child_pid = tmp_path / "child.pid"

    async def go():
        gen = providers.run_jsonl([str(script), str(child_pid)], "", providers.child_env(), tmp_path, providers.Run())
        assert await gen.__anext__() == {"type": "x"}
        started = asyncio.get_running_loop().time()
        await gen.aclose()
        return asyncio.get_running_loop().time() - started

    took = asyncio.run(go())
    pid = int(child_pid.read_text())
    for _ in range(50):
        if not _alive(pid):
            break
        asyncio.run(asyncio.sleep(0.05))
    assert not _alive(pid) and took < 5


class Slow(Fake):
    """A provider that is slow before it streams (status) or while streaming."""

    def __init__(self, slow_status=False, kind="cli"):
        super().__init__(kind=kind)
        self.slow_status = slow_status
        self.hang = True
        self.release = __import__("threading").Event()

    def status(self):
        if self.slow_status:
            self.release.wait(5)
        return super().status()

    async def stream(self, t):
        self.turns.append(t)
        yield {"type": "thread", "id": "th-new"}
        yield {"type": "delta", "text": "开始"}
        if self.hang:
            await asyncio.sleep(30)
        yield {"type": "delta", "text": "不该到" if self.hang else "完"}


def test_stop_also_works_before_the_stream_starts(monkeypatch, no_claude):
    slow = Slow(slow_status=True)
    monkeypatch.setitem(providers.PROVIDERS, "fake", slow)
    chat = chat_for()
    asyncio.run(chat.set_model("fake", "f-1"))

    async def go():
        task = asyncio.create_task(chat.send(text="x", page=None, selection=None, images=[], include_page_text=False))
        while chat.other is None:
            await asyncio.sleep(0.01)
        await chat.interrupt()  # while the status check is still running
        await asyncio.wait_for(task, 1)
        slow.release.set()
    asyncio.run(go())
    assert slow.turns == [] and [e["type"] for e in chat.ws.sent] == ["error", "done"]
    assert chat.ws.sent[0]["message"] == "已停止。" and chat.ws.sent[-1]["is_error"]


def test_a_new_conversation_mid_answer_keeps_nothing_of_the_old_turn(monkeypatch, no_claude):
    slow = Slow(kind="api")
    monkeypatch.setitem(providers.PROVIDERS, "fake", slow)
    chat = chat_for()
    model_chat.save("studio", "fake", "f-1", "th-old")
    slow.hang = False
    send(chat, "第一轮")  # finishes: a transcript to archive
    slow.hang = True
    chat.ws.sent.clear()

    async def go():
        chat.task = asyncio.create_task(chat.send(text="第二轮", page=None, selection=None, images=[], include_page_text=False))
        while not any(e["type"] == "delta" for e in chat.ws.sent):
            await asyncio.sleep(0.01)
        await asyncio.wait_for(chat.reset(), 5)
        assert chat.task.done()
    asyncio.run(go())
    # the stopped turn wrote no thread, no API history and nothing into the new transcript
    assert model_chat.binding("studio")["thread"] is None and model_chat.load_thread("studio", "fake") == []
    assert claude_chat.history("studio") == []
    assert model_chat.public("studio") == {"provider": "fake", "model": "f-1"}


def test_the_codex_model_list_never_blocks_the_event_loop(fake_cli, monkeypatch, tmp_path):
    codex = providers.Codex()
    calls = tmp_path / "calls"
    monkeypatch.setenv("FAKE_EXIT", "1")  # `codex debug models` fails
    real = providers.run_quick

    def counted(args, timeout=15):
        with calls.open("a") as f:
            f.write(" ".join(args[1:]) + "\n")
        return real(args, timeout)
    monkeypatch.setattr(providers, "run_quick", counted)
    assert codex._catalog() == []
    assert codex._catalog() == []  # a failure is remembered for a minute, not retried on every call
    assert calls.read_text().splitlines() == ["debug models"]
    # a stale list is served at once while a background thread refreshes it
    # fetched 10 minutes ago (relative: monotonic time can be under 300 s on a freshly booted machine)
    codex._models = (__import__("time").monotonic() - 600, [{"slug": "old", "visibility": "list"}])
    monkeypatch.setenv("FAKE_EXIT", "0")
    Path(os.environ["FAKE_OUT"]).write_text(json.dumps({"models": [{"slug": "new", "visibility": "list"}]}))
    assert [m["slug"] for m in codex._catalog()] == ["old"]
    for _ in range(100):
        if codex._models[1] and codex._models[1][0]["slug"] == "new":
            break
        __import__("time").sleep(0.02)
    assert [m["slug"] for m in codex._catalog()] == ["new"]


def test_turn_setup_runs_off_the_event_loop(monkeypatch, no_claude):
    import threading
    seen = {}

    class Picky(Fake):
        def default_model(self):
            seen.setdefault("default", threading.current_thread())
            return super().default_model()

        def models(self):
            seen.setdefault("models", threading.current_thread())
            return super().models()

    monkeypatch.setitem(providers.PROVIDERS, "fake", Picky([{"type": "text", "text": "好"}]))
    chat = chat_for()
    loop_thread = {}

    async def go():
        loop_thread["t"] = threading.current_thread()
        await chat.set_model("fake", "")  # the divider names the default model
        seen.clear()
        await chat.send(text="x", page=None, selection=None, images=[], include_page_text=False)
    asyncio.run(go())
    assert seen and all(t is not loop_thread["t"] for t in seen.values())


def test_codex_labels_the_model_its_session_file_records(fake_cli, monkeypatch, tmp_path):
    codex = providers.Codex()
    monkeypatch.setattr(codex, "home", tmp_path / "codex-home")
    day = tmp_path / "codex-home" / "sessions" / "2026" / "10" / "26"
    day.mkdir(parents=True)
    (day / "rollout-2026-10-26T01-03-24-th-77777777.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"type": "session_meta", "payload": {"id": "th-77777777"}},
        {"type": "turn_context", "payload": {"model": "gpt-old"}},
        {"type": "turn_context", "payload": {"model": "gpt-actual"}}]) + "\n")
    fake_cli.events({"type": "thread.started", "thread_id": "th-77777777"}, {"type": "turn.completed"})
    models = [e["model"] for e in run(codex.stream(turn(model=""))) if e["type"] == "model"]
    assert models == ["默认模型", "gpt-actual"]
    assert codex.rollout_model("../../etc") == ""


def test_too_long_contexts_are_named_as_such(api_mock):
    calls, replies = api_mock
    write_secret("deepseek", "DEEPSEEK_API_KEY=sk-1\n")
    replies.append(httpx.Response(400, json={"error": {"message": "This model's maximum context length is 131072 tokens. "
                                                       "However, you requested 140000 tokens.", "type": "invalid_request_error"}}))
    evs = run(providers.PROVIDERS["deepseek"].stream(turn(model="deepseek-chat")))
    assert "上下文超长" in evs[-1]["message"] and "新对话" in evs[-1]["message"] and "用不了模型" not in evs[-1]["message"]
    assert "上下文超长" in providers.PROVIDERS["codex"].friendly("context_length_exceeded: input exceeds the context window")
    assert "上下文超长" in providers.PROVIDERS["gemini"].friendly("The input token count exceeds the maximum number of tokens")


def test_fit_drops_the_oldest_turns_then_old_tool_results():
    big = "x" * 7000  # about 2000 tokens each
    msgs = [{"role": "system", "content": "S"},
            {"role": "user", "content": big}, {"role": "assistant", "content": big},
            {"role": "user", "content": big}, {"role": "assistant", "content": big},
            {"role": "user", "content": "这一轮"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]}, {"role": "tool", "tool_call_id": "a", "content": big},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "b"}]}, {"role": "tool", "tool_call_id": "b", "content": big}]
    current, dropped = providers.fit(msgs, 5, 9000)
    assert (current, dropped) == (3, 2) and msgs[1]["content"] == big and msgs[3]["content"] == "这一轮"
    current, dropped = providers.fit(msgs, current, 3000)
    assert (current, dropped) == (1, 2) and msgs[0]["content"] == "S" and msgs[1]["content"] == "这一轮"
    tools = [m for m in msgs if m["role"] == "tool"]
    assert tools[0]["content"] == providers.SHORTENED and tools[-1]["content"] == big  # the latest result stays


def test_a_trimmed_request_says_so(api_mock, monkeypatch):
    calls, replies = api_mock
    write_secret("deepseek", "DEEPSEEK_API_KEY=sk-1\n")
    monkeypatch.setattr(providers.DeepSeek, "budget", lambda self, model: 3000)
    replies.append(httpx.Response(200, content=sse({"choices": [{"delta": {"content": "好"}}]})))
    history = [{"role": "user", "content": "x" * 7000}, {"role": "assistant", "content": "y" * 7000}]
    evs = run(providers.PROVIDERS["deepseek"].stream(turn(model="deepseek-chat", history=history)))
    assert {"type": "tool", "name": "trim", "summary": "上下文太长：这次没带上最早的 2 条对话"} in evs
    assert [m["role"] for m in calls[0]["body"]["messages"]] == ["system", "user"]
