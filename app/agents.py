import re
from pathlib import Path

import yaml

USER_AGENTS_DIR = Path.home() / ".claude" / "agents"
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,60}$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_md(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {"frontmatter": {}, "body": text, "size": len(text)}
    fm_text, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}
    return {"frontmatter": fm, "body": body, "size": len(text)}


def _scan_dir(dir_path: Path, scope: str) -> list[dict]:
    out: list[dict] = []
    if not dir_path.is_dir():
        return out
    for f in sorted(dir_path.glob("*.md")):
        parsed = _parse_md(f)
        fm = parsed.get("frontmatter") or {}
        desc = (fm.get("description") or "").strip()
        # description suele incluir Examples\n\n- User: ... — corta antes
        if "\n\nExamples:" in desc:
            desc = desc.split("\n\nExamples:")[0].strip()
        elif "Examples:" in desc:
            desc = desc.split("Examples:")[0].strip()
        out.append({
            "name": fm.get("name") or f.stem,
            "scope": scope,
            "description": desc[:300],
            "model": fm.get("model") or "",
            "color": fm.get("color") or "",
            "memory": fm.get("memory") or "",
            "file": str(f),
            "body_size": len(parsed.get("body", "")),
        })
    return out


def list_agents(project_paths: list[Path] | None = None) -> list[dict]:
    out = _scan_dir(USER_AGENTS_DIR, "user")
    for p in project_paths or []:
        agents_dir = p / ".claude" / "agents"
        out.extend(_scan_dir(agents_dir, f"project:{p.name}"))
    return out


def get_agent(scope: str, name: str) -> dict | None:
    if not NAME_RE.match(name):
        return None
    if scope == "user":
        path = USER_AGENTS_DIR / f"{name}.md"
    elif scope.startswith("project:"):
        # Caller resolves project path; here we only handle user scope for safety.
        return None
    else:
        return None
    if not path.is_file():
        return None
    parsed = _parse_md(path)
    return {
        "name": name,
        "scope": scope,
        "file": str(path),
        "frontmatter": parsed.get("frontmatter") or {},
        "body": parsed.get("body", ""),
    }


def get_agent_at(file_path: Path) -> dict | None:
    base_user = USER_AGENTS_DIR.resolve()
    try:
        resolved = file_path.resolve()
    except OSError:
        return None
    # Only allow files under user agents dir or under any */.claude/agents/
    allowed = False
    if resolved.is_relative_to(base_user):
        allowed = True
    elif resolved.parent.name == "agents" and resolved.parent.parent.name == ".claude":
        allowed = True
    if not allowed or not resolved.is_file() or resolved.suffix != ".md":
        return None
    parsed = _parse_md(resolved)
    return {
        "file": str(resolved),
        "frontmatter": parsed.get("frontmatter") or {},
        "body": parsed.get("body", ""),
    }
