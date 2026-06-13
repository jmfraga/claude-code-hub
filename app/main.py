import asyncio
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from . import agents, claude_sessions, dashboard, fable, git_utils, jobs, llm, projects, tmux_utils

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "settings.yaml"

app = FastAPI(title="Claude Code Hub")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def _no_cache_api(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


def load_settings() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    resp = templates.TemplateResponse(request, "dashboard.html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.get("/api/tmux/buffer")
async def api_tmux_buffer():
    return {"content": tmux_utils.show_buffer()}


@app.get("/api/config")
async def api_config():
    """Safe-to-expose config bits for the frontend."""
    s = load_settings()
    return {"ssh_target": s.get("ssh_target") or ""}


@app.get("/api/projects")
async def api_projects():
    s = load_settings()
    return projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])


@app.get("/api/dashboard")
async def api_dashboard():
    s = load_settings()
    plist = projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])
    return dashboard.build(plist)


CACHE_DIR = Path.home() / ".cache" / "cchub" / "summaries"


def _cache_path(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_.")[:80] or "p"
    return CACHE_DIR / f"{safe}.json"


@app.get("/api/dashboard/summary/{name}")
async def api_get_summary(name: str):
    path = _cache_path(name)
    if not path.is_file():
        return {"cached": False}
    try:
        import json as _json
        return {"cached": True, **_json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {"cached": False}


@app.post("/api/dashboard/summary/{name}")
async def api_make_summary(name: str):
    s = load_settings()
    plist = projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])
    proj = projects.find(plist, name)
    if not proj:
        raise HTTPException(404, "project not found")
    enriched = dashboard.build([proj])[0]
    commits = git_utils.recent_commits(Path(proj["path"]), n=5)
    result = llm.summarize(enriched, commits, s.get("llm") or {})
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "llm failed")
    import json as _json
    import time as _time
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**result, "generated_at": _time.time()}
    try:
        _cache_path(name).write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return payload


@app.get("/api/dashboard/graph")
async def api_dashboard_graph():
    """Aggregate cached summaries into a graph payload for V3."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    if not CACHE_DIR.is_dir():
        return {"nodes": [], "edges": []}
    import json as _json
    for f in CACHE_DIR.glob("*.json"):
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        project_name = f.stem
        nodes.setdefault(project_name, {"id": project_name, "type": "project", "health": data.get("health", "yellow")})
        for ent in data.get("entities", []) or []:
            ename = (ent.get("name") or "").strip()
            if not ename:
                continue
            etype = ent.get("type") or "related"
            nodes.setdefault(ename, {"id": ename, "type": etype})
        for rel in data.get("relations", []) or []:
            fr = (rel.get("from") or "").strip()
            to = (rel.get("to") or "").strip()
            if not fr or not to:
                continue
            edges.append({"from": fr, "to": to, "type": rel.get("type") or "related_to"})
    # Dedup edges on (from, to, type)
    seen: set[tuple] = set()
    unique_edges: list[dict] = []
    for e in edges:
        key = (e["from"], e["to"], e["type"])
        if key in seen:
            continue
        seen.add(key)
        unique_edges.append(e)
    # Ensure every edge endpoint exists as a node (LLM may name something not previously seen)
    for e in unique_edges:
        for endpoint in (e["from"], e["to"]):
            nodes.setdefault(endpoint, {"id": endpoint, "type": "unknown"})
    return {"nodes": list(nodes.values()), "edges": unique_edges}


@app.get("/api/projects/{name}/files")
async def api_project_files(name: str, subpath: str = ""):
    s = load_settings()
    plist = projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])
    proj = projects.find(plist, name)
    if not proj:
        raise HTTPException(404, "project not found")
    target = projects.safe_subpath(Path(proj["path"]), subpath)
    if target is None or not target.is_dir():
        raise HTTPException(404, "subpath not found")
    return {
        "subpath": subpath.lstrip("/"),
        "entries": projects.list_dir(target),
    }


@app.get("/api/projects/{name}/file")
async def api_project_file(name: str, subpath: str):
    if not subpath:
        raise HTTPException(400, "subpath required")
    s = load_settings()
    plist = projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])
    proj = projects.find(plist, name)
    if not proj:
        raise HTTPException(404, "project not found")
    target = projects.safe_subpath(Path(proj["path"]), subpath)
    if target is None:
        raise HTTPException(400, "invalid subpath")
    detail = projects.read_file(target)
    if detail is None:
        raise HTTPException(404, "file not found")
    return detail


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


@app.get("/api/projects/{name}/fable")
async def api_project_fable(name: str):
    s = load_settings()
    plist = projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])
    proj = projects.find(plist, name)
    if not proj:
        raise HTTPException(404, "project not found")
    detail = fable.read_findings(Path(proj["path"]))
    if detail is None:
        raise HTTPException(404, "Propuestas-Fable.md not present")
    return detail


@app.post("/api/projects/{name}/fable/{f_id}")
async def api_project_fable_set(name: str, f_id: str, payload: dict):
    s = load_settings()
    plist = projects.scan(Path(s["projects_root"]).expanduser(), s.get("extra_projects", []) or [])
    proj = projects.find(plist, name)
    if not proj:
        raise HTTPException(404, "project not found")
    done = bool(payload.get("done"))
    commit = (payload.get("commit") or "").strip()
    ok, msg = fable.set_status(Path(proj["path"]), f_id, done, commit)
    if not ok:
        code = 400 if msg in ("invalid finding id", "invalid commit hash") else 404
        raise HTTPException(code, msg)
    return {"ok": True, "id": f_id, "done": done}


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
    claude_bin = str(Path.home() / ".local" / "bin" / "claude")
    # Schedule /title <name> via send-keys after claude is up; then exec claude.
    # The pane stays alive on exit so the user reads any error.
    wrapped = (
        f"( sleep 3 && {tmux_utils.TMUX} send-keys -t {name} '/rename {safe_title}' Enter ) & "
        f"{claude_bin}; "
        "ec=$?; echo; echo \"[claude exited with $ec — press Enter to close]\"; read"
    )
    ok, msg = tmux_utils.new_session(
        name,
        Path.home(),
        command=["/bin/zsh", "-l", "-c", wrapped],
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
    claude_bin = str(Path.home() / ".local" / "bin" / "claude")
    wrapped = (
        f"{claude_bin} --resume {session_id}; "
        "ec=$?; echo; echo \"[claude --resume exited with $ec — press Enter to close]\"; read"
    )
    ok, msg = tmux_utils.new_session(
        name,
        Path.home(),
        command=["/bin/zsh", "-l", "-c", wrapped],
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
