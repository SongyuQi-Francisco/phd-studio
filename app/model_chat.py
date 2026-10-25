"""Which provider and model each conversation uses, and the turns of the non-Claude ones.

A conversation (paper co-reader, workbench assistant, research line) is bound to one provider and
model, per device (state/<device>/chat_models.json); without a binding it is Claude with the Settings
model. Switching provider starts a new thread: the new model does not see the earlier turns, which
stay in the transcript under a labelled divider. A model change within a provider keeps the thread.
Every answer is logged with the provider and the model that produced it.

The chosen provider is never swapped for another: when it is unavailable, or cannot take the turn
(images for a text-only model), the turn fails with that provider's error and the fix.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import library, line_chat, providers, ro_tools
from .config import CROPS_DIR, EXTRA_READ_DIRS, IDEAS_FILE, PLAN_FILE, READING_LIST, STATE_DIR, TEXT_DIR, VAULT_DIR

if TYPE_CHECKING:
    from .claude_chat import PaperChat

BINDINGS_FILE = STATE_DIR / "chat_models.json"
THREADS_DIR = STATE_DIR / "threads"  # API providers' message history, one file per conversation
FULLTEXT_CHARS = 120_000  # paper text inlined for API models; longer texts are read with read_file
HISTORY_MESSAGES = 40
DEFAULT = {"provider": "claude", "model": "", "thread": None}


def _load() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(BINDINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def binding(key: str) -> dict[str, Any]:
    b = _load().get(key) or {}
    out = {**DEFAULT, **{k: b[k] for k in DEFAULT if k in b}}
    if providers.get(out["provider"]) is None:  # a provider this version doesn't know: say so at send time
        out["unknown"] = True
    return out


def public(key: str) -> dict[str, Any]:
    b = binding(key)
    return {"provider": b["provider"], "model": b["model"]}


def save(key: str, provider: str, model: str, thread: str | None) -> dict[str, Any]:
    data = _load()
    if provider == "claude" and not model:
        data.pop(key, None)
    else:
        data[key] = {"provider": provider, "model": model, "thread": thread}
    _write_json(BINDINGS_FILE, data)
    return binding(key)


def set_thread(key: str, thread: str | None) -> None:
    b = binding(key)
    if b["provider"] != "claude":
        save(key, b["provider"], b["model"], thread)


def _thread_file(key: str) -> Path:
    return THREADS_DIR / f"{library.safe_name(key)}.json"


def load_thread(key: str, provider: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(_thread_file(key).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("messages", []) if data.get("provider") == provider else []


def save_thread(key: str, provider: str, messages: list[dict[str, Any]]) -> None:
    _write_json(_thread_file(key), {"provider": provider, "messages": messages[-HISTORY_MESSAGES:]})


def drop_thread(key: str) -> None:
    """Forget the non-Claude thread (new conversation, or a provider switch); the transcript stays."""
    _thread_file(key).unlink(missing_ok=True)
    set_thread(key, None)


def answered_by(provider: str, model: str) -> str:
    return providers.display(provider, model)


# ---------------- instructions ----------------


def _readonly_block(p: providers.Provider, model: str, studio: bool) -> str:
    if p.id == "codex":
        how = "用只读 shell 命令读文件（cat、sed -n、rg、ls）；沙盒不允许写文件，也不能联网。"
    elif p.id == "gemini":
        how = "用只读工具读文件（read_file、glob、search_file_content 等）；写文件和跑命令的工具不可用。"
    else:
        how = "用 read_file / list_dir / search_text 读 vault、论文全文和额外目录" + (
            "；用 reading_list / idea_list / timeline / venue_search / tracker_latest 查工作台数据。" if studio else "。")
    stores = ""
    if studio and p.kind == "cli":
        stores = (f"\n- 工作台数据是 vault 里的 YAML，需要时直接读：阅读清单 `{READING_LIST}`、规划与投稿目标 `{PLAN_FILE}`、"
                  f"想法 `{IDEAS_FILE}`。")
    return f"""
# 当前模型：{providers.display(p.id, model)}（只读）
用户在工作台里给这个对话选了 {p.label}，你就是它，不是 Claude。上面关于 Edit / Write、studio 写入工具
（reading_push、reading_update、*_add、*_update、idea_merge、follow、venue_watch、venue_details_propose）的说明对你不适用。
- 你只能读：{how}{stores}
- 用户要你改 vault 笔记或工作台数据（阅读清单、里程碑、投稿目标、想法、关注）时，不要尝试写入，也不要说已经改好了；
  告诉用户这一步要在对话面板顶部把模型换回 Claude（Claude 的改动会先给用户看差异再写入），并把建议的修改直接写在回答里。
""".strip()


def _paper_inline(stem: str) -> str:
    """API models can't open files by themselves: the note and the full text go into the system message."""
    paper = library.get_paper(stem)
    parts = []
    if paper.get("note_markdown"):
        parts.append(f"<paper_note path=\"{paper['note_rel']}\">\n{paper['note_markdown']}\n</paper_note>")
    try:
        path = library.text_file(stem)
        text = path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError, RuntimeError):
        path, text = None, ""
    if text:
        cut = len(text) > FULLTEXT_CHARS
        parts.append(f"<paper_fulltext path=\"{path}\"{' truncated=\"true\"' if cut else ''}>\n{text[:FULLTEXT_CHARS]}\n</paper_fulltext>")
        if cut:
            parts.append(f"全文较长，上面只放了前 {FULLTEXT_CHARS} 字；后面的内容用 read_file 读 `{path}`（按行号 offset 接着读）。")
    return "\n\n".join(parts)


def instructions(chat: PaperChat, p: providers.Provider, model: str) -> str:
    """The same role and context Claude gets, plus what this model may do. Reads files: run it off the loop."""
    studio = chat.stem == "studio" or line_chat.is_line_key(chat.stem)
    parts = [chat.instructions(), _readonly_block(p, model, studio)]
    if p.kind == "api" and not studio:
        parts.append(_paper_inline(chat.stem))
    return "\n\n".join(x for x in parts if x)


def unavailable(p: providers.Provider, st: dict[str, Any]) -> str:
    return f"{p.label} 不可用：{st['detail']}。{st['fix']}".rstrip("。") + "。没有换用其他模型。"


def no_vision(p: providers.Provider, model: str) -> str:
    seeing = "、".join(m["label"] for m in p.models() if m.get("vision"))
    alt = f"换成能看图的 {seeing}，或 Claude" if seeing else "换成 Claude 或其他能看图的模型"
    return f"{providers.display(p.id, model)} 不能看图。在面板顶部{alt}，再发一次。"


# ---------------- one turn ----------------


def _check(p: providers.Provider, b: dict[str, Any], paths: list[Path]) -> tuple[str, str | None]:
    """The model this turn uses and why it can't run, if it can't. Status checks and model lists can run
    a CLI: off the event loop."""
    model = b["model"] or p.default_model()
    st = p.status()
    if not st["ok"]:
        return model, unavailable(p, st)
    if paths and not p.vision(model):
        return model, no_vision(p, model)
    return model, None


async def run_turn(chat: PaperChat, b: dict[str, Any], text: str, images: list[str]) -> None:
    """Answer one message with the conversation's non-Claude provider, streaming to the panel.

    The whole turn (status check, context, the stream) is one task that 「停止」 cancels. If the
    conversation was reset meanwhile (新对话), nothing of this turn is written: not the thread, not the
    API history, not the transcript."""
    p = providers.get(b["provider"])
    generation = chat.generation
    segments: list[dict[str, Any]] = []
    out: dict[str, Any] = {"model": b["model"], "reply": "", "error": False, "thread": None, "stopped": False}
    started = time.monotonic()
    paths = [x for x in (CROPS_DIR / Path(n).name for n in images) if x.exists()]

    async def fail(msg: str) -> None:
        segments.append({"type": "error", "text": msg})
        out["error"] = True
        await chat.emit({"type": "error", "message": msg})

    async def work() -> None:
        if p is None:
            await fail(f"这个对话绑定的模型来源「{b['provider']}」已不存在。在面板顶部重新选一个模型。")
            return
        out["model"], problem = await asyncio.to_thread(_check, p, b, paths)
        if problem:
            await fail(problem)
            return
        await chat.emit({"type": "start"})
        try:  # a resumed CLI thread already has its instructions
            resumed = p.kind == "cli" and b.get("thread")
            instr = "" if resumed else await asyncio.to_thread(instructions, chat, p, out["model"])
        except Exception as e:  # noqa: BLE001 - e.g. the paper's PDF can't be fetched
            await fail(f"准备上下文失败：{e}")
            return
        studio = chat.stem == "studio" or line_chat.is_line_key(chat.stem)
        turn = providers.Turn(
            text=text, instructions=instr, model=out["model"], cwd=VAULT_DIR,
            read_dirs=[TEXT_DIR, CROPS_DIR, *(Path(d) for d in EXTRA_READ_DIRS)],
            thread=b.get("thread"), history=load_thread(chat.stem, p.id) if p.kind == "api" else [],
            images=paths, tools=ro_tools.for_chat(studio) if p.kind == "api" else [])
        await _consume(chat, p, turn, segments, out)

    task = asyncio.create_task(work())
    chat.other = task
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if not task.cancelled() or (current and current.cancelling()):
            raise  # the whole send is being cancelled (app shutting down), not just this turn stopped
        out["stopped"] = True
        segments.append({"type": "error", "text": "已停止。"})
        await chat.emit({"type": "error", "message": "已停止。"})
    except Exception as e:  # noqa: BLE001 - report to the panel, never fall back to another model
        await fail(f"{p.label if p else b['provider']} 出错：{e}")
    finally:
        chat.other = None
    if chat.generation != generation:
        return  # 新对话 while this was answering: a stale turn must not write into the new conversation
    if p is not None and not out["error"] and not out["stopped"]:
        if out["thread"]:
            set_thread(chat.stem, out["thread"])
        if p.kind == "api" and out["reply"]:
            note = f"\n\n[附图 {len(paths)} 张]" if paths else ""
            history = load_thread(chat.stem, p.id) + [{"role": "user", "content": text + note},
                                                     {"role": "assistant", "content": out["reply"]}]
            save_thread(chat.stem, p.id, history)
    elif out.get("lost"):
        set_thread(chat.stem, None)  # said so in the error: the next message opens a new thread
    elif out["thread"] and not b.get("thread"):
        set_thread(chat.stem, out["thread"])  # a failed or stopped first turn still opened the CLI's thread
    pid = p.id if p else b["provider"]
    done = {"type": "done", "provider": pid, "model": out["model"], "duration_ms": int((time.monotonic() - started) * 1000),
            "segments": segments, "is_error": out["error"] or out["stopped"]}
    await chat.emit(done)
    chat.log({"role": "assistant", "segments": segments, "provider": pid, "model": out["model"], "ts": time.time()})


async def _consume(chat: PaperChat, p: providers.Provider, turn: providers.Turn,
                   segments: list[dict[str, Any]], out: dict[str, Any]) -> None:
    async for ev in p.stream(turn):
        kind = ev.get("type")
        if kind == "thread":
            out["thread"] = ev["id"]
        elif kind == "thread_lost":
            out["lost"] = True
        elif kind == "model":
            out["model"] = ev["model"]
        elif kind in ("delta", "text"):
            chunk = ev["text"]
            last = segments[-1] if segments else None
            if kind == "text" and last and last["type"] == "text" and last["text"]:
                chunk = "\n\n" + chunk  # whole messages from a CLI are paragraphs
            if last and last["type"] == "text":
                last["text"] += chunk
            else:
                segments.append({"type": "text", "text": chunk})
            out["reply"] += chunk
            await chat.emit({"type": "delta", "text": chunk})
        elif kind == "tool":
            seg = {"type": "tool", "name": ev["name"], "summary": ev["summary"]}
            segments.append(seg)
            await chat.emit({"type": "tool", **seg})
        elif kind == "error":
            segments.append({"type": "error", "text": ev["message"]})
            out["error"] = True
            await chat.emit({"type": "error", "message": ev["message"]})
            return
