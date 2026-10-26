// Time travel on the research map page (#46): the all-projects map (总图) and the history of one map
// (time scrubber with play, change view between two dates). Components: /rmap/overview.js, /rmap/history.js.
// i18n: done
import { useCallback, useEffect, useState } from "preact/hooks";
import { html, api, prefs } from "/lib.js";
import { t } from "/i18n.js";
import { rmap } from "/rmap/registry.js";
import { useStamp } from "/rmap/page.js";
import { Overview } from "/rmap/overview.js";
import { HistoryPanel } from "/rmap/history.js";

let lastSlug = prefs.get("rmap:history:slug", null);
rmap.on("load", ({ slug }) => { lastSlug = slug; });

// The history needs a project. On a page that hands project views the selected map (ctx.slug), it is that
// one; otherwise the view picks one itself: the map seen last, kept per Mac.
function History({ ctx }) {
  const own = !ctx.slug;
  const [slug, setSlug] = useState(() => (ctx.projects.some((p) => p.slug === lastSlug) ? lastSlug : ctx.projects[0]?.slug || null));
  const [map, setMap] = useState(null);
  const base = slug && `/api/rmap/projects/${encodeURIComponent(slug)}`;
  const load = useCallback(() => (base ? api(base).then((m) => setMap((old) => (old?.slug === m.slug && old.stamp === m.stamp ? old : m))) : Promise.resolve()), [base]);
  useEffect(() => { if (own && slug) { setMap(null); prefs.set("rmap:history:slug", slug); lastSlug = slug; load().catch(() => {}); } }, [own, slug]);
  useStamp(own && base && `${base}/stamp`, map?.stamp, load);
  if (!own) return html`<${HistoryPanel} ctx=${ctx} />`;
  if (!ctx.projects.length) return html`<p class="muted">${t("rmap_history.no_projects")}</p>`;
  const pctx = map && map.slug === slug && { ...ctx, slug, map, api: { ...ctx.api, base, refresh: load } };
  return html`<div class="mp-hx-own">
    <div class="mp-hx-pick">
      <span class="mp-quiet">${t("rmap_history.pick")}</span>
      <div class="seg">${ctx.projects.map((p) => html`<button key=${p.slug} class=${p.slug === slug ? "on" : ""} onClick=${() => setSlug(p.slug)}>${p.title}</button>`)}</div>
    </div>
    ${pctx ? html`<${HistoryPanel} ctx=${pctx} />` : html`<p class="muted">${t("rmap_history.loading")}</p>`}
  </div>`;
}

rmap.view({ id: "overview", label: "rmap_history.view.overview", order: 0, render: (ctx) => html`<${Overview} ctx=${ctx} />` });
rmap.view({ id: "history", label: "rmap_history.view.history", icon: "clock", order: 10, scope: "project",
  render: (ctx) => html`<${History} ctx=${ctx} />` });
