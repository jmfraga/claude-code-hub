import os
import re
import shutil
import subprocess
from pathlib import Path

TMUX = os.environ.get("CCHUB_TMUX_BIN") or shutil.which("tmux") or "/usr/bin/tmux"
SAFE_NAME = re.compile(r"^[a-zA-Z0-9-]{1,40}$")


def list_sessions() -> list[dict]:
    p = subprocess.run(
        [TMUX, "list-sessions", "-F", "#{session_name}|#{session_attached}|#{session_created}"],
        capture_output=True, text=True, timeout=5,
    )
    if p.returncode != 0:
        return []
    sessions = []
    for line in p.stdout.splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        sessions.append({
            "name": parts[0],
            "attached": parts[1] == "1",
            "created": int(parts[2]) if parts[2].isdigit() else 0,
        })
    return sessions


def new_session(name: str, cwd: Path, command: list[str] | None = None) -> tuple[bool, str]:
    if not SAFE_NAME.match(name):
        return False, "invalid session name"
    if not cwd.is_dir():
        return False, "invalid cwd"
    args = [TMUX, "new-session", "-d", "-s", name, "-c", str(cwd)]
    if command:
        args.extend(command)
    p = subprocess.run(
        args,
        capture_output=True, text=True, timeout=5,
    )
    if p.returncode != 0:
        err = p.stderr.strip() or "tmux failed"
        return False, err
    return True, name


def session_exists(name: str) -> bool:
    p = subprocess.run([TMUX, "has-session", "-t", name], capture_output=True, timeout=3)
    return p.returncode == 0


def show_buffer() -> str:
    """Return tmux's most-recent paste buffer (what was last selected/copied)."""
    p = subprocess.run([TMUX, "show-buffer"], capture_output=True, text=True, timeout=3)
    if p.returncode != 0:
        return ""
    return p.stdout
