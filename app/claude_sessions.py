import json
import os
import re
import shutil
import time
from pathlib import Path


def _default_sessions_dir() -> Path:
    """Claude Code stores session JSONLs under ~/.claude/projects/<slug>, where
    <slug> is the cwd-at-launch with `/` replaced by `-` (and a leading dash).
    For an interactive `claude` launched from $HOME, this is e.g. `-Users-alice`.
    Override with CCHUB_SESSIONS_DIR if your workflow differs.
    """
    override = os.environ.get("CCHUB_SESSIONS_DIR")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    slug = "-" + str(home).strip("/").replace("/", "-")
    return home / ".claude" / "projects" / slug


SESSIONS_DIR = _default_sessions_dir()
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _safe_session_path(session_id: str) -> Path | None:
    if not UUID_RE.match(session_id):
        return None
    base = SESSIONS_DIR.resolve()
    candidate = (SESSIONS_DIR / f"{session_id}.jsonl").resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def _strip_channel_prefix(text: str) -> str:
    # tel-code: <channel source="plugin:telegram" ...>actual text</channel>
    m = re.search(r">([^<]+)", text)
    if m and text.lstrip().startswith("<"):
        return m.group(1).strip()
    return text


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text", "") or ""
            if isinstance(part, str):
                return part
    return ""


def _extract_metadata(jsonl_path: Path) -> dict:
    stat = jsonl_path.stat()
    session_id = jsonl_path.stem
    tag = "interactive"
    title = ""
    last_activity = stat.st_mtime
    turn_count = 0
    custom_title = None

    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")
                if etype == "custom-title":
                    ct = ev.get("customTitle")
                    if ct:
                        custom_title = ct
                        if ct == "tel-code":
                            tag = "tel-code"
                elif etype == "user":
                    turn_count += 1
                    if not title:
                        msg = ev.get("message", {})
                        text = _extract_text(msg.get("content", ""))
                        text = _strip_channel_prefix(text)
                        text = text.strip().replace("\n", " ")
                        if text:
                            title = text[:80]
                ts = ev.get("timestamp")
                if ts:
                    try:
                        # ISO 8601 → epoch
                        from datetime import datetime
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        epoch = dt.timestamp()
                        if epoch > last_activity:
                            last_activity = epoch
                    except (ValueError, AttributeError):
                        pass
    except OSError:
        pass

    if custom_title and custom_title != "tel-code":
        title = custom_title[:80]
    elif tag == "tel-code" and not title:
        title = "(tel-code)"
    elif not title:
        title = f"({session_id[:8]})"

    dir_path = SESSIONS_DIR / session_id
    return {
        "id": session_id,
        "title": title,
        "tag": tag,
        "last_activity": last_activity,
        "turn_count": turn_count,
        "size_bytes": stat.st_size,
        "has_dir": dir_path.is_dir(),
    }


def list_sessions() -> list[dict]:
    if not SESSIONS_DIR.is_dir():
        return []
    out: list[dict] = []
    for jsonl in SESSIONS_DIR.glob("*.jsonl"):
        try:
            out.append(_extract_metadata(jsonl))
        except OSError:
            continue
    out.sort(key=lambda x: x["last_activity"], reverse=True)
    return out


def delete_session(session_id: str) -> tuple[bool, str]:
    jsonl = _safe_session_path(session_id)
    if jsonl is None:
        return False, "invalid session id"
    if not jsonl.exists():
        return False, "session not found"
    try:
        jsonl.unlink()
    except OSError as e:
        return False, f"failed to delete jsonl: {e}"
    dir_path = SESSIONS_DIR / session_id
    if dir_path.is_dir():
        try:
            base = SESSIONS_DIR.resolve()
            resolved = dir_path.resolve()
            resolved.relative_to(base)
            shutil.rmtree(resolved)
        except (OSError, ValueError):
            pass
    return True, session_id


def bulk_delete(tag: str | None = None, older_than_days: int | None = None) -> dict:
    deleted: list[str] = []
    failed: list[dict] = []
    cutoff = None
    if older_than_days is not None and older_than_days >= 0:
        cutoff = time.time() - (older_than_days * 86400)
    for s in list_sessions():
        if tag and s["tag"] != tag:
            continue
        if cutoff is not None and s["last_activity"] >= cutoff:
            continue
        ok, msg = delete_session(s["id"])
        if ok:
            deleted.append(s["id"])
        else:
            failed.append({"id": s["id"], "error": msg})
    return {"deleted": deleted, "failed": failed, "deleted_count": len(deleted)}
