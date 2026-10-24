# PhD Studio

A local reading workbench for research papers: a PDF reader with a Claude co-reader,
built on top of an Obsidian vault and a Zotero library you already have.

- Everything runs on your machine. The vault and Zotero stay the source of truth.
- Claude runs through your own Claude subscription via the [Claude Agent SDK](https://docs.claude.com/en/docs/agent-sdk/overview).
- No build step: Python (FastAPI) backend, Preact + pdf.js frontend served as static files.

## Features

- **Library** (⌘K): one row per paper note, ordered by your reading-queue note.
  PDFs are taken from the vault, or downloaded once (Zotero attachment URL, arXiv, OpenReview, ACL Anthology, ACM) into a local cache.
- **Reader**: pdf.js viewer with outline, find (⌘F) and zoom.
  - Select text → **翻译 / Translate** (DeepSeek or Google; the engine used is shown), **讲解 / Explain**, **Ask Claude**, **摘录 / Excerpt**.
  - Excerpt appends one line, `- "quote" [p.N] — your comment`, to a section of the paper's note (default: a new `## Excerpts`). You see the line before it is written, and can undo it from the toast (byte for byte, unless the note was edited in between; then just that line goes). The quote is escaped, so `<tags>` or `$` from the PDF stay text; the note is rewritten in place (same file, creation time and line endings).
  - ⌥-drag (or the crop tool) on a figure, table or formula → the actual image is sent to Claude.
  - **Bilingual / translated PDF** of the whole paper via [pdf2zh-next](https://github.com/PDFMathTranslate/PDFMathTranslate-next) (keeps formulas and layout), generated in the background.
- **Claude co-reader**: one persistent session per paper (resumed after restarts), streamed.
  Claude can read the vault, the paper's full text and the web. Any edit to the vault is shown as a diff and only applied after you approve it. If Claude is unavailable you get an error, never a silent switch to another model.
- **Note**: the paper's Obsidian note, with an Obsidian deep link and "ask Claude to update".
  Page references in notes and chat ([p.5], p. 5, pp. 4-5) jump to that page; section references (§5.1, Section 3) jump to that section, via the PDF outline or, without one, the heading in the text.
- **Claim Gate audit**: one click checks every claim in the note against the full text and primary sources (venue status, code), shows an audit table, then proposes edits to the note's Claim Gate section for your approval.
- **Reading list**: `inbox → T1/T2/T3 → reading → done`, plus snooze and dismiss.
  - Every push records the date, source and a required one-line reason.
  - Urgency (tier) is set before reading. Depth (skim/deep), verdict (key/useful/irrelevant) and a one-line takeaway are recorded after; Claude can draft the takeaway.
  - Keyboard triage: `1/2/3`, `S`, `X`, `D`, `J/K`, `↵`.
  - Opening a paper marks it as reading.
  - Inbox items left for 3 weeks fold into an archive.
  - Data lives in one YAML file in the vault, and a summary block is regenerated at the top of the reading-queue note.

### Pushing papers from other agents or scripts

```bash
./studio push --paper "Attention 2017" --tier T1 --line "Project A" --reason "what to look for" --by "planning session"
./studio push --arxiv 2606.17328 --reason "cited by X; check their metric" --by "weekly tracker" --source tracker
./studio list --status inbox
./studio done "Attention 2017" --depth deep --verdict key --takeaway "..."
```

`--reason` is required. Papers already read or dismissed are not re-added. The CLI works with or without the server running.
Tell your agents about it in the vault rules file (e.g. `AGENTS.md`).

## Setup

Requirements: macOS or Linux, [uv](https://docs.astral.sh/uv/), the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) with a Claude plan.

```bash
cp studio.example.toml studio.toml   # set your vault, Zotero and profile
./start.sh                            # http://127.0.0.1:8770
```

Claude login for a long-running local service (a one-year token from your subscription):

```bash
claude setup-token
read -s "T?token: " && printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$T" > ~/.config/secrets/claude.env && chmod 600 ~/.config/secrets/claude.env && unset T
```

Desktop app (macOS): `launcher/make_app.sh` builds `PhD Studio.app` on your Desktop: a small native entry point (`launcher/stub.c`) that runs the workbench in a WebKit window, so it shows up as "PhD Studio" in the Dock and ⌘Tab.
Double-click to open; closing the window or ⌘Q stops everything. Start it with `STUDIO_DEBUG=1 .venv/bin/python -m app.desktop` to enable a local JS-eval hook for testing.
The power button in the sidebar shuts the server down. Server logs go to `~/Library/Logs/PhD Studio.log`.

Optional: `DEEPSEEK_API_KEY=...` in `~/.config/secrets/deepseek.env` for fast selection translation and bilingual PDFs; without it Google Translate is used.

### Vault conventions

- One note per paper in the papers folder; YAML frontmatter may carry `zotero: zotero://select/library/items/KEY`, `arxiv:`, `source:`, `status:`, `year:`, `tags:`.
- The reading-queue note lists papers as `[[Note Name]]` under `##` sections.
- An optional rules file (e.g. `AGENTS.md`) is given to Claude verbatim and must be followed for every edit.

## Tests

```bash
uv run pytest
```

The suite runs against a throwaway vault, data dir and state dir created by `tests/conftest.py` (it points `STUDIO_CONFIG` at a generated config, so no `studio.toml` is needed and your real vault, Zotero and `state/` are never touched). The network is blocked; venue data comes from small fixtures. CI runs it on every push and pull request; a PR needs the `tests` check to pass before it can merge.

## Where data lives

| What | Where |
|---|---|
| Notes, queue, rules | your vault (read; written only through approved Claude edits) |
| Chat logs, session ids, settings | `state/` (git-ignored) |
| Downloaded PDFs, bilingual PDFs, text dumps, crops | `~/Library/Application Support/PhD Studio/` or `~/.local/share/phd-studio/` |
| Secrets | `~/.config/secrets/*.env` |

## Using it on two Macs (Syncthing or similar)

The project folder and the vault can live in synced folders:

- **Chat state is per machine.** Chat logs, Claude session ids and settings go to `state/<hostname>/`, so each Mac only writes its own subfolder. If a session can't be resumed on a machine, a fresh one starts.
- **The reading list merges itself.** Every item in `reading.yaml` has an `updated_at`. If both Macs edit the list before syncing and the sync tool leaves a `reading.sync-conflict-*.yaml`, the next read merges it (the newest edit per item wins; the same paper pushed on both sides is deduplicated). The conflict copy is then moved to `<data>/sync-conflicts/`.
- **The summary is its own file.** `Reading List.md` is a separate, generated note, so it never collides with your hand-edited queue note. Conflict copies of it are simply regenerated.
- **Writes are atomic.** The list is written to a temp file outside the vault and moved into place in one step.
- **Large files stay local.** Downloaded and translated PDFs live in the per-machine data dir and are never synced.
- **Don't sync `.git`.** Exclude the project's `.git` from sync, e.g. add `/phd_studio/.git` to `.stignore` on *each* machine (ignore files are not synced), and get code updates from GitHub.
- **Per-machine setup.** On the second Mac, run `launcher/make_app.sh` to build its own launcher. The `.venv` is created there on first start.

## Code map

- `app/main.py` routes · `library.py` notes, Zotero, PDFs · `claude_chat.py` Agent SDK sessions and edit approvals
  · `translate.py` selection translation · `bilingual.py` pdf2zh-next jobs · `config.py` paths and settings
- `web/` `main.js` app shell, library, reader · `chat.js` Claude and note panels · `pdfview.js` pdf.js wrapper
- `web/vendor/` Preact (MIT), htm (Apache-2.0), pdf.js (Apache-2.0), marked (MIT), KaTeX (MIT); licenses alongside.

## License

MIT
