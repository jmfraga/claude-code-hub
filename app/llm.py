import json
import re
import urllib.request
import urllib.error

SYSTEM_PROMPT = """Eres un asistente que analiza el estado de un proyecto de software \
y devuelve un resumen estructurado en JSON. Eres conciso, claro y honesto sobre el \
estado real (verde / amarillo / rojo). Identificas entidades y relaciones que el \
proyecto tiene con otros sistemas, máquinas, servicios y agentes. Respondes SIEMPRE \
en español mexicano y SOLO con JSON válido — sin prosa adicional, sin markdown."""

USER_TEMPLATE = """Analiza este proyecto y devuelve JSON con esta estructura exacta:

{{
  "summary": "2-3 oraciones que resumen estado, foco actual y trabajo reciente",
  "health": "green" | "yellow" | "red",
  "health_reason": "una frase explicando por qué ese color",
  "next_action": "una sola oración sugiriendo el próximo paso concreto",
  "entities": [
    {{"type": "machine" | "service" | "agent" | "project" | "repo" | "device", "name": "..."}}
  ],
  "relations": [
    {{"from": "<este proyecto>", "to": "<entity name>", "type": "runs_on" | "depends_on" | "monitored_by" | "related_to" | "deploys_to"}}
  ]
}}

Reglas:
- health=green si está limpio y avanzando; yellow si hay dirty/ahead/behind o pendientes acumulados; red si lleva tiempo sin commits, muchas tareas atascadas, o señales claras de problemas.
- entities: extrae nombres concretos mencionados en commits/sesiones/pendientes (RPi5, ThinkCentre, M4, Tailscale, Postgres, OpenClaw, Synapse, agente fleet-ops, etc.). Si no hay claros, deja arreglo vacío.
- relations: une SIEMPRE "from": "{name}" hacia cada entity inferida.
- NO inventes datos que no estén en el contexto.

CONTEXTO DEL PROYECTO:
==============================
Nombre: {name}
Ruta: {path}
Repo git: {is_repo}
Rama: {branch}
Estado: dirty={dirty}, ahead={ahead}, behind={behind}
Commits recientes:
{commits}

Pendientes detectados (TODO.md / ROADMAP.md):
{pending}

Sesiones Claude recientes que mencionan el proyecto:
{sessions}

Tmux activo en este proyecto:
{tmux}
==============================

Devuelve SOLO el JSON, sin envolverlo en ```json ni texto adicional."""


def _format_list(items: list[str]) -> str:
    if not items:
        return "(ninguno)"
    return "\n".join(f"- {x}" for x in items)


def build_user_prompt(project: dict, commits: list[dict]) -> str:
    git = project.get("git") or {}
    commits_str = _format_list([f"{c['hash']} · {c['ago']} · {c['subject']}" for c in commits]) if commits else "(ninguno)"
    pending = project.get("pending") or []
    pending_str = _format_list([p["text"] for p in pending]) if pending else "(ninguno)"
    sessions = project.get("recent_sessions") or []
    sessions_str = _format_list([f"{s.get('title','?')} (turnos: {s.get('turn_count','?')})" for s in sessions]) if sessions else "(ninguna)"
    tmux = project.get("active_tmux") or []
    tmux_str = _format_list([t["name"] for t in tmux]) if tmux else "(ninguno)"
    return USER_TEMPLATE.format(
        name=project["name"],
        path=project["path"],
        is_repo=git.get("is_repo", False),
        branch=git.get("branch", "-"),
        dirty=git.get("dirty", 0),
        ahead=git.get("ahead", 0),
        behind=git.get("behind", 0),
        commits=commits_str,
        pending=pending_str,
        sessions=sessions_str,
        tmux=tmux_str,
    )


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    # Strip ```json fences if model insists
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Find first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def summarize(project: dict, commits: list[dict], llm_cfg: dict) -> dict:
    """Call the configured LLM and return parsed structured result.

    Returns {ok: True, ...} or {ok: False, error: str}.
    """
    url = (llm_cfg or {}).get("url")
    model = (llm_cfg or {}).get("model")
    timeout = int((llm_cfg or {}).get("timeout") or 60)
    if not url or not model:
        return {"ok": False, "error": "llm not configured"}

    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 700,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(project, commits)},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"llm unreachable: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"llm error: {e}"}

    try:
        resp_json = json.loads(body)
        content = resp_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"bad llm response: {e}"}

    parsed = _extract_json(content)
    if not parsed:
        return {"ok": False, "error": "llm did not return valid JSON", "raw": content[:500]}

    # Normalize / clamp
    parsed.setdefault("summary", "")
    parsed.setdefault("health", "yellow")
    if parsed["health"] not in ("green", "yellow", "red"):
        parsed["health"] = "yellow"
    parsed.setdefault("health_reason", "")
    parsed.setdefault("next_action", "")
    parsed.setdefault("entities", [])
    parsed.setdefault("relations", [])
    return {"ok": True, **parsed}
