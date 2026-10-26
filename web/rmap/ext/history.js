// Time travel on the research map page (#46): the all-projects map (总图, a page view) and the history of the
// selected map (a project view: time scrubber with play, change view between two dates).
// Components: /rmap/overview.js, /rmap/history.js.
// i18n: done
import { html } from "/lib.js";
import { rmap } from "/rmap/registry.js";
import { Overview } from "/rmap/overview.js";
import { HistoryPanel } from "/rmap/history.js";

rmap.view({ id: "overview", label: "rmap_history.view.overview", order: 0, render: (ctx) => html`<${Overview} ctx=${ctx} />` });
rmap.view({ id: "history", label: "rmap_history.view.history", icon: "clock", order: 10, scope: "project",
  render: (ctx) => html`<${HistoryPanel} key=${ctx.slug} ctx=${ctx} />` });
