// i18n: done
// Right panel: Claude co-reader (one persistent session per paper) and the paper's Obsidian note.
// Each conversation can be switched to another model (models.js); only Claude may write.
import { useEffect, useRef, useState, useCallback } from "preact/hooks";
import { html, Icon, renderMarkdown, api, composing } from "/lib.js";
import { ModelPicker, answeredBy, providerLabel } from "/models.js";
import { t } from "/i18n.js";

// Prompts sent to Claude: not UI text, so they stay in Chinese. None of them names a language: the
// reply follows the answer-language setting (app/claude_chat.py puts it in the system prompt).
export const PROMPTS = {
  brief: "给我这篇论文的速读：①要解决的问题 ②方法核心（一句话 + 关键机制）③主要证据（关键数字，带页码）④局限 ⑤和我当前课题的关系。先读全文再回答。", // i18n-ignore
  page: (p) => `讲解第 ${p} 页：这一页在讲什么、关键概念和公式、容易误解的细节。`, // i18n-ignore
  relate: "这篇论文和我正在推进的课题有什么关系？分别列出：可以借鉴的、需要在论文里区分开的、可能构成威胁（撞车）的。先看我的 Roadmap 和研究线笔记。", // i18n-ignore
  explainSel: "讲解选中的这段：先给通俗意思，再拆关键术语和符号，最后说它在本文论证里起什么作用。", // i18n-ignore
  explainImg: "这是从论文里框选的区域。讲解：它表达了什么；如果是公式，逐个解释符号并推导关键步骤；如果是图表，说明每个轴/列的含义和主要结论。", // i18n-ignore
  intake: "为这篇论文按 AGENTS.md 的 Paper Intake 规则新建论文笔记：用 Paper Note Template 的结构，先核对一手来源（arXiv 页面），只写有原文依据的内容，并按规则更新 Literature Matrix。动手前先说明笔记文件名和打算写的要点。", // i18n-ignore
  note: (rel) => `根据我们目前的讨论和论文原文，更新这篇论文的笔记（${rel}）。只补充或修正有原文依据的内容，保持现有结构和语言，遵守 AGENTS.md 的 Claim Gate。动手前先用两三句话说明打算改哪里。`, // i18n-ignore
  discussNote: (path) => `我们来讨论这篇笔记：${path}。先读它，然后告诉我要点、和我当前课题的关系、以及值得跟进的下一步。`, // i18n-ignore
  audit: (rel, date) => [
    `对这篇论文的笔记（${rel}）做一次 Claim Gate 审计，严格遵守 AGENTS.md。今天是 ${date}。`, // i18n-ignore
    "1. 逐条核对笔记里的每个论断（Problem、Method、Evidence、Relevance、Excerpts 以及其他小节），对照论文全文标为：✅ 有原文支持（给出页码 [p.N]）/ ⚠️ 原文里找不到或表述过度 / ❌ 与原文矛盾。数字、数据集、指标、基线逐个对。", // i18n-ignore
    "2. 发表状态（同行评审的会议/期刊，还是 arXiv 预印本）和代码是否开源，必须到一手来源核实（arXiv、OpenReview、ACL Anthology、ACM/出版社页面、官方项目页），不要凭记忆。", // i18n-ignore
    "3. 先给一张审计表（论断 | 结论 | 依据与页码），再列出需要修改的地方。", // i18n-ignore
    `4. 然后用 Edit 修改笔记：更新 ## Claim Gate 小节（Verified source / Still uncertain，写明核实日期 ${date}），修正有问题的论断。只改有依据的地方，保持原有结构和语言。如果某个重要论断变了，按规则同时更新 07 Evidence/Verification Log.md。每处修改都会先给我看差异。`, // i18n-ignore
  ].join("\n"),
};

// The date in the audit prompt: a machine format (YYYY-MM-DD), not the interface's.
const today = () => new Date().toLocaleDateString("sv"); // i18n-ignore

// loose: also link "p. 3" / "§5.4" written in prose (paper notes, the paper's own chat).
function Markdown({ text, onPage, onSection, loose }) {
  const ref = useRef();
  const click = (e) => {
    const a = e.target.closest(".pagelink");
    if (!a) return;
    if (a.dataset.sec) onSection?.(a.dataset.sec);
    else onPage?.(Number(a.dataset.page));
  };
  return html`<div class="md" ref=${ref} onClick=${click} dangerouslySetInnerHTML=${{ __html: renderMarkdown(text, { loose }) }} />`;
}

function Diff({ text }) {
  const lines = text.split("\n").filter((l) => !l.startsWith("---") && !l.startsWith("+++"));
  return html`<div class="diff">${lines.map((l) => html`<div class=${l[0] === "+" ? "a" : l[0] === "-" ? "d" : l.startsWith("@@") ? "h" : ""}>${l || " "}</div>`)}</div>`;
}

function Permission({ p, answer }) {
  return html`<div class="perm">
    <div class="perm-head"><${Icon} name="file" sm /><span>${t(p.is_new ? "chat.perm.new" : "chat.perm.edit")}</span><b title=${p.path}>${p.path}</b></div>
    <${Diff} text=${p.diff || t("chat.perm.no_change")} />
    ${p.resolved === undefined
      ? html`<div class="perm-foot">
          <button class="btn ghost" onClick=${() => answer(p.id, false)}>${t("chat.perm.deny")}</button>
          <button class="btn primary" onClick=${() => answer(p.id, true)}>${t("chat.perm.allow")}</button>
        </div>`
      : html`<div class="perm-done">${t(p.resolved ? "chat.perm.written" : "chat.perm.declined")}</div>`}
  </div>`;
}

function AssistantMsg({ m, onPage, onSection, answer }) {
  return html`<div class="msg-ai">
    ${(m.segments || []).map((s) => s.type === "text"
      ? html`<${Markdown} text=${s.text} onPage=${onPage} onSection=${onSection} loose=${!!onPage} />`
      : s.type === "tool"
      ? html`<div class="tool"><${Icon} name=${s.name === "Edit" || s.name === "Write" ? "file" : "search"} sm /><span>${s.summary}</span></div>`
      : s.type === "perm"
      ? html`<${Permission} p=${s} answer=${answer} />`
      : s.type === "change"
      ? html`<div class="change"><${Icon} name="check" sm /><span>${s.text}</span></div>`
      : html`<div class="err">${s.text}</div>`)}
    ${m.streaming && html`<div class="typing"><i /><i /><i /></div>`}
    ${!m.streaming && answeredBy(m) && html`<div class="foot"><span>${answeredBy(m)}</span>${m.duration_ms ? html`<span>${(m.duration_ms / 1000).toFixed(1)}s</span>` : ""}</div>`}
  </div>`;
}

// A provider or model switch, kept in the transcript where it happened.
function EventMsg({ m }) {
  return html`<div class="msg-event"><span>${t(m.kind === "provider" ? "chat.event.provider" : "chat.event.model", { label: m.label })}</span></div>`;
}

function UserMsg({ m }) {
  return html`<div class="msg-user">
    ${m.images?.length ? html`<div class="thumbs">${m.images.map((n) => html`<img src=${"/api/crops/" + n} />`)}</div>` : ""}
    ${m.selection && html`<div class="quote">${m.selection}</div>`}
    ${m.text && html`<div class="bubble">${m.text}</div>`}
  </div>`;
}

export function ChatPanel({ stem, paper, getPage, attachments, setAttachments, registerSend, onPage, onSection, onNoteChanged, claudeOk,
  chips, emptyHint, title, onStudioChanged, onBack, backLabel, banner }) {
  const [msgs, setMsgs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [binding, setBinding] = useState({ provider: "claude", model: "" });
  const isClaude = binding.provider === "claude";
  const who = providerLabel(binding.provider);
  const [text, setText] = useState("");
  const wsRef = useRef(null);
  const listRef = useRef();
  const taRef = useRef();
  const stick = useRef(true);

  const update = (fn) => setMsgs((prev) => {
    const next = prev.slice();
    const last = next[next.length - 1];
    if (last?.role === "assistant" && last.streaming) next[next.length - 1] = fn({ ...last, segments: last.segments.slice() });
    return next;
  });

  const connect = useCallback(() => new Promise((resolve, reject) => {
    const old = wsRef.current;
    if (old && old.readyState === WebSocket.OPEN) return resolve(old);
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/chat/${encodeURIComponent(stem)}`);
    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error(t("chat.no_server")));
    ws.onclose = () => { if (wsRef.current === ws) { wsRef.current = null; setBusy(false); update((m) => ({ ...m, streaming: false })); } };
    ws.onmessage = (ev) => {
      const d = JSON.parse(ev.data);
      if (d.type === "delta") update((m) => {
        const segs = m.segments;
        const last = segs[segs.length - 1];
        if (last?.type === "text") segs[segs.length - 1] = { ...last, text: last.text + d.text };
        else segs.push({ type: "text", text: d.text });
        return m;
      });
      else if (d.type === "tool") update((m) => { m.segments.push({ type: "tool", name: d.name, summary: d.summary }); return m; });
      else if (d.type === "permission") update((m) => { m.segments.push({ ...d, type: "perm" }); return m; });
      else if (d.type === "studio_changed") { update((m) => { m.segments.push({ type: "change", text: d.summary }); return m; }); onStudioChanged?.(d.area); }
      else if (d.type === "permission_resolved") {
        setMsgs((prev) => prev.map((m) => m.segments ? { ...m, segments: m.segments.map((s) => s.type === "perm" && s.id === d.id ? { ...s, resolved: d.allowed } : s) } : m));
        if (d.allowed) onNoteChanged?.();
      } else if (d.type === "error") {
        update((m) => { m.segments.push({ type: "error", text: d.message }); return m; });
      } else if (d.type === "done") {
        update((m) => {
          // keep live permission cards (they carry the user's decision); take text/tools from the server
          const perms = m.segments.filter((s) => s.type === "perm" || s.type === "change");
          const segs = d.segments.length ? [...d.segments, ...perms] : m.segments;
          return { ...m, segments: segs, streaming: false, provider: d.provider, model: d.model, duration_ms: d.duration_ms };
        });
        setBusy(false);
      }
    };
    wsRef.current = ws;
  }), [stem]);

  useEffect(() => {
    let alive = true;
    setMsgs([]); setBusy(false); setBinding({ provider: "claude", model: "" });
    api(`/api/chat/${encodeURIComponent(stem)}`).then((r) => {
      if (!alive) return;
      if (r.binding) setBinding(r.binding);
      // keep anything sent before the history arrived (a prompt handed to a panel that just mounted)
      setMsgs((prev) => [...r.messages.map((m) => m.role === "assistant" ? { ...m, segments: m.segments } : m), ...prev]);
      stick.current = true;
    });
    return () => { alive = false; wsRef.current?.close(); wsRef.current = null; };
  }, [stem]);

  useEffect(() => {
    const el = listRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [msgs]);

  const send = useCallback(async (body, opts = {}) => {
    if (busy) return;
    if (opts.write && !isClaude) {  // never hand a write to a read-only model, never switch for the user
      setMsgs((prev) => [...prev, { role: "user", text: opts.label || body }, { role: "assistant", segments: [{ type: "error", text: t("chat.needs_claude") }] }]);
      return;
    }
    const sel = opts.selection !== undefined ? opts.selection : attachments.selection;
    const imgs = opts.images !== undefined ? opts.images : attachments.images;
    const page = opts.page || sel?.page || imgs[0]?.page || getPage?.() || null;
    const payload = {
      type: "send", text: body, page,
      selection: sel?.text || null,
      images: imgs.map((i) => i.name),
      include_page_text: !!opts.includePageText,
      display: opts.label || null,
    };
    setMsgs((prev) => [...prev,
      { role: "user", text: opts.label || body, selection: payload.selection, images: payload.images },
      { role: "assistant", segments: [], streaming: true }]);
    stick.current = true;
    setBusy(true);
    setText("");
    setAttachments({ selection: null, images: [] });
    try {
      const ws = await connect();
      ws.send(JSON.stringify(payload));
    } catch (e) {
      update((m) => ({ ...m, streaming: false, segments: [{ type: "error", text: e.message }] }));
      setBusy(false);
    }
  }, [busy, attachments, connect, getPage, isClaude]);

  // busy: answering or waiting on an approval (send changes with it, so this re-registers)
  useEffect(() => { registerSend?.({ send, focus: () => taRef.current?.focus(), busy }); }, [send]);

  const answer = (id, allow) => wsRef.current?.send(JSON.stringify({ type: "permission", id, allow }));
  const stop = () => wsRef.current?.send(JSON.stringify({ type: "stop" }));
  const pick = async (provider, model) => {
    if (provider === binding.provider && model === binding.model) return;
    if (provider !== binding.provider && msgs.some((m) => m.role === "user")
      && !confirm(t("chat.confirm_switch", { label: providerLabel(provider) }))) return;
    try {
      const r = await api(`/api/chat/${encodeURIComponent(stem)}/model`, { json: { provider, model } });
      setBinding(r.binding);
      if (r.event) setMsgs((prev) => [...prev, r.event]);
    } catch (e) { alert(e.message); }
  };
  const reset = async () => {
    if (!confirm(t("chat.confirm_reset", { who }))) return;
    wsRef.current?.close();
    await api(`/api/chat/${encodeURIComponent(stem)}`, { method: "DELETE" });
    setMsgs([]);
  };

  const submit = () => {
    const body = text.trim();
    if (!body && !attachments.selection && !attachments.images.length) return;
    if (body) send(body); else if (attachments.images.length) send(PROMPTS.explainImg, { label: t("chat.explain") }); else send(PROMPTS.explainSel, { label: t("chat.explain") });
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !composing(e)) { e.preventDefault(); submit(); }
  };
  const autosize = (e) => {
    setText(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = Math.min(180, e.target.scrollHeight) + "px";
  };
  const onScroll = (e) => {
    const el = e.target;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const hasAttach = !!(attachments.selection || attachments.images.length);

  return html`
    <div class="side-head">
      ${onBack && html`<button class="tb-btn back" title=${t("chat.back_to", { label: backLabel })} onClick=${onBack}><${Icon} name="left" sm />${backLabel}</button>`}
      <div class="meta">${title && html`<span class="side-title">${title}</span>`}
        <${ModelPicker} binding=${binding} busy=${busy} fallbackOk=${claudeOk} onPick=${pick} /></div>
      <button class="tb-btn" title=${t("chat.new_chat")} onClick=${reset}><${Icon} name="refresh" sm /></button>
    </div>
    ${banner}
    <div class="chat" ref=${listRef} onScroll=${onScroll}>
      ${msgs.length === 0
        ? html`<div class="chat-empty"><${Icon} name="sparkle" /><div>${emptyHint || t("chat.empty_hint")}</div></div>`
        : msgs.map((m) => m.role === "user" ? html`<${UserMsg} m=${m} />` : m.role === "event" ? html`<${EventMsg} m=${m} />`
          : html`<${AssistantMsg} m=${m} onPage=${onPage} onSection=${onSection} answer=${answer} />`)}
    </div>
    <div class="composer">
      ${chips ? html`<div class="chips">${chips.map((c) => html`<button class="chip" disabled=${busy || (c.write && !isClaude)}
          title=${c.write && !isClaude ? t("chat.only_claude_data") : ""}
          onClick=${() => c.run ? c.run(send) : c.fill ? (setText(c.prompt), setTimeout(() => taRef.current?.focus(), 0)) : send(c.prompt, { selection: null, images: [], label: c.label, write: c.write })}>${c.label}</button>`)}</div>` : html`
      <div class="chips">
        <button class="chip" disabled=${busy} onClick=${() => send(PROMPTS.brief, { selection: null, images: [], label: t("chat.quick_read") })}>${t("chat.quick_read")}</button>
        <button class="chip" disabled=${busy} onClick=${() => { const p = getPage(); send(PROMPTS.page(p), { includePageText: true, page: p, selection: null, images: [], label: t("chat.explain_page", { p }) }); }}>${t("chat.explain_this_page")}</button>
        <button class="chip" disabled=${busy} onClick=${() => send(PROMPTS.relate, { selection: null, images: [], label: t("chat.relate_label") })}>${t("chat.relate")}</button>
        <button class="chip" disabled=${busy || !paper || !isClaude} title=${isClaude ? "" : t("chat.only_claude_note")} onClick=${() => paper.note_rel
          ? send(PROMPTS.note(paper.note_rel), { selection: null, images: [], label: t("chat.update_note"), write: true })
          : send(PROMPTS.intake, { selection: null, images: [], label: t("chat.create_note"), write: true })}>${t(paper && !paper.note_rel ? "chat.create_note" : "chat.update_note")}</button>
        <button class="chip" disabled=${busy || !paper?.note_rel || !isClaude}
          title=${!isClaude ? t("chat.only_claude_note") : paper?.note_rel ? t("chat.audit_title") : t("chat.need_note")}
          onClick=${() => send(PROMPTS.audit(paper.note_rel, today()), { selection: null, images: [], label: t("chat.audit"), write: true })}>${t("chat.audit")}</button>
      </div>`}
      ${hasAttach && html`<div class="attach">
        ${attachments.selection && html`<div class="att"><${Icon} name="quote" sm /><span>${attachments.selection.text}</span>
          <button onClick=${() => setAttachments({ ...attachments, selection: null })}><${Icon} name="x" sm /></button></div>`}
        ${attachments.images.map((im, i) => html`<div class="att"><img src=${"/api/crops/" + im.name} /><span>p.${im.page}</span>
          <button onClick=${() => setAttachments({ ...attachments, images: attachments.images.filter((_, j) => j !== i) })}><${Icon} name="x" sm /></button></div>`)}
      </div>`}
      <div class="inputbox">
        <textarea ref=${taRef} rows="1" placeholder=${t(hasAttach ? "chat.placeholder_attach" : "chat.placeholder", { who })} value=${text} onInput=${autosize} onKeyDown=${onKey} />
        ${busy
          ? html`<button class="send" title=${t("chat.stop")} onClick=${stop}><${Icon} name="stop" sm /></button>`
          : html`<button class="send" title=${t("chat.send")} disabled=${!text.trim() && !hasAttach} onClick=${submit}><${Icon} name="send" sm /></button>`}
      </div>
    </div>`;
}

export function NotePanel({ paper, version, onAskUpdate, onAskIntake, onPage, onSection }) {
  const [md, setMd] = useState(paper?.note_markdown || "");
  useEffect(() => {
    if (!paper?.note_rel) return;
    api(`/api/papers/${encodeURIComponent(paper.stem)}`).then((p) => setMd(p.note_markdown));
  }, [paper?.stem, version]);
  if (!paper) return null;
  if (!paper.note_rel) return html`
    <div class="side-head"><div class="meta"><span>${t("note.panel.empty_title")}</span></div></div>
    <div class="note-empty"><p>${t("note.panel.empty_body")}</p>
      <button class="btn primary" onClick=${onAskIntake}>${t("note.panel.ask_create")}</button></div>`;
  const body = md.replace(/^---\n[\s\S]*?\n---\n?/, "");
  const statusCls = paper.status === "verified" ? "ok" : paper.status?.includes("preprint") ? "warn" : "";
  const obsidian = paper.obsidian_url;
  return html`
    <div class="side-head">
      <div class="meta"><span>${paper.note_rel}</span></div>
      <div style="display:flex;gap:2px">
        <a class="tb-btn" href=${obsidian} title=${t("note.open_obsidian")}><${Icon} name="external" sm /></a>
        <button class="tb-btn" onClick=${onAskUpdate}><${Icon} name="sparkle" sm />${t("note.panel.ask_update")}</button>
      </div>
    </div>
    <div class="note">
      <div class="props">
        ${paper.status && html`<span class=${"tag " + statusCls}>${paper.status}</span>`}
        ${paper.year && html`<span class="tag">${paper.year}</span>`}
        ${paper.tags.slice(0, 6).map((tag) => html`<span class="tag">${tag}</span>`)}
      </div>
      <${Markdown} text=${body} onPage=${onPage} onSection=${onSection} loose />
    </div>`;
}
