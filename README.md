# Claude Code Hub

A small self-hosted web panel to manage your local **Claude Code** sessions, agents, and projects from any device on your LAN / Tailscale network — no manual SSH.

Built for the case where you have a beefy "brain" machine (mine is a Mac Mini M4) running long-lived Claude Code sessions and bots, and you want a tidy UI to:

- See a **Dashboard** of the whole ecosystem at a glance: each project shows its git state, pending checkbox items pulled from `TODO.md` / `ROADMAP.md`, recent Claude sessions that mention the project, and active tmux. Click the top stats (dirty / ahead / behind / pendientes) to filter. Optional **🧠 resumen** button per project calls a local LLM (MLX / Ollama / any OpenAI-compatible endpoint) for a natural-language overview, health flag (green / yellow / red), suggested next action, and structured `entities` + `relations`.
- Explore the **Graph** view: an interactive force-directed graph (Cytoscape.js) of every project, machine, service, agent and device the LLM extracted from your summaries — colored by type, with green/yellow/red health borders on project nodes. Click a node to see its neighbors; jump between connected nodes with one click. Layout switcher (force / concentric / tree / grid).
- Browse your **Projects**: git status, last commit, run a small whitelist of commands (`git status`, `git pull`, `npm test`, …) and stream output live via SSE. Each project has a file browser drawer with breadcrumb-navigable tree and inline file viewer (1 MB cap, binary detection, path-traversal guard).
- Manage **Claude Code sessions**: list every session JSONL with metadata (title, last activity, turn count, size, tag), **resume** any session in a tmux pane, **delete** stale ones, **bulk-delete** by age (great for cleaning up bot-spawned sessions). Create a **new named session** from the UI — the hub starts `claude` and auto-runs `/rename <title>` so it shows up with a friendly name.
- Browse the **Agents** you have registered (`~/.claude/agents/*.md` and per-project ones): name, description, model, color, full system prompt. An example agent — [`agents/fable-auditor.md`](agents/fable-auditor.md) — ships in this repo: a read-only auditor that writes `Propuestas-Fable.md` per project. The Dashboard parses that file and shows a **Fable pill** on each card (`done/total · N ALTA` with red/amber/green coloring) so you can see at a glance which projects have unresolved audit findings.
- Drop into a **Terminal** (ttyd over a shared tmux session) — with a `📋 copy buffer` button to copy whatever you last selected with the mouse into the browser's clipboard.

Stack: FastAPI + Jinja + HTMX + Alpine.js + Tailwind CDN + ttyd + tmux. No build step, no DB.

> ⚠️ Heads up: there is **no auth**. The hub is intended to bind to a private network (Tailscale, LAN). Don't expose it to the public internet.

## Screenshot

The UI is intentionally plain — six tabs (Dashboard / Graph / Projects / Agents / Sessions / Terminal), small components, no JS framework dance.

## How sessions are tracked

Claude Code stores each session as a JSONL file under `~/.claude/projects/<slug>/`, where `<slug>` is the cwd-at-launch with `/` replaced by `-`. The hub reads the JSONLs to extract:

- **title**: the `customTitle` event (set via `/rename`) if present, else the first user message (truncated, with `<channel source=…>` prefixes from Telegram-style wrappers stripped).
- **tag**: an arbitrary marker. The hub recognizes one out of the box — sessions whose `customTitle` equals `tel-code` get a red chip. This was the original use case (bot-spawned sessions named `tel-code` for triage). You can adapt the detector in `app/claude_sessions.py` to your own tags.
- **last activity**: max `timestamp` field across all events (fallback: file mtime).
- **turn count**: number of `user` events.

By default the hub looks at `~/.claude/projects/-Users-<you>` (computed from `$HOME`). Override with `CCHUB_SESSIONS_DIR=/path/to/jsonl/folder`.

## Resume / new session — how it works

When you click **Resume** on a session or **Create** a new one:

1. The hub creates a detached tmux session named `claude-<id8>` (resume) or `cl-<slug>` (new).
2. Inside it runs `claude --resume <id>` (or just `claude` for a new session) wrapped in a shell that keeps the pane alive on exit, so any error from Claude is readable instead of vanishing with the tmux window.
3. For new sessions, after a short delay the hub injects `/rename <title>` via `tmux send-keys` so the session has a friendly title from the start.
4. The frontend points the ttyd iframe at `?arg=attach&arg=-t&arg=<name>` so you land in the right pane.

There's a `← shared` button on the Terminal tab as an escape hatch: it always reattaches to the default `shared` tmux session in case you got stuck on a dead one.

## Setup

### 1. Prereqs (macOS shown; Linux is similar)

```bash
brew install python tmux ttyd
```

`claude` (the CLI) must be installed and in `$PATH`. The hub auto-detects it via `which claude`; override with `CCHUB_CLAUDE_BIN=/abs/path/to/claude`.

### 2. Clone & install

```bash
git clone https://github.com/jmfraga/claude-code-hub.git
cd claude-code-hub
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp config/settings.example.yaml config/settings.yaml
$EDITOR config/settings.yaml      # set projects_root (and optionally ssh_target)
mkdir -p logs
```

### 3. Run (foreground, for testing)

```bash
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8081
```

In another terminal start ttyd. **The `-a` flag is required** — it lets the web UI pass tmux args via URL query parameters:

```bash
ttyd -p 8082 -W -a /opt/homebrew/bin/tmux
```

Open [http://localhost:8081](http://localhost:8081). The terminal tab embeds ttyd from `:8082`.

### 4. Persistent service (macOS, launchd)

Templates live in `launchd/`. Copy them to `~/Library/LaunchAgents/`, edit the placeholder paths, then:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.you.cchub.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.you.cchub-ttyd.plist
```

To restart after editing:

```bash
launchctl kickstart -k gui/$(id -u)/com.you.cchub
```

### 5. Linux (systemd) — sketch

Adapt the launchd plists to two `~/.config/systemd/user/*.service` units running uvicorn and ttyd respectively, then `systemctl --user enable --now`.

## Configuration

| What | Where |
|---|---|
| Projects root, reports path, allowed shell commands | `config/settings.yaml` |
| Where to find Claude session JSONLs | `CCHUB_SESSIONS_DIR` env var (defaults to `~/.claude/projects/-<home-slug>`) |
| `claude` binary | `CCHUB_CLAUDE_BIN` env var (else `which claude`) |
| `tmux` binary | `CCHUB_TMUX_BIN` env var (else `which tmux`) |
| Login shell for resume/new wrappers | `CCHUB_SHELL` env var (else `$SHELL`) |
| ttyd port | the `-p` arg in the ttyd plist |
| Hub port | the `--port` arg in the cchub plist |

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/projects` | List projects with git state |
| GET | `/api/projects/{name}/files` | File tree (capped) |
| POST | `/api/projects/{name}/run/{cmd_id}` | Run a whitelisted command |
| GET | `/api/projects/{name}/output/{job_id}` | SSE stream of job output |
| GET | `/api/jobs` | Recent jobs |
| GET | `/api/sessions` | List tmux sessions |
| POST | `/api/sessions/new` | Create a tmux session inside a project |
| GET | `/api/claude-sessions` | List Claude Code sessions with metadata |
| POST | `/api/claude-sessions/new` | New named session (`{title}`) — auto `/rename` |
| POST | `/api/claude-sessions/{id}/resume` | Resume a session in tmux |
| POST | `/api/claude-sessions/{id}/delete` | Delete `.jsonl` (and dir if exists) |
| POST | `/api/claude-sessions/bulk-delete` | `{tag?, older_than_days?}` |
| GET | `/api/dashboard` | Enriched project list (git + pending + sessions + tmux) |
| GET / POST | `/api/dashboard/summary/{name}` | Get cached / regenerate LLM summary for a project |
| GET | `/api/dashboard/graph` | Aggregate nodes & edges from all cached summaries (for graph view) |
| GET | `/api/agents` | List agents (user-level + per-project) |
| GET | `/api/agents/file?path=…` | Read one agent's frontmatter + body |

## Security notes

- **No auth.** Bind to private network only.
- All filesystem operations on session IDs validate against a UUID regex and check the resolved path stays under the sessions directory (no path traversal).
- The shell command whitelist (`allowed_commands` in `settings.yaml`) is enforced server-side: the API rejects any command id not in the list.
- Project / report paths are looked up by name in the scanned list — you can't `cd` outside of `projects_root`.
- ttyd is launched with `-W` (writable). Anyone who can reach `:8082` can use the terminal.

## Why not just SSH?

You can. This panel just makes the things I do dozens of times a day faster: scan projects with one glance, kill stale tel-code sessions in bulk, re-attach to a long-running Claude session from my phone over Tailscale, drop into a tmux without remembering the session name.

## Contributing

Issues and PRs welcome. Keep it small.

## License

MIT — see [LICENSE](LICENSE).
