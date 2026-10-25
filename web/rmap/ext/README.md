# Research map extensions

Every `.js` file in this folder is imported by the research map page (in name order) and every `.css`
file is linked; `GET /api/rmap/ext` lists them. Files starting with `.` or `_` are skipped.

An extension registers what it adds through `/rmap/registry.js` and never edits the core files:

```js
import { html } from "/lib.js";
import { rmap } from "/rmap/registry.js";

rmap.strings({ zh: { "rmap.layer.peers": "同行层" }, en: { "rmap.layer.peers": "Peers" } });
rmap.layer({ id: "peers", label: "rmap.layer.peers", accent: "var(--rm-peer)", node: (n, box, ctx) => html`<g>…</g>` });
```

The full contract (slots, `ctx`, events, back-end plugins) is in `docs/research-map.md`.
