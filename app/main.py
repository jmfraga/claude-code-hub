import asyncio
import os
import shutil
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import markdown as md_lib

from . import agents, claude_sessions, jobs, projects, tmux_utils

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "settings.yaml"


def _claude_bin() -> str:
    """Locate the `claude` CLI. Override with CCHUB_CLAUDE_BIN env var."""
    override = os.environ.get("CCHUB_CLAUDE_BIN")
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    # Common install location for Claude Code on macOS/Linux
    candidate = Path.home() / ".local" / "bin" / "claude"
    return str(candidate)


def _shell() -> str:
    return os.environ.get("CCHUB_SHELL") or os.environ.get("SHELL") or "/bin/sh"


app = FastAPI(title="Claude Code Hub")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def load_settings() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def reports_dirs(settings: dict) -> list[Path]:
    out: list[Path] = []
    p = Path(settings.get("reports_path", "")).expanduser()
    if p.is_dir():
        out.append(p)
    for extra in settings.get("extra_reports_paths", []) or []:
        ep = Path(extra).expanduser()
        if ep.is_dir():
            out.append(ep)
    return out


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/api/projects")
async def api_projects():
    s = load_settings()
    return projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])


@app.get("/api/projects/{name}/files")
async def api_project_files(name: str):
    s = load_settings()
    plist = projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])
    proj = projects.find(plist, name)
    if not proj:
        raise HTTPException(404, "project not found")
    return projects.file_tree(Path(proj["path"]))


@app.get("/api/reports")
async def api_reports():
    s = load_settings()
    out = []
    for d in reports_dirs(s):
        for f in d.glob("*.md"):
            stat = f.stat()
            out.append({
                "filename": f.name,
                "path": str(f),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "dir": str(d),
            })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


@app.get("/api/reports/{filename}", response_class=HTMLResponse)
async def api_report_render(filename: str):
    if "/" in filename or filename.startswith("."):
        raise HTTPException(400, "invalid filename")
    s = load_settings()
    for d in reports_dirs(s):
        candidate = d / filename
        if candidate.is_file() and candidate.suffix == ".md":
            text = candidate.read_text(encoding="utf-8", errors="replace")
            html = md_lib.markdown(text, extensions=["fenced_code", "tables", "toc"])
            return HTMLResponse(html)
    raise HTTPException(404, "report not found")


@app.get("/api/sessions")
async def api_sessions():
    return tmux_utils.list_sessions()


@app.post("/api/sessions/new")
async def api_session_new(payload: dict):
    name = payload.get("name", "")
    project_name = payload.get("project", "")
    s = load_settings()
    plist = projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])
    proj = projects.find(plist, project_name)
    if not proj:
        raise HTTPException(404, "project not found")
    ok, msg = tmux_utils.new_session(name, Path(proj["path"]))
    if not ok:
        raise HTTPException(400, msg)
    return {"name": msg}


@app.get("/api/agents")
async def api_agents():
    s = load_settings()
    plist = projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])
    project_paths = [Path(p["path"]) for p in plist]
    return agents.list_agents(project_paths)


@app.get("/api/agents/file")
async def api_agent_file(path: str):
    detail = agents.get_agent_at(Path(path))
    if not detail:
        raise HTTPException(404, "agent not found")
    return detail


@app.get("/api/claude-sessions")
async def api_claude_sessions():
    return claude_sessions.list_sessions()


@app.post("/api/claude-sessions/new")
async def api_claude_session_new(payload: dict):
    raw_title = (payload.get("title") or "").strip()
    if not raw_title or len(raw_title) > 50:
        raise HTTPException(400, "title required (1-50 chars)")
    # Sanitize for tmux session name and /title argument
    import re as _re
    slug = _re.sub(r"[^a-zA-Z0-9-]+", "-", raw_title).strip("-")[:30].lower()
    if not slug:
        raise HTTPException(400, "title yields empty slug")
    safe_title = _re.sub(r"['\"\\\\`$]", "", raw_title)  # strip shell-dangerous chars
    name = f"cl-{slug}"
    # Disambiguate if name collides
    base = name
    i = 2
    while tmux_utils.session_exists(name):
        name = f"{base}-{i}"
        i += 1
        if i > 9:
            raise HTTPException(409, "too many sessions with similar name")
    claude_bin = _claude_bin()
    # Schedule /rename <name> via send-keys after claude is up; then run claude.
    # The pane stays alive on exit so the user reads any error.
    wrapped = (
        f"( sleep 3 && {tmux_utils.TMUX} send-keys -t {name} '/rename {safe_title}' Enter ) & "
        f"{claude_bin}; "
        "ec=$?; echo; echo \"[claude exited with $ec — press Enter to close]\"; read"
    )
    ok, msg = tmux_utils.new_session(
        name,
        Path.home(),
        command=[_shell(), "-l", "-c", wrapped],
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"name": msg, "title": safe_title}


@app.post("/api/claude-sessions/{session_id}/resume")
async def api_claude_session_resume(session_id: str):
    if not claude_sessions.UUID_RE.match(session_id):
        raise HTTPException(400, "invalid session id")
    jsonl = claude_sessions.SESSIONS_DIR / f"{session_id}.jsonl"
    if not jsonl.is_file():
        raise HTTPException(404, "session not found")
    name = f"claude-{session_id[:8]}"
    if tmux_utils.session_exists(name):
        return {"name": name, "reused": True}
    # Wrap in zsh so the pane stays alive if claude exits (e.g. session already
    # locked by another instance) — user can read the error and Enter to close.
    claude_bin = _claude_bin()
    wrapped = (
        f"{claude_bin} --resume {session_id}; "
        "ec=$?; echo; echo \"[claude --resume exited with $ec — press Enter to close]\"; read"
    )
    ok, msg = tmux_utils.new_session(
        name,
        Path.home(),
        command=[_shell(), "-l", "-c", wrapped],
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"name": msg, "reused": False}


@app.post("/api/claude-sessions/{session_id}/delete")
async def api_claude_session_delete(session_id: str):
    ok, msg = claude_sessions.delete_session(session_id)
    if not ok:
        code = 400 if msg == "invalid session id" else 404
        raise HTTPException(code, msg)
    return {"deleted": msg}


@app.post("/api/claude-sessions/bulk-delete")
async def api_claude_session_bulk_delete(payload: dict):
    tag = payload.get("tag")
    if tag is not None and not isinstance(tag, str):
        raise HTTPException(400, "invalid tag")
    older = payload.get("older_than_days")
    if older is not None:
        try:
            older = int(older)
            if older < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(400, "invalid older_than_days")
    return claude_sessions.bulk_delete(tag=tag, older_than_days=older)


@app.post("/api/projects/{name}/run/{cmd_id}")
async def api_run(name: str, cmd_id: str):
    s = load_settings()
    plist = projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])
    proj = projects.find(plist, name)
    if not proj:
        raise HTTPException(404, "project not found")
    cmd_def = next((c for c in s.get("allowed_commands", []) if c["id"] == cmd_id), None)
    if not cmd_def:
        raise HTTPException(403, "command not allowed")
    job = jobs.submit(name, cmd_id, list(cmd_def["cmd"]), Path(proj["path"]))
    return {"job_id": job.id, "project": name, "cmd_id": cmd_id}


@app.get("/api/projects/{name}/output/{job_id}")
async def api_output(name: str, job_id: str):
    job = jobs.get(job_id)
    if not job or job.project != name:
        raise HTTPException(404, "job not found")

    async def gen():
        for line in list(job.lines):
            yield f"data: {line}\n\n"
        if job.done:
            yield f"event: end\ndata: exit={job.exit_code}\n\n"
            return
        while True:
            try:
                line = await asyncio.wait_for(job.queue.get(), timeout=30)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if line is None:
                yield f"event: end\ndata: exit={job.exit_code}\n\n"
                return
            yield f"data: {line}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/jobs")
async def api_jobs():
    return jobs.list_recent()
