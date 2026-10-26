// Import dialog of the results panel: paste a table or point at a CSV / JSON output file, say once which
// column is which (the app remembers it for the file), see what would change, then write it as one batch.
// i18n: done
// i18n-keys: rmap_results.role.{config,dataset,date,run,metric,value}
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, prefs } from "/lib.js";
import { t } from "/i18n.js";

const ROLES = ["config", "dataset", "date", "run"];
const signature = (columns) => [...columns].sort().join("\u0000");
const CONST = "\u0000const";

// A column picker for one role: a column, a fixed value, or nothing.
function Pick({ role, columns, mp, set, required }) {
  const cur = mp[role];
  const val = cur?.col ?? (cur ? CONST : "");
  return html`<label class="imp-pick"><span>${t("rmap_results.role." + role)}${required && html`<em>*</em>`}</span>
    <select value=${val} onChange=${(e) => {
      const v = e.target.value;
      set(role, v === "" ? null : v === CONST ? { const: cur?.const || " " } : { col: v });
    }}>
      <option value="">${required ? t("rmap_results.imp.choose") : "—"}</option>
      ${columns.map((c) => html`<option value=${c}>${c}</option>`)}
      <option value=${CONST}>${t("rmap_results.imp.fixed")}</option>
    </select>
    ${cur?.const != null && html`<input class="imp-const" value=${cur.const.trim()} placeholder=${t("rmap_results.imp.fixed_ph")}
      onInput=${(e) => set(role, { const: e.target.value || " " })} />`}</label>`;
}

function MappingEditor({ pre, mp, setMp, configs }) {
  const set = (role, v) => setMp((m) => { const n = { ...m }; if (v) n[role] = v; else delete n[role]; return n; });
  const wide = !!mp.metrics;
  const used = new Set(ROLES.concat(["metric", "value", "baseline"]).map((r) => mp[r]?.col).filter(Boolean));
  const toggle = (key, name) => setMp((m) => {
    const cur = m[key] || [];
    const next = cur.includes(name) ? cur.filter((x) => x !== name) : [...cur, name];
    const n = { ...m };
    if (next.length) n[key] = next; else delete n[key];
    return n;
  });
  const shape = (isWide) => setMp((m) => {
    const n = { ...m };
    if (isWide) { delete n.metric; delete n.value; n.metrics = pre.columns.filter((c) => !used.has(c)).slice(0, 8); }
    else { delete n.metrics; }
    return n;
  });
  return html`<div class="imp-map">
    <div class="imp-shape" role="radiogroup">
      <button type="button" role="radio" aria-checked=${!wide} class=${!wide ? "on" : ""} onClick=${() => shape(false)}>${t("rmap_results.imp.long")}</button>
      <button type="button" role="radio" aria-checked=${wide} class=${wide ? "on" : ""} onClick=${() => shape(true)}>${t("rmap_results.imp.wide")}</button>
    </div>
    <div class="imp-picks">
      <${Pick} role="config" required columns=${pre.columns} mp=${mp} set=${set} />
      ${!wide && html`<${Pick} role="metric" required columns=${pre.columns} mp=${mp} set=${set} />
        <${Pick} role="value" required columns=${pre.columns} mp=${mp} set=${set} />`}
      ${ROLES.slice(1).map((r) => html`<${Pick} key=${r} role=${r} columns=${pre.columns} mp=${mp} set=${set} />`)}
    </div>
    ${wide && html`<div class="imp-metrics"><span>${t("rmap_results.imp.metrics")}</span>
      ${pre.columns.filter((c) => !used.has(c)).map((c) => html`<button type="button" key=${c} class=${"rs-chip rs-mono" + (mp.metrics.includes(c) ? " on" : "")}
        aria-pressed=${mp.metrics.includes(c)} onClick=${() => toggle("metrics", c)}>${c}</button>`)}</div>`}
    <label class="imp-decimal"><span>${t("rmap_results.imp.decimal")}</span>
      <select value=${mp.decimal || "."} onChange=${(e) => setMp((m) => ({ ...m, decimal: e.target.value }))}><option value=".">0.5</option><option value=",">0,5</option></select></label>
    ${configs.length > 0 && html`<div class="imp-metrics"><span>${t("rmap_results.imp.baselines")}</span>
      ${configs.map((c) => html`<button type="button" key=${c} class=${"rs-chip" + ((mp.baselines || []).includes(c) ? " on" : "")}
        aria-pressed=${(mp.baselines || []).includes(c)} onClick=${() => toggle("baselines", c)}>${c}</button>`)}</div>`}
  </div>`;
}

export function ImportDialog({ ctx, node, onClose }) {
  const [src, setSrc] = useState("paste");
  const [text, setText] = useState("");
  const [path, setPath] = useState("");
  const [files, setFiles] = useState(null);
  const [pre, setPre] = useState(null);
  const [mp, setMp] = useState(null);
  const [check, setCheck] = useState(null);
  const [error, setError] = useState("");
  const [remember, setRemember] = useState(true);
  const [watch, setWatch] = useState(null); // null: not touched, the file keeps whatever watch it has
  const [busy, setBusy] = useState(false);
  const stale = useRef(0);
  const base = `${ctx.api.base}/results`;
  const source = src === "file" ? { path: path.trim() } : { text };
  const ready = src === "file" ? path.trim() !== "" : text.trim() !== "";

  useEffect(() => {
    if (src !== "file" || files) return;
    ctx.api.call("/api/rmap/results/files").then(setFiles).catch(() => setFiles({ files: [], roots: [] }));
  }, [src]);

  const read = async () => {
    setError(""); setCheck(null); setBusy(true);
    try {
      const r = await ctx.api.call(`${base}/preview`, { json: { ...source, node: node.id } });
      // a pasted table has no file to remember the mapping on: this browser keeps it for the same header
      const mine = src === "paste" && !r.remembered ? prefs.get("rmap:rs:map:" + signature(r.columns), null) : null;
      const usable = mine && Object.values(mine).every((v) => !v?.col || r.columns.includes(v.col));
      setPre(usable ? { ...r, remembered: true } : r); setMp(usable ? mine : r.mapping); setWatch(null);
    } catch (e) { setPre(null); setError(e.message); } finally { setBusy(false); }
  };

  // what the import would do with the mapping as it stands (nothing is written)
  useEffect(() => {
    if (!pre || !mp) return;
    const mine = ++stale.current;
    const timer = setTimeout(async () => {
      try {
        const r = await ctx.api.call(`${base}/import`, { json: { node: node.id, ...source, mapping: mp, dry_run: true, remember } });
        if (mine === stale.current) { setCheck(r); setError(""); }
      } catch (e) { if (mine === stale.current) { setCheck(null); setError(e.message); } }
    }, 250);
    return () => clearTimeout(timer);
  }, [mp, pre]);

  const go = async () => {
    setBusy(true);
    try {
      const r = await ctx.api.call(`${base}/import`, { json: { node: node.id, ...source, mapping: mp, remember, watch: src === "file" ? watch : null } }); // watch: only what the person chose
      if (src === "paste") prefs.set("rmap:rs:map:" + signature(pre.columns), mp);
      await ctx.api.refresh();
      ctx.toast(t("rmap_results.imp.done", { n: r.added + r.updated }));
      onClose();
    } catch (e) { setError(e.message); setBusy(false); }
  };
  const changes = check ? check.added + check.updated : 0;
  const mine = !!(pre?.existing?.watch && pre.existing.device === pre.device); // this Mac watches the file already
  const other = !!(pre?.existing?.watch && pre.existing.device !== pre.device); // the other Mac does: ticking takes it over
  const near = (files?.files || []).filter((f) => !path || f.path.toLowerCase().includes(path.trim().toLowerCase())).slice(0, 6);

  return html`<div class="rs-import" onKeyDown=${(e) => e.key === "Escape" && onClose()}>
    <div class="imp-head">
      <div class="seg"><button class=${src === "paste" ? "on" : ""} onClick=${() => { setSrc("paste"); setPre(null); }}>${t("rmap_results.imp.paste")}</button>
        <button class=${src === "file" ? "on" : ""} onClick=${() => { setSrc("file"); setPre(null); }}>${t("rmap_results.imp.file")}</button></div>
      <span class="grow" />
      <button class="mp-close" title=${t("rmap.close")} onClick=${onClose}><${Icon} name="x" sm /></button>
    </div>
    ${src === "paste"
      ? html`<textarea class="imp-text rs-mono" rows="5" placeholder=${t("rmap_results.imp.paste_ph")} value=${text} onInput=${(e) => { setText(e.target.value); setPre(null); }} />`
      : html`<div class="imp-file"><input class="rs-mono" placeholder="~/code/experiment/results.csv" value=${path} onInput=${(e) => { setPath(e.target.value); setPre(null); }}
          onKeyDown=${(e) => e.key === "Enter" && ready && read()} />
        ${files?.message && html`<p class="mp-quiet">${files.message}</p>`}
        ${near.length > 0 && html`<div class="imp-near">${near.map((f) => html`<button type="button" key=${f.path} onClick=${() => { setPath(f.path); setPre(null); }} title=${f.path}>
          <span class="rs-mono">${f.path.split("/").pop()}</span><small>${f.path.split("/").slice(0, -1).join("/")}</small></button>`)}</div>`}</div>`}
    <div class="row-end"><button class="btn sm" disabled=${!ready || busy} onClick=${read}>${t("rmap_results.imp.read")}</button></div>
    ${error && html`<div class="err">${error}</div>`}

    ${pre && mp && html`
      <div class="imp-tablewrap"><table class="rs-table imp-sample"><thead><tr>${pre.columns.map((c) => html`<th>${c}</th>`)}</tr></thead>
        <tbody>${pre.rows.map((r) => html`<tr>${pre.columns.map((c) => html`<td>${String(r[c] ?? "")}</td>`)}</tr>`)}</tbody></table></div>
      <p class="mp-quiet">${t("rmap_results.imp.rows", { n: pre.total })}${pre.truncated ? " " + t("rmap_results.imp.truncated") : ""}${pre.remembered ? " · " + t("rmap_results.imp.remembered") : ""}</p>
      <${MappingEditor} pre=${pre} mp=${mp} setMp=${setMp} configs=${check?.configs || pre.configs || []} />
      <div class="imp-foot">
        ${src === "file" && html`<label class="rs-check"><input type="checkbox" checked=${remember} onChange=${(e) => setRemember(e.target.checked)} />${t("rmap_results.imp.remember")}</label>
          <label class="rs-check" title=${other ? t("rmap_results.src.watched_by", { device: pre.existing.device }) : ""}><input type="checkbox" checked=${watch ?? mine}
            onChange=${(e) => setWatch(e.target.checked)} />${t("rmap_results.imp.watch")}${other && html`<small>${pre.existing.device}</small>`}</label>`}
        <span class="grow" />
        ${check && html`<span class="imp-sum rs-mono">${t("rmap_results.imp.sum", { added: check.added, updated: check.updated, unchanged: check.unchanged })}${check.skipped ? " · " + t("rmap_results.imp.skipped", { n: check.skipped }) : ""}</span>`}
        <button class="btn primary sm" disabled=${busy || !check || (!changes && !(src === "file" && (watch || remember)))} onClick=${go}>${t("rmap_results.imp.go")}</button>
      </div>
      ${check?.sample?.length > 0 && html`<ul class="imp-sample-list rs-mono">${check.sample.slice(0, 4).map((s) => html`<li>${s}</li>`)}${check.added + check.updated > 4 ? html`<li>…</li>` : ""}</ul>`}`}
  </div>`;
}
