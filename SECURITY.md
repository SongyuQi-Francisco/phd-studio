# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| `main` | yes |
| 0.1.x | yes |
| older | no |

Fixes land on `main` and go into the next release.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through GitHub: open the repository's **Security** tab and click **Report a vulnerability**, or go directly to
<https://github.com/SongyuQi-Francisco/phd-studio/security/advisories/new>.

Include what you found, the steps or a proof of concept to reproduce it, and the version or commit you tested. Leave out anything personal from your own vault or logs. Reports are handled on a best-effort basis; please keep the details private until a fix is released.

## Security model

PhD Studio is a single-user app that runs on your own machine. Knowing its assumptions helps decide whether something is a vulnerability.

- **Local only, no authentication.** `./start.sh` and the desktop window (`app/desktop.py`) start the server on `127.0.0.1:8770` (the desktop window uses `STUDIO_PORT` if set). There are no accounts, logins or API tokens: anything that can reach that address can use the API. The design relies on the server not being reachable from other machines. `start.sh` passes extra arguments on to uvicorn, so do not add `--host 0.0.0.0` or put the port behind a proxy.
- **Secrets stay in files you control.** API keys and the Claude token are read from `KEY=VALUE` files in the secrets folder (`~/.config/secrets/*.env` by default, `paths.secrets` in `studio.toml`), or from environment variables, which take precedence. The app reads these files but does not write them. Only `CLAUDE_CODE_OAUTH_TOKEN` from `claude.env` is passed to the Claude Agent SDK.
- **Claude's file edits need your approval.** In the co-reader and assistant chats, read-only tools (`Read`, `Glob`, `Grep`, `WebFetch`, `WebSearch`) and the workbench's own tools (reading list, ideas, milestones and similar) run without asking. `Edit`, `Write` and `MultiEdit` are allowed only for paths inside the vault, and each one is shown in the UI as a diff and applied only after you approve it; other tools are denied (`app/claude_chat.py`). Other models (Codex, Gemini, Zhipu GLM, DeepSeek) run read-only.
- **The app's own vault writes are limited.** Besides approved Claude edits, the app writes its structured data (the reading list, plan, ideas and similar YAML files), regenerates the `Reading List.md` summary note, and, after you confirm the exact line, adds an excerpt line or a `zotero:` link to a paper note.
- **Paths stay inside the vault.** Note paths from the API and from `[[links]]` are resolved and refused if they end up outside the vault, including through `..` segments, absolute paths or symlinks (`app/notes.py`).
- **Debug hook is off by default.** Starting the desktop window with `STUDIO_DEBUG=1` enables `/api/debug/eval`, which runs arbitrary JavaScript inside the app window for test scripts. Without it, that endpoint returns 404. Never enable it outside local testing.

Known limitation: read-only tools such as `WebFetch` run without a prompt, so instructions hidden in a PDF or web page (prompt injection) could steer a model into fetching URLs. Claude's vault edits still require your approval.

## Scope

In scope:

- Ways for another machine, or a web page open in your browser, to reach or drive the local server (for example cross-site requests or DNS rebinding).
- Reading or writing files outside the vault and the app's own folders through the API, or model tool calls that write outside the vault.
- Claude edits to the vault applied without your approval, or write access for models that are meant to be read-only.
- Secrets leaking into logs, chat history, command lines or requests to services other than the one the key is for.

Out of scope:

- Setups that remove the assumptions above: binding the server to a non-loopback address, exposing it through a proxy or tunnel, or running with `STUDIO_DEBUG=1`.
- Attacks that already require running code as your user account on the machine.
- Vulnerabilities in third-party tools and services (Claude Code, Codex CLI, Gemini CLI, pdf2zh-next, Zotero, Obsidian, model APIs). Please report those upstream; how PhD Studio uses them is in scope.
