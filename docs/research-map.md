# Research map (研究地图): data model and extension points

Each project gets its own map that grows with the work: question → paths / hypotheses → sub-lines →
experiments → results, with what is verified, what is blocked and what was dropped. Phase 1 (#45)
builds the data model, the single-project map and editing; later phases plug in through the
extension points below: time replay, change view and the all-projects map (#46), the peer layer
(#47), experiment results (#48).

Code: `app/rmap/` (back end), `web/rmap/` (front end). Tests: `tests/test_rmap.py`.

## Files

A project is a folder in the vault, under `paths.research_maps` in `studio.toml` (default
`Research Maps`, at the top of the vault):

```
Research Maps/
  <slug>/                     folder name from the project title ("Project Atlas" -> project-atlas)
    map.yaml                  project (one record, id "project"), nodes, edges
    evidence.yaml             evidence attachments
    <kind>.yaml               one file per registered attachment kind (peers, results, ...)
    events/2026-10.yaml       the change log, one file per month
    map.canvas                Obsidian Canvas view, generated, read-only (phase 1, second PR)
```

Every file is a sync-safe store (`app/store.py`), the same as `plan.yaml` and `ideas.yaml`:

- every record has an `id`, `created_at` and `updated_at`;
- a delete is a tombstone (`deleted: true`), so the other Mac does not bring it back;
- Syncthing conflict copies (`map.sync-conflict-….yaml`) are merged on the next read, the newest
  `updated_at` winning per record, and moved out of the vault (`<data>/sync-conflicts/`);
- writes take a lock and a temp file in the data dir (`<data>/rmap/`), never in the vault;
- a write that changes nothing leaves the file alone (`Store.editing(skip_unchanged=True)`), so
  Syncthing has nothing to send.

Readers tolerate what sync can produce: an edge or attachment whose node is gone (deleted on the
other Mac) is skipped; a cycle that two Macs created together must not break the layout.

Records are plain YAML and can be read by hand, but edit them in the app: hand edits leave no event.

## Project

`map.yaml → project[0]`: `title`, `line` (research line name as in `profile.lines`), `note` (vault path of
the research-line note), `description`, `archived`.

## Nodes

`map.yaml → nodes[]`

| field | |
|---|---|
| `type` | `question` 问题 · `hypothesis` 路径/假设 · `subline` 子线 · `experiment` 实验 · `result` 结果 · `decision` 决策 · `kill` Kill criteria · `open` 开放问题 |
| `title` | one line, ≤ 200 characters |
| `status` | `todo` 未开始 · `active` 进行中 · `verified` 已验证 · `blocked` 没打通 · `refuted` 证伪 · `dropped` 放弃 |
| `body` | Markdown, optional |
| `pin` | `{x, y}` in map coordinates: the layout keeps the node there; absent = automatic |
| `order` | number, optional: order among siblings (else creation time) |
| `tags` | list of words, optional |
| `ext` | `{<extension key>: any}`: data an extension keeps on the node; a change replaces only the keys it names |

The first five types have a column (0–4, left to right: 问题 · 路径 · 子线 · 实验 · 结果); decisions,
Kill criteria and open questions sit next to their parent. Refuted and dropped nodes stay on the map,
faded. `verified`, `refuted` and `dropped` count as closed. Collapsing and focusing are view state
(per Mac, in the browser), not part of the map.

## Edges

`map.yaml → edges[]`: `from`, `to` (node ids), `kind`, `label` (optional, ≤ 80 characters).

| kind | | direction |
|---|---|---|
| `refines` 细分 | `to` is a narrower part of `from` (question → path → sub-line → experiment → result) | left to right |
| `depends` 依赖 | `to` needs `from` first | left to right |
| `alternative` 备选 | two ways to the same end | lateral, symmetric |
| `conflicts` 冲突 | both cannot hold | lateral, symmetric |

`refines` and `depends` decide the layers of the layout and must stay acyclic (a write that would close
a cycle is refused). One edge of a kind per pair (either direction for the lateral kinds). Deleting a
node tombstones its edges and attachments too.

## Attachments

Things hung on a node. Each kind has its own file and the same generic routes; every record has
`node` (the node id) plus the kind's fields.

| kind | file | fields | phase |
|---|---|---|---|
| `evidence` 证据 | `evidence.yaml` | `type`: `paper` (paper note name) / `note` (vault path) / `file` (vault path) / `url`; `target`; `title`; `note`; `stance`: `supports` / `contradicts` / `context` | 1 |
| peers 同行 | registered by #47 | e.g. paper / scholar / lab, relation: same approach / competing / builds on | 3 |
| results 结果记录 | registered by #48 | e.g. config, dataset, metric, value, date, source | 4 |

## Events

`events/<YYYY-MM>.yaml → events[]`, one record per changed record, written after the change itself.
Events are never edited; conflict copies of a month merge as a union.

| field | |
|---|---|
| `at` | when (local ISO time, seconds) |
| `who` | `user` · `assistant` · `cli` · `import` · `system` |
| `device` | which Mac (`STUDIO_DEVICE` / host name) |
| `kind` | `project` · `node` · `edge` · an attachment kind · `log` |
| `action` | `add` · `update` · `delete` · `note` (log entries) |
| `target` | id of the changed record |
| `nodes` | node ids the event is about (one for a node or attachment, two for an edge) |
| `title` | node title at that time, for a readable feed |
| `record` | snapshot of the record, on add and delete |
| `changes` | `{field: [old, new]}`, on update |
| `summary` | log text, or a one-line description of an attachment |
| `group` | shared by all events of one batch (one user action, one confirmed proposal) |

A `log` event carries a free sentence ("ran the ablation, tool use did not help on long sessions"):
the one-sentence update is stored as its node changes + evidence + that log line, all in one group.
Replay (#46) rebuilds the map at a date from `record` / `changes`; hand edits and data older than the
log fall back to `created_at` / `updated_at`.

## Writing: one path

Every change goes through `app/rmap/ops.apply(slug, ops, who=, summary=)`: a batch validated as a
whole and written under the map's lock (all or nothing), then logged. Ids in a batch may be `$name`
refs to records added earlier in it.

```json
[{"op": "node.add", "ref": "$h", "set": {"type": "hypothesis", "title": "Tool use helps"}, "parent": "n1a2b3c4"},
 {"op": "attach.add", "kind": "evidence", "node": "$h", "set": {"type": "paper", "target": "Attention 2017"}},
 {"op": "node.update", "id": "n5d6e7f8", "set": {"status": "verified"}},
 {"op": "edge.add", "from": "$h", "to": "n5d6e7f8", "kind": "depends"},
 {"op": "log", "text": "first ablation done"}]
```

Operations: `node.add / update / delete`, `edge.add / update / delete`, `attach.add / update / delete`,
`log`, `project.update`. The CLI (#48) and the assistant's confirmed proposals use the same call.

## HTTP

All under `/api/rmap` (`app/rmap/api.py`). Edits from the page are the user's own, like the reading list
and the plan: no approval card. The assistant only proposes; the user confirms.

| | |
|---|---|
| `GET /meta` | node types (with columns), statuses, edge kinds, attachment kinds |
| `GET /ext` | front-end extension files in `web/rmap/ext/` |
| `GET /stamp` | changes whenever any map's files change |
| `GET /projects?archived=` · `POST /projects` | list (with status counts) · create `{title, line?, slug?, note?, description?}` |
| `GET /projects/{slug}?deleted=` · `PATCH` | the whole map (`deleted=true` keeps tombstones, for replay) · project fields |
| `GET /projects/{slug}/stamp` | change stamp of one map: poll it for live refresh |
| `GET /projects/{slug}/events?before=&since=&node=&kind=&limit=` | the log, newest first |
| `POST /projects/{slug}/batch` | `{ops, summary?, who?}` |
| `POST /projects/{slug}/nodes` · `PATCH` · `DELETE /nodes/{id}` | `POST` takes node fields + `parent`, `edge` |
| `POST /projects/{slug}/edges` · `PATCH` · `DELETE /edges/{id}` | |
| `GET` · `POST /projects/{slug}/attachments/{kind}` · `PATCH` · `DELETE …/{id}` | any registered kind |

Live refresh: the change stamp hashes the names, sizes and times of the map's YAML files (conflict
copies included, the generated canvas not), so an edit here, a `studio map` command or a file synced
from the other Mac all show up. The page polls it every 2 s while visible, then reloads the map and
reads the events since the last one it saw.

## Back-end extension points

`app/rmap/registry.py`. A later phase adds a module under `app/rmap/plugins/` (imported once, in name
order; a failing one is logged and skipped) and does not edit the core files:

- `attachment_kind(AttachmentKind(name, prefix, fields={field: validator}, required=(), file="", node_types=(),
  summary=None, extra={}))`: a new kind gets its own file, the generic routes, cascade on node delete,
  events, and its `extra` in `/meta`. A validator takes a value and returns the clean one (or raises
  `ValueError`).
- `on_event(fn)`: `fn(slug, events)` after every write, in the writing thread (keep it quick; errors are
  logged, never raised into the write).
- `router`: a module-level `APIRouter` is mounted before the core routes. Use paths under `/api/rmap/` that
  are not core paths, e.g. `/api/rmap/overview`, `/api/rmap/projects/{slug}/results/import`.
- Node data that is not an attachment goes in `node.ext[<key>]`.

## Front-end extension points

`web/rmap/registry.js`. Every `.js` file in `web/rmap/ext/` is imported by the page (name order) and
every `.css` file is linked; register through `rmap`:

| slot | spec | used for |
|---|---|---|
| `rmap.layer` | `{id, label, order, default, accent, node(n, box, ctx), nodeClass(n, ctx), svg(ctx), legend(ctx)}` | map layers toggled in the toolbar: per-node SVG decorations, an extra class on a node (e.g. the aqua dashed 空白 outline), overlays in map coordinates |
| `rmap.section` | `{id, label, order, when(n, ctx), render(n, ctx)}` | detail panel sections of the selected node (peers, results) |
| `rmap.tool` | `{id, label, icon, order, run(ctx)}` or `{…, render(ctx)}` | toolbar buttons and small controls (time scrubber) |
| `rmap.view` | `{id, label, order, render(ctx)}` | whole-page views in the header's segmented control, before the projects (总图) |
| `rmap.kpi` | `{id, order, render(ctx)}` | KPI tiles above the map |
| `rmap.strings` | `{zh: {...}, en: {...}}` | the extension's own UI strings |

Labels are string keys passed through `t()`. Registering the same id again replaces the item.

Events: `rmap.on(name, fn)` returns an unsubscribe function. `load` `{slug, map}` after a map is read;
`change` `{slug, events}` with new log events (own edits, CLI, the other Mac); `select` `{slug, node}`;
`view` `{id}`.

Every hook gets `ctx`:

| | |
|---|---|
| `slug`, `map` | `{project, nodes, edges, attachments: {kind: [...]}, stamp}` as `GET /projects/{slug}` returns it |
| `meta`, `projects` | `/meta`; the project list with counts |
| `selected`, `select(id)` | the selected node |
| `layers` | `{layer id: on}` |
| `layout` | in layer hooks: `{boxes: {node id: {x, y, w, h, col}}, width, height}` in SVG map coordinates |
| `api` | `apply(ops, summary)` (a batch, then refresh), `refresh()`, `base` (the project's URL), `call` (`api` of lib.js) |
| `t`, `toast`, `onAsk(prompt, label)`, `onOpenNote(path)`, `onOpenPaper(stem)` | strings, a toast, hand a task to the workbench assistant, open a vault note or a paper |

## Strings

All research map UI text is in `web/rmap/strings.js` (zh and en, `t(key, params)`), ready to move into the
i18n framework of #49. In the Chinese UI technical terms stay English: Gate, Kill criteria, Claim Gate,
baseline, ablation, metric names such as Recall@10.
