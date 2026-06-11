import re
from pathlib import Path

from . import claude_sessions, projects, tmux_utils

PENDING_FILES = ("TODO.md", "ROADMAP.md", "todo.md", "roadmap.md")
PENDING_RE = re.compile(r"^\s*[-*]\s*\[ \]\s*(.+?)\s*$", re.MULTILINE)
MAX_PENDING = 8
MAX_SESSIONS = 6

FABLE_FILE = "Propuestas-Fable.md"
FABLE_HEADING_RE = re.compile(r"^###\s+F-\d+\.\s+.*?\[(ALTA|MEDIA|BAJA)\]", re.MULTILINE)
FABLE_ESTADO_RE = re.compile(r"^-\s+\*\*Estado\*\*:\s*\[([ x])\]", re.MULTILINE)
FABLE_NO_FINDINGS_RE = re.compile(r"##\s+Hallazgos\s*\n+Sin hallazgos\.", re.IGNORECASE)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def extract_pending(project_path: Path) -> list[dict]:
    """Find unchecked checkbox items in TODO.md / ROADMAP.md at project root."""
    out: list[dict] = []
    for fname in PENDING_FILES:
        f = project_path / fname
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in PENDING_RE.finditer(text):
            item = m.group(1).strip()
            # Strip markdown link syntax for readability
            item = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", item)
            if len(item) > 140:
                item = item[:137] + "…"
            out.append({"text": item, "source": fname})
            if len(out) >= MAX_PENDING:
                return out
    return out


def match_sessions(project_name: str, project_path: Path, sessions: list[dict]) -> list[dict]:
    """Return sessions whose title or cwd-derived identifier mentions this project."""
    keys = {project_name.lower(), _slug(project_name)}
    # Also recognize sessions launched from this directory (their JSONL slug encodes cwd).
    cwd_slug = "-" + str(project_path.resolve()).strip("/").replace("/", "-")
    keys.add(cwd_slug.lower())
    matched: list[dict] = []
    for s in sessions:
        hay = (s.get("title") or "").lower()
        if any(k and k in hay for k in keys):
            matched.append(s)
            if len(matched) >= MAX_SESSIONS:
                break
    return matched


def match_tmux(project_name: str, tmux_sessions: list[dict]) -> list[dict]:
    """Find tmux sessions whose name contains the project slug."""
    slug = _slug(project_name)
    return [t for t in tmux_sessions if slug and slug in t["name"].lower()]


def parse_fable(project_path: Path) -> dict | None:
    """Parse Propuestas-Fable.md at project root.

    Returns:
        None if no file present.
        {"no_findings": True} if the audit found nothing.
        Otherwise {"total", "done", "alta_pending"}.
    """
    f = project_path / FABLE_FILE
    if not f.is_file():
        return None
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if FABLE_NO_FINDINGS_RE.search(text):
        return {"no_findings": True, "total": 0, "done": 0, "alta_pending": 0}
    # Walk findings in order; each ### heading + first Estado line below it form a pair.
    findings: list[tuple[str, str]] = []  # (severity, state_char)
    pos = 0
    headings = list(FABLE_HEADING_RE.finditer(text))
    for i, m in enumerate(headings):
        sev = m.group(1)
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        sub = text[m.end():block_end]
        est = FABLE_ESTADO_RE.search(sub)
        state = est.group(1) if est else " "
        findings.append((sev, state))
    total = len(findings)
    done = sum(1 for _, s in findings if s == "x")
    alta_pending = sum(1 for sev, s in findings if sev == "ALTA" and s != "x")
    return {"no_findings": False, "total": total, "done": done, "alta_pending": alta_pending}


def build(projects_list: list[dict]) -> list[dict]:
    sessions = claude_sessions.list_sessions()
    tmux_list = tmux_utils.list_sessions()
    out: list[dict] = []
    for p in projects_list:
        ppath = Path(p["path"])
        out.append({
            **p,
            "pending": extract_pending(ppath),
            "recent_sessions": match_sessions(p["name"], ppath, sessions),
            "active_tmux": match_tmux(p["name"], tmux_list),
            "fable": parse_fable(ppath),
        })
    return out
