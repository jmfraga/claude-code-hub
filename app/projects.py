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


SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next"}
MAX_FILE_BYTES = 1_000_000  # 1 MB cap for file viewer
TEXT_HINT_BYTES = 4096


def safe_subpath(project_path: Path, subpath: str | None) -> Path | None:
    """Resolve subpath under project_path; reject if it escapes via .. or symlink."""
    if not subpath:
        return project_path
    # Strip leading slash to keep relative
    sp = subpath.lstrip("/").strip()
    if not sp:
        return project_path
    candidate = (project_path / sp)
    try:
        resolved = candidate.resolve()
        base = project_path.resolve()
        resolved.relative_to(base)
    except (OSError, ValueError):
        return None
    return resolved


def list_dir(path: Path, max_entries: int = 1000) -> list[dict]:
    out: list[dict] = []
    if not path.is_dir():
        return out
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return out
    for entry in entries:
        if entry.name in SKIP_DIRS:
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        out.append({
            "name": entry.name,
            "type": "dir" if entry.is_dir() else "file",
            "size": stat.st_size if entry.is_file() else 0,
            "mtime": stat.st_mtime,
        })
        if len(out) >= max_entries:
            break
    return out


# Backwards-compat shim (other call sites still expect this name).
def file_tree(path: Path, max_entries: int = 500) -> list[dict]:
    return list_dir(path, max_entries)


def _is_probably_text(sample: bytes) -> bool:
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    # Heuristic: count non-printable bytes (excluding common whitespace)
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(sample) > 0.85


def read_file(path: Path) -> dict | None:
    """Read a file under the project. Returns dict with content or `binary=True`."""
    if not path.is_file():
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > MAX_FILE_BYTES:
        return {"name": path.name, "size": size, "truncated": True, "binary": False,
                "content": f"[file too large to display: {size} bytes; max {MAX_FILE_BYTES}]"}
    try:
        with open(path, "rb") as f:
            sample = f.read(TEXT_HINT_BYTES)
    except OSError:
        return None
    if not _is_probably_text(sample):
        return {"name": path.name, "size": size, "truncated": False, "binary": True, "content": ""}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return {"name": path.name, "size": size, "truncated": False, "binary": False, "content": text}
