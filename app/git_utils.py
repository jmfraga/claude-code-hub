import subprocess
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=10)
    return p.returncode, p.stdout, p.stderr


def is_repo(path: Path) -> bool:
    return (path / ".git").exists()


def status(path: Path) -> dict:
    if not is_repo(path):
        return {"is_repo": False}
    rc, out, _ = _run(["git", "status", "--porcelain", "--branch"], path)
    if rc != 0:
        return {"is_repo": True, "error": "git status failed"}
    lines = out.splitlines()
    branch = "?"
    ahead = behind = 0
    if lines and lines[0].startswith("##"):
        head = lines[0][3:]
        branch = head.split("...")[0].strip()
        if "ahead " in head:
            try:
                ahead = int(head.split("ahead ")[1].split(",")[0].split("]")[0])
            except (ValueError, IndexError):
                pass
        if "behind " in head:
            try:
                behind = int(head.split("behind ")[1].split(",")[0].split("]")[0])
            except (ValueError, IndexError):
                pass
    dirty = sum(1 for l in lines[1:] if l.strip())
    return {
        "is_repo": True,
        "branch": branch,
        "dirty": dirty,
        "ahead": ahead,
        "behind": behind,
        "clean": dirty == 0 and ahead == 0 and behind == 0,
    }


def last_commit(path: Path) -> dict | None:
    if not is_repo(path):
        return None
    rc, out, _ = _run(["git", "log", "-1", "--format=%h|%ar|%s"], path)
    if rc != 0 or not out.strip():
        return None
    parts = out.strip().split("|", 2)
    if len(parts) != 3:
        return None
    return {"hash": parts[0], "ago": parts[1], "subject": parts[2]}


def recent_commits(path: Path, n: int = 5) -> list[dict]:
    if not is_repo(path):
        return []
    rc, out, _ = _run(["git", "log", f"-{n}", "--format=%h|%ar|%s"], path)
    if rc != 0:
        return []
    commits: list[dict] = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({"hash": parts[0], "ago": parts[1], "subject": parts[2]})
    return commits
