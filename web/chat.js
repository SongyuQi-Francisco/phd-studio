// Right panel: Claude co-reader (one persistent session per paper) and the paper's Obsidian note.
import { useEffect, useRef, useState, useCallback } from "preact/hooks";
import { html, Icon, renderMarkdown, api, composing } from "/lib.js";

export const PROMPTS = {
  brief: "给我这篇论文的中文速读：①要解决的问题 ②方法核心（一句话 + 关键机制）③主要证据（关键数字，带页码）④局限 ⑤和我当前课题的关系。先读全文再回答。",
  page: (p) => `用中文讲解第 ${p} 页：这一页在讲什么、关键概念和公式、容易误解的细节。`,
  relate: "这篇论文和我正在推进的课题有什么关系？分别列出：可以借鉴的、需要在论文里区分开的、可能构成威胁（撞车）的。先看我的 Roadmap 和研究线笔记。",
  explainSel: "用中文讲解选中的这段：先给通俗意思，再拆关键术语和符号，最后说它在本文论证里起什么作用。",
  explainImg: "这是从论文里框选的区域。用中文讲解：它表达了什么；如果是公式，逐个解释符号并推导关键步骤；如果是图表，说明每个轴/列的含义和主要结论。",
  intake: "为这篇论文按 AGENTS.md 的 Paper Intake 规则新建论文笔记：用 Paper Note Template 的结构，先核对一手来源（arXiv 页面），只写有原文依据的内容，并按规则更新 Literature Matrix。动手前先说明笔记文件名和打算写的要点。",
  note: (rel) => `根据我们目前的讨论和论文原文，更新这篇论文的笔记（${rel}）。只补充或修正有原文依据的内容，保持现有结构和语言，遵守 AGENTS.md 的 Claim Gate。动手前先用两三句话说明打算改哪里。`,
  audit: (rel, today) => `对这篇论文的笔记（${rel}）做一次 Claim Gate 审计，严格遵守 AGENTS.md。今天是 ${today}。
1. 逐条核对笔记里的每个论断（Problem、Method、Evidence、Relevance、Excerpts 以及其他小节），对照论文全文标为：✅ 有原文支持（给出页码 [p.N]）/ ⚠️ 原文里找不到或表述过度 / ❌ 与原文矛盾。数字、数据集、指标、基线逐个对。
2. 发表状态（同行评审的会议/期刊，还是 arXiv 预印本）和代码是否开源，必须到一手来源核实（arXiv、OpenReview、ACL Anthology、ACM/出版社页面、官方项目页），不要凭记忆。
3. 先给一张审计表（论断 | 结论 | 依据与页码），再列出需要修改的地方。
4. 然后用 Edit 修改笔记：更新 ## Claim Gate 小节（Verified source / Still uncertain，写明核实日期 ${today}），修正有问题的论断。只改有依据的地方，保持原有结构和语言。如果某个重要论断变了，按规则同时更新 07 Evidence/Verification Log.md。每处修改都会先给我看差异。`,
};

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
    <div class="perm-head"><${Icon} name="file" sm /><span>${p.is_new ? "新建" : "修改"}</span><b title=${p.path}>${p.path}</b></div>
    <${Diff} text=${p.diff || "(无变化)"} />
    ${p.resolved === undefined
      ? html`<div class="perm-foot">
          <button class="btn ghost" onClick=${() => answer(p.id, false)}>不要改</button>
          <button class="btn primary" onClick=${() => answer(p.id, true)}>允许写入</button>
        </div>`
      : html`<div class="perm-done">${p.resolved ? "已写入 vault" : "已拒绝"}</div>`}
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
    ${!m.streaming && m.model && html`<div class="foot"><span>${m.model}</span>${m.duration_ms ? html`<span>${(m.duration_ms / 1000).toFixed(1)}s</span>` : ""}</div>`}
  </div>`;
}

function UserMsg({ m }) {
  return html`<div class="msg-user">
    ${m.images?.length ? html`<div class="thumbs">${m.images.map((n) => html`<img src=${"/api/crops/" + n} />`)}</div>` : ""}
    ${m.selection && html`<div class="quote">${m.selection}</div>`}
    ${m.text && html`<div class="bubble">${m.text}</div>`}
  </div>`;
}

export function ChatPanel({ stem, paper, getPage, attachments, setAttachments, registerSend, onPage, onSection, onNoteChanged, claudeOk,
  chips, emptyHint, title, onStudioChanged }) {
  const [msgs, setMsgs] = useState([]);
  const [busy, setBusy] = useState(false);
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
    ws.onerror = () => reject(new Error("连接不上本地服务"));
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
          return { ...m, segments: segs, streaming: false, model: d.model, duration_ms: d.duration_ms };
        });
        setBusy(false);
      }
    };
    wsRef.current = ws;
  }), [stem]);

  useEffect(() => {
    let alive = true;
    setMsgs([]); setBusy(false);
    api(`/api/chat/${encodeURIComponent(stem)}`).then((r) => {
      if (!alive) return;
      setMsgs(r.messages.map((m) => m.role === "assistant" ? { ...m, segments: m.segments } : m));
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
  }, [busy, attachments, connect, getPage]);

  useEffect(() => { registerSend?.({ send, focus: () => taRef.current?.focus() }); }, [send]);

  const answer = (id, allow) => wsRef.current?.send(JSON.stringify({ type: "permission", id, allow }));
  const stop = () => wsRef.current?.send(JSON.stringify({ type: "stop" }));
  const reset = async () => {
    if (!confirm("开始新对话？当前对话会存档，Claude 不再记得之前的内容。")) return;
    wsRef.current?.close();
    await api(`/api/chat/${encodeURIComponent(stem)}`, { method: "DELETE" });
    setMsgs([]);
  };

  const submit = () => {
    const t = text.trim();
    if (!t && !attachments.selection && !attachments.images.length) return;
    if (t) send(t); else if (attachments.images.length) send(PROMPTS.explainImg, { label: "讲解" }); else send(PROMPTS.explainSel, { label: "讲解" });
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

  const lastModel = [...msgs].reverse().find((m) => m.model)?.model;
  const hasAttach = !!(attachments.selection || attachments.images.length);

  return html`
    <div class="side-head">
      <div class="meta"><span class=${"dot " + (claudeOk ? "ok" : "bad")} /><span>${claudeOk ? (title ? `${title} · ${lastModel || "Claude"}` : lastModel || "Claude") : "Claude 未连接"}</span></div>
      <button class="tb-btn" title="新对话" onClick=${reset}><${Icon} name="refresh" sm /></button>
    </div>
    <div class="chat" ref=${listRef} onScroll=${onScroll}>
      ${msgs.length === 0
        ? html`<div class="chat-empty"><${Icon} name="sparkle" /><div>${emptyHint || "划选文字，或按住 ⌥ 框选图表 / 公式"}</div></div>`
        : msgs.map((m) => m.role === "user" ? html`<${UserMsg} m=${m} />` : html`<${AssistantMsg} m=${m} onPage=${onPage} onSection=${onSection} answer=${answer} />`)}
    </div>
    <div class="composer">
      ${chips ? html`<div class="chips">${chips.map((c) => html`<button class="chip" disabled=${busy}
          onClick=${() => c.fill ? (setText(c.prompt), setTimeout(() => taRef.current?.focus(), 0)) : send(c.prompt, { selection: null, images: [], label: c.label })}>${c.label}</button>`)}</div>` : html`
      <div class="chips">
        <button class="chip" disabled=${busy} onClick=${() => send(PROMPTS.brief, { selection: null, images: [], label: "速读" })}>速读</button>
        <button class="chip" disabled=${busy} onClick=${() => { const p = getPage(); send(PROMPTS.page(p), { includePageText: true, page: p, selection: null, images: [], label: `讲解第 ${p} 页` }); }}>讲解本页</button>
        <button class="chip" disabled=${busy} onClick=${() => send(PROMPTS.relate, { selection: null, images: [], label: "和我的课题有什么关系" })}>和我的课题</button>
        <button class="chip" disabled=${busy || !paper} onClick=${() => paper.note_rel
          ? send(PROMPTS.note(paper.note_rel), { selection: null, images: [], label: "更新笔记" })
          : send(PROMPTS.intake, { selection: null, images: [], label: "建笔记" })}>${paper && !paper.note_rel ? "建笔记" : "更新笔记"}</button>
        <button class="chip" disabled=${busy || !paper?.note_rel} title=${paper?.note_rel ? "逐条核对笔记论断和发表状态，更新 Claim Gate" : "先建笔记"}
          onClick=${() => send(PROMPTS.audit(paper.note_rel, new Date().toLocaleDateString("sv")), { selection: null, images: [], label: "Claim Gate 审计" })}>Claim Gate 审计</button>
      </div>`}
      ${hasAttach && html`<div class="attach">
        ${attachments.selection && html`<div class="att"><${Icon} name="quote" sm /><span>${attachments.selection.text}</span>
          <button onClick=${() => setAttachments({ ...attachments, selection: null })}><${Icon} name="x" sm /></button></div>`}
        ${attachments.images.map((im, i) => html`<div class="att"><img src=${"/api/crops/" + im.name} /><span>p.${im.page}</span>
          <button onClick=${() => setAttachments({ ...attachments, images: attachments.images.filter((_, j) => j !== i) })}><${Icon} name="x" sm /></button></div>`)}
      </div>`}
      <div class="inputbox">
        <textarea ref=${taRef} rows="1" placeholder=${hasAttach ? "问点什么，或直接发送让 Claude 讲解" : "问 Claude…"} value=${text} onInput=${autosize} onKeyDown=${onKey} />
        ${busy
          ? html`<button class="send" title="停止" onClick=${stop}><${Icon} name="stop" sm /></button>`
          : html`<button class="send" title="发送 (Enter)" disabled=${!text.trim() && !hasAttach} onClick=${submit}><${Icon} name="send" sm /></button>`}
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
    <div class="side-head"><div class="meta"><span>还没有笔记</span></div></div>
    <div class="note-empty"><p>这篇只有 arXiv 链接，还没有 Obsidian 笔记。</p>
      <button class="btn primary" onClick=${onAskIntake}>让 Claude 建笔记</button></div>`;
  const body = md.replace(/^---\n[\s\S]*?\n---\n?/, "");
  const statusCls = paper.status === "verified" ? "ok" : paper.status?.includes("preprint") ? "warn" : "";
  const obsidian = paper.obsidian_url;
  return html`
    <div class="side-head">
      <div class="meta"><span>${paper.note_rel}</span></div>
      <div style="display:flex;gap:2px">
        <a class="tb-btn" href=${obsidian} title="在 Obsidian 打开"><${Icon} name="external" sm /></a>
        <button class="tb-btn" onClick=${onAskUpdate}><${Icon} name="sparkle" sm />让 Claude 更新</button>
      </div>
    </div>
    <div class="note">
      <div class="props">
        ${paper.status && html`<span class=${"tag " + statusCls}>${paper.status}</span>`}
        ${paper.year && html`<span class="tag">${paper.year}</span>`}
        ${paper.tags.slice(0, 6).map((t) => html`<span class="tag">${t}</span>`)}
      </div>
      <${Markdown} text=${body} onPage=${onPage} onSection=${onSection} loose />
    </div>`;
}
