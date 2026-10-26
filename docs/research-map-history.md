# Research map: time travel, change view and the all-projects map (#46)

Phase 2 of the research map ([docs/research-map.md](research-map.md) is the contract). Everything here
reads the maps and their change logs; nothing writes. Code: `app/rmap/history.py` (replay, diff, frames,
overview, advisor packets), `app/rmap/plugins/history.py` (routes), `web/rmap/timeline.js` (pure helpers),
`web/rmap/history.js` (scrubber and change view), `web/rmap/overview.js` (metro map),
`web/rmap/ext/history.js` + `.css` (registration). Tests: `tests/test_rmap_history.py`,
`tests/js/rmap_history.test.mjs`.

## Replay

The map at a time is rebuilt from the log, never stored:

- **Order.** Events are applied in log order, `(hlc, id)`, with the merge rules of `records.py`: a field
  holds its latest write, a delete is a write, a record written after its delete is live again, and the
  edges and attachments a node delete took along come back with the node (the cascade is told from the
  batch: same device, same `at`, same `group`).
- **Time.** "The map on 10-05" is every change *made* by the end of that day, by the event's `at` (the
  wall clock of the Mac that made it), merged in log order. A clock stamp can run ahead of the wall
  clock (it never goes back on a Mac), so stamps decide the order, not the day.
- **What the log does not know.** A record with no `add` event (a hand edit, a map older than the log)
  appears at its `created_at` with the values it had before its logged updates; a delete with no event
  happens at its `deleted` stamp. Attachment kinds logged per batch take their records' current fields.
- Bounds: a date (`2026-10-05`: its end for "at" and "until", its start for "since"), a local time
  (`2026-10-05T09:00`), or an event cursor (`<hlc>|<id>`: up to and including that event).

## Routes

| | |
|---|---|
| `GET /api/rmap/projects/{slug}/history` | the time scrubber's frames: weeks `W1 … Wn` (Monday to Sunday, from the map's first change to this week, at most 260; each with `events`, `nodes`, `verified` and its log lines in `notes`) and, per record (`project`, `nodes`, `edges`, `attachments: {kind}`), its state at the end of each week where it changed: `[week, record \| null]` |
| `GET /api/rmap/projects/{slug}/history/at?at=` | the map at a time, shaped like `GET /projects/{slug}` |
| `GET /api/rmap/projects/{slug}/history/diff?since=&until=` | from the map at `since` (none: before the first change) to the map at `until` (none: now): `map` (the later map with what was deleted as ghosts, `gone: true`), `marks` (`added`, `removed`, `dropped`, `status`, `retitled` per node), `changes` (lists by kind, edge counts, attachments added/removed, log lines), `events` (newest first, at most 300) and `total` |
| `GET /api/rmap/projects/{slug}/history/packet` | the last advisor packet of the map's research line (by `line` / `note`), for the change view's preset |
| `GET /api/rmap/overview` | every map on one axis: `weeks`, `now`, `lines` (per project: `start`, `end`, counts, `stations`) and `interchanges` |

Timelines are cached per map by its change stamp.

## The page

- **总图 / All projects** (`rmap.view` `overview`, first in the header): a metro map. A project is a
  line from its first change to now (or to its archiving); its stations are key validations (a
  hypothesis, sub-line, experiment or result validated) and decisions (a decision node, a node falsified
  or abandoned, a Kill criterion partly triggered or triggered: status `active` / `blocked`), one station
  per day. An entity that several projects carry (`entity` on a node or an attachment, #53) is an
  interchange joining their lines at the time it became shared, i.e. when the last of them took it up.
  Click a station or an interchange for the details. It polls `/api/rmap/stamp` to follow edits.
- **回放 / Replay** (project view `history`): the time scrubber, mono week ticks with the week's activity
  above them, a play button (one week per 0.9 s), and the map of that week drawn by the core map view,
  read-only (no menu, rename or add keys; a write is refused with a toast). The changes of that week are
  marked and listed under the map (with that week's change log).
- **对比 / Compare**: two dates (presets: the last 7 days, the last 4 weeks, since the last advisor packet,
  everything). Added nodes glow aqua, abandoned, falsified and deleted ones are red (deleted ones as dashed
  ghosts, with their links), status changes are outlined in blue with a `from → to` pill, renames get a
  pill; the list below groups them and shows the change log of the period.
- The marks are a layer, `history`, registered only while the replay or the change view is open.
- 回放 is a project view (`rmap.view` with `scope: "project"`, added to the page for it): the 回放 button
  in the toolbar opens it for the selected project in place of the KPI row, the map and the detail panel;
  a second click or another project closes it. The read-only card of a node that is still on the map can
  close the history and select it there (`ctx.closeView()`). Where the page offers `ctx.open(slug, nodeId)`
  (#47), the 总图's stations and interchanges open the map at their node.

## Advisor packets

`line_chat.packet_prompt` (起草导师汇报包) adds the map changes since the line's last advisor packet (its
date included; since the map began when there is none): new nodes, status changes, deletions, renames,
new evidence and the log lines of the period (`history.packet_changes`). The answer carries
`map: {slug, since, total}`. A line without a map, or a map that cannot be read, leaves the prompt as it
was.
