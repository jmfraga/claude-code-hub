from pathlib import Path
from . import git_utils


def scan(projects_root: Path, extra: list[str]) -> list[dict]:
    results: list[dict] = []
    seen: set[Path] = set()

    if projects_root.is_dir():
        for child in sorted(projects_root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                results.append(_describe(child))
                seen.add(child.resolve())

    for raw in extra:
        p = Path(raw).expanduser()
        if not p.is_dir():
            continue
        if p.resolve() in seen:
            continue
        results.append(_describe(p))
        seen.add(p.resolve())

    return results


def _describe(path: Path) -> dict:
    return {
        "name": path.name,
        "path": str(path),
        "git": git_utils.status(path),
        "last_commit": git_utils.last_commit(path),
    }


def find(projects: list[dict], name: str) -> dict | None:
    for p in projects:
        if p["name"] == name:
            return p
    return None


def file_tree(path: Path, max_entries: int = 500) -> list[dict]:
    out: list[dict] = []
    skip = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next"}
    for entry in sorted(path.iterdir()):
        if entry.name in skip or entry.name.startswith("."):
            continue
        out.append({"name": entry.name, "type": "dir" if entry.is_dir() else "file"})
        if len(out) >= max_entries:
            break
    return out
