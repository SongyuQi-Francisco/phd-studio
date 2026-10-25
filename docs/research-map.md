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
  <slug>/                     "Project Atlas" -> project-atlas-3f9c (title slug + 4 hex of the project uid)
    map.yaml                  project (one record, id "project"), nodes, edges
    evidence.yaml             evidence attachments
    <kind>.yaml               one file per registered attachment kind (peers, results, ...)
    events/2026-10.yaml       the change log, one file per month
    map.canvas                Obsidian Canvas view: generated, read-only (second PR)
```

Folder names are lower case (the Mac's disk ignores case, so `Foo` and `foo` would be one folder with two
locks) and carry part of the project's random `uid`, so two Macs that create the same title while apart
get two folders, never one silently merged map. A title already in use (ignoring case) is refused; two
same-titled maps that arrive from two Macs are both kept and flagged (`same_title` in the listing).

Records are plain YAML and can be read by hand, but edit them in the app: hand edits leave no event and
no clock stamp (see below), so the other Mac's copy may win over them.

## Syncing two Macs

Every file is a store built on `app/store.py` (locked read-modify-write, temp files and locks in the data
dir under `rmap/`, never in the vault; a write that changes nothing leaves the file alone; Syncthing
conflict copies are merged on the next read and moved to `<data>/sync-conflicts/`). The map and
attachment files merge **field by field** (`app/rmap/records.py`); the other stores of the app keep
merging whole records by `updated_at`.

- **Clock stamps.** Every field a write sets (or clears) gets a stamp in the record's `clock`:
  `{title: "1793001234567.0000.macbook", status: …}`. Stamps come from a hybrid logical clock
  (`app/rmap/clock.py`): milliseconds that follow the wall clock but never go backwards on a Mac and never
  fall behind a stamp already seen in the map, a counter for writes in the same millisecond, and the
  device name as tie-break, so no two writes compare equal. Text order is time order.
- **Merge.** For each field the later stamp wins. Two Macs that change different fields of a node both
  keep their change; a cleared field stays cleared against an older copy. Records written before stamps
  existed count as written whole at their `updated_at`.
- **Delete and resurrect.** A delete is a field too (`deleted: true` with its stamp). A record changed
  after it was deleted (on a Mac that had not seen the delete) is live again, and so is everything the
  delete took along: a node delete tombstones its edges and attachments with `deleted_by: <node id>`, and
  they follow that node back. An edge or attachment deleted on its own stays deleted. A delete stamped
  after the edit wins.
- **Cycles.** `refines` and `depends` must stay acyclic; a write that would close a cycle is refused. Two
  Macs can still make one between them (A → B on one, B → A on the other). Reading a map marks the edge
  that closes it, taken in creation order so both Macs mark the same one, with `cycle: true`, and adds a
  `{kind: "cycle", edges: [...]}` warning. Layouts and metrics walk `dag_edges(map)` (Python) /
  `dagEdges(map, meta)` (`web/rmap/util.js`), which leave marked edges out.
- **Missing nodes.** An edge or attachment whose node is gone is not shown.

## Project

`map.yaml → project[0]`: `title`, `line` (research line name as in `profile.lines`), `note` (vault path of
the research-line note), `description`, `archived`, `uid` (random, set once).

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
| `entity` | optional, see "Entities": what this node is across projects (a shared dataset, baseline) |
| `ext` | `{<extension key>: value}`: data an extension keeps on the node; a change replaces only the keys it names. A plugin validates its key with `ext_field`; a key no plugin registered holds at most 4000 characters of JSON |

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

One edge of a kind per pair (either direction for the lateral kinds).

## Attachments

Things hung on a node. Each kind has its own file and the same generic routes; every record has
`node` (the node id), an optional `entity`, and the kind's fields.

| kind | file | fields | phase |
|---|---|---|---|
| `evidence` 证据 | `evidence.yaml` | `type`: `paper` (paper note name) / `note` (vault path) / `file` (vault path) / `url`; `target`; `title`; `note`; `stance`: `supports` / `contradicts` / `context` | 1 |
| peers 同行 | registered by #47 | e.g. paper / scholar / lab, relation: same approach / competing / builds on | 3 |
| results 结果记录 | registered by #48 | e.g. config, dataset, metric, value, date, source | 4 |

A `url` target opens only with `http`, `https`, `obsidian` or `zotero`; a paper name, note or file is a
plain vault-relative path (no scheme, no leading `/` or `~`, no `..`). The front end opens links through
`safeUrl` (`web/rmap/util.js`) as well.

## Entities

Things several projects point at (#46 shared stations, #47 shared peers) are named by a key
`<namespace>:<key>`, the same on every Mac without any registry to keep in sync: `paper:2310.11511`
(arXiv id) or `paper:<note name>`, `doi:10.1145/…`, `openreview:<forum>`, `acl:<id>`,
`scholar:openalex:A…`, `lab:<slug>`, `dataset:msmarco`, `metric:recall@10`, `method:colbert`. Nodes and
attachment records carry it in `entity`; `GET /api/rmap/entities` (`project.entities()`) lists where each
one appears, across all maps.

## Events

`events/<YYYY-MM>.yaml → events[]`, one record per changed record, written after the change itself.
Events are never edited; conflict copies of a month merge as a union.

| field | |
|---|---|
| `hlc` | clock stamp of the change (the same stamp as the fields it wrote); with `id`, the order of the log |
| `at` | when (local ISO time, seconds), for people |
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
| `targets`, `count` | instead of `target`, for an attachment kind logged per batch (bulk imports) |

The log is ordered by `(hlc, id)` and paged by a cursor `"<hlc>|<id>"`: nothing is lost or repeated,
however many events share a second. Events written before stamps existed sort by `at` and their place in
the file.

Each Mac also numbers events in the order it first sees them, written here or synced in
(`Project.ingest`, state in `<data>/rmap/ingest/<slug>.json`): a live feed asks for `after=<seq>` and gets
everything new, including events an offline Mac wrote last week that only arrived now. The first time a
Mac looks at a map it only records what is there.

A `log` event carries a free sentence ("ran the ablation, tool use did not help on long sessions"):
the one-sentence update is stored as its node changes + evidence + that log line, all in one group.
Replay (#46) rebuilds the map at a time from `record` / `changes` in `(hlc, id)` order, with the delete
and resurrect rule above; hand edits and data older than the log fall back to `created_at` /
`updated_at`.

## Writing: one path

Every change goes through `app/rmap/ops.apply(slug, ops, who=, summary=)`: a batch validated as a
whole and written under the map's lock (all or nothing), then logged. Ids in a batch may be `$name`
refs to records added earlier in it. `ops.simulate(slug | None, ops)` runs the same checks without
writing (None: against a new, empty map) and returns the events the batch would log.

```json
[{"op": "node.add", "ref": "$h", "set": {"type": "hypothesis", "title": "Tool use helps"}, "parent": "n1a2b3c4"},
 {"op": "attach.add", "kind": "evidence", "node": "$h", "set": {"type": "paper", "target": "Attention 2017"}},
 {"op": "node.update", "id": "n5d6e7f8", "set": {"status": "verified"}},
 {"op": "edge.add", "from": "$h", "to": "n5d6e7f8", "kind": "depends"},
 {"op": "log", "text": "first ablation done"}]
```

Operations: `node.add / update / delete`, `edge.add / update / delete`, `attach.add / update / delete`,
`log`, `project.update`. A batch holds up to 400 operations; with `who="import"` or `"cli"` up to 20000
(a results CSV is still one atomic batch). The CLI (#48) and the assistant's confirmed proposals use the
same call.

## HTTP

All under `/api/rmap` (`app/rmap/api.py`). Edits from the page are the user's own, like the reading list
and the plan: no approval card. The assistant only proposes; the user confirms.

| | |
|---|---|
| `GET /meta` | node types (with columns), statuses, edge kinds, attachment kinds, batch limits |
| `GET /ext` | front-end extension files in `web/rmap/ext/` |
| `GET /stamp` | changes whenever any map's files change |
| `GET /projects?archived=` · `POST /projects` | list (status counts, warnings) · create `{title, line?, slug?, note?, description?}` |
| `GET /entities?key=` | where each entity appears, across maps |
| `GET /projects/{slug}?deleted=` · `PATCH` | the whole map with `warnings` and this Mac's ingest `seq` (`deleted=true` keeps tombstones, for replay) · project fields |
| `GET /projects/{slug}/stamp` | change stamp of one map: poll it for live refresh |
| `GET /projects/{slug}/events?before=&since=&after=&node=&kind=&limit=` | the log, newest first: `{events (each with cursor, seq), next, seq}`; `before` / `since` take a cursor or a date; pass `next` back as `before` for the next page; `after` is an ingest sequence |
| `POST /projects/{slug}/batch` | `{ops, summary?, who?}` |
| `POST /projects/{slug}/nodes` · `PATCH` · `DELETE /nodes/{id}` | `POST` takes node fields + `parent`, `edge` |
| `POST /projects/{slug}/edges` · `PATCH` · `DELETE /edges/{id}` | |
| `GET` · `POST /projects/{slug}/attachments/{kind}` · `PATCH` · `DELETE …/{id}` | any registered kind |

Live refresh: the change stamp hashes the names, sizes and times of the map's YAML files (conflict
copies included, the generated canvas not), so an edit here, a `studio map` command or a file synced
from the other Mac all show up. The page polls it every 2 s while visible, then reloads the map and
reads the events after the last ingest sequence it handed on.

## Back-end extension points

`app/rmap/registry.py`. A later phase adds a module under `app/rmap/plugins/` and does not edit the core
files. Plugins are imported once, in name order, the first time any research map code needs the
registry: the page, the CLI, the assistant's tools and tests all get them (`registry.kinds()`); a
failing plugin is logged and skipped.

- `attachment_kind(AttachmentKind(name, prefix, fields={field: validator}, required=(), file="",
  node_types=(), summary=None, check=None, log="each", extra={}))`: a new kind gets its own file, the
  generic routes, cascade on node delete, events, and its `extra` in `/meta`. A validator takes a value
  and returns the clean one (or raises `ValueError`); `check(record)` sees the whole record after each
  add or change; `log="batch"` logs one event per batch and action (`targets`, `count`) instead of one
  per record, for kinds filled by bulk imports.
- `ext_field(key, validator)`: validate what the plugin keeps in `node.ext[key]`.
- `on_event(fn)`: `fn(slug, events)` with the events this Mac had not seen yet, oldest first: after every
  write here, and whenever events from the other Mac arrive (a background watcher, `app/rmap/watch.py`,
  looks every 5 s while the app runs; reading a map does too). Each event reaches subscribers once per
  Mac. It runs in the thread that found the events; keep it quick (errors are logged, never raised).
- `router`: a module-level `APIRouter` is mounted before the core routes. Use paths under `/api/rmap/` that
  are not core paths, e.g. `/api/rmap/overview`, `/api/rmap/projects/{slug}/results/import`.

## Front-end extension points

`web/rmap/registry.js`. Every `.js` file in `web/rmap/ext/` is imported by the page (name order) and
every `.css` file is linked, before the page reads its first map; register through `rmap`:

| slot | spec | used for |
|---|---|---|
| `rmap.layer` | `{id, label, order, default, accent, node(n, box, ctx), nodeClass(n, ctx), svg(ctx), under, legend(ctx)}` | map layers toggled in the toolbar: per-node SVG decorations, an extra class on a node (e.g. the aqua dashed 空白 outline), overlays in map coordinates (`under: true` draws them below the edges) |
| `rmap.section` | `{id, label, order, when(n, ctx), render(n, ctx)}` | detail panel sections of the selected node (peers, results) |
| `rmap.tool` | `{id, label, icon, order, run(ctx)}` or `{…, render(ctx)}` | toolbar buttons and small controls (time scrubber) |
| `rmap.view` | `{id, label, order, render(ctx)}` | whole-page views in the header's segmented control, before the projects (总图) |
| `rmap.kpi` | `{id, order, render(ctx)}` | KPI tiles above the map |
| `rmap.strings` | `{zh: {...}, en: {...}}` | the extension's own UI strings |

Labels are string keys passed through `t()`. Registering the same id again replaces the item.
CSS: the page root has class `rmap`; research map classes start with `mp-` and color tokens with `--mp-`
(`--mp-verified`, `--mp-active`, `--mp-blocked`, `--mp-faded`, `--mp-peer`, …, set for light and dark).
`web/rmap/util.js` has `safeUrl(u)` and `dagEdges(map, meta)`.

Events: `rmap.on(name, fn)` returns an unsubscribe function. `load` `{slug, map}` after a map is read;
`change` `{slug, events}` with the events this Mac saw since the last `change` (own edits, CLI, the other
Mac, late ones included); `select` `{slug, node}`; `view` `{id}`.

Every hook gets `ctx`:

| | |
|---|---|
| `slug`, `map` | `{project, nodes, edges, attachments: {kind: [...]}, warnings, stamp, seq}` as `GET /projects/{slug}` returns it |
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
