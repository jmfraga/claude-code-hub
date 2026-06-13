"""Fable audit file reader/writer.

Parses Propuestas-Fable.md at a project root into structured findings and
applies focused edits (toggle Estado line per F-N). Keeps the file as the
single source of truth — the dashboard drawer mutates this file directly.
"""
import re
from datetime import date
from pathlib import Path

FABLE_FILE = "Propuestas-Fable.md"

HEADING_RE = re.compile(
    r"^###\s+(F-\d+)\.\s+(.+?)\s+—\s+\[(ALTA|MEDIA|BAJA)\]\s*$",
    re.MULTILINE,
)
FIELD_RE = re.compile(r"^-\s+\*\*([^*]+)\*\*:\s*(.+?)\s*$", re.MULTILINE)
ESTADO_LINE_RE = re.compile(
    r"^(-\s+\*\*Estado\*\*:\s*)\[([ x])\](.*)$",
    re.MULTILINE,
)


def read_findings(project_path: Path) -> dict | None:
    """Return {"findings": [...], "no_findings": bool} or None if no file."""
    f = project_path / FABLE_FILE
    if not f.is_file():
        return None
    text = f.read_text(encoding="utf-8", errors="replace")
    # "Sin hallazgos." short-circuit
    if re.search(r"##\s+Hallazgos\s*\n+Sin hallazgos\.", text, re.IGNORECASE):
        return {"no_findings": True, "findings": []}
    heads = list(HEADING_RE.finditer(text))
    findings: list[dict] = []
    for i, m in enumerate(heads):
        f_id, title, severity = m.group(1), m.group(2), m.group(3)
        block_end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        block = text[m.end():block_end]
        # Parse field lines (- **Field**: value)
        fields = {k.strip().lower(): v.strip() for k, v in FIELD_RE.findall(block)}
        est = ESTADO_LINE_RE.search(block)
        done = est is not None and est.group(2) == "x"
        estado_note = est.group(3).strip() if est else ""
        findings.append({
            "id": f_id,
            "title": title.strip(),
            "severity": severity,
            "dimension": fields.get("dimensión", "") or fields.get("dimension", ""),
            "evidencia": fields.get("evidencia", ""),
            "propuesta": fields.get("propuesta", ""),
            "esfuerzo": fields.get("esfuerzo", ""),
            "done": done,
            "estado_note": estado_note,
        })
    return {"no_findings": False, "findings": findings}


def set_status(project_path: Path, f_id: str, done: bool, commit: str = "") -> tuple[bool, str]:
    """Toggle the Estado line for F-N. Returns (ok, message_or_new_state)."""
    f = project_path / FABLE_FILE
    if not f.is_file():
        return False, "Propuestas-Fable.md not found"
    if not re.fullmatch(r"F-\d{1,3}", f_id):
        return False, "invalid finding id"
    if commit and not re.fullmatch(r"[a-f0-9]{7,40}", commit):
        return False, "invalid commit hash"
    text = f.read_text(encoding="utf-8", errors="replace")
    heads = list(HEADING_RE.finditer(text))
    target = None
    for i, m in enumerate(heads):
        if m.group(1) == f_id:
            target = (m.end(), heads[i + 1].start() if i + 1 < len(heads) else len(text))
            break
    if target is None:
        return False, f"{f_id} not found in file"
    start, end = target
    block = text[start:end]
    est = ESTADO_LINE_RE.search(block)
    if not est:
        return False, f"Estado line not found inside {f_id}"
    if done:
        today = date.today().isoformat()
        suffix = f" · commit {commit}" if commit else ""
        new_line = f"- **Estado**: [x] implementado {today}{suffix}"
    else:
        new_line = "- **Estado**: [ ] pendiente"
    new_block = block[:est.start()] + new_line + block[est.end():]
    new_text = text[:start] + new_block + text[end:]
    f.write_text(new_text, encoding="utf-8")
    return True, "ok"
