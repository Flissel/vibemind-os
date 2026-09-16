"""Versionierter MCP-Server fuer die Research-Bubble-Kopplung.

Startet Recherchelaeufe aus einer Bubble heraus und legt das Ergebnis in
derselben Bubble ab. Der Beleg fuer einen echten Lauf ist die Reportdatei
am diktierten Pfad plus selbst gezaehlte Quellen - nie der Selbstbericht
des Agenten.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

SERVER_NAME = "spaces-research"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT_SECONDS = 15

RESEARCHER_AGENT_ID = "52a6d4df-6eb0-5c55-a200-b984514886ab"
OPENFANG_URL = os.environ.get("OPENFANG_URL", "http://127.0.0.1:4200")
ARTIFACT_DIR = pathlib.Path.home() / ".openfang" / "research-artifacts"

DEPTHS = ("quick", "thorough", "exhaustive")
OUTPUT_STYLES = ("brief", "detailed", "academic", "executive")
CITATION_STYLES = ("inline_url", "footnotes", "academic_apa", "numbered")


class ToolError(Exception):
    pass


def _load_brief_module():
    path = pathlib.Path(__file__).resolve().parent / "brief.py"
    spec = importlib.util.spec_from_file_location("spaces_research_brief", path)
    if spec is None or spec.loader is None:
        raise ToolError("configuration_error: brief.py missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


brief_mod = _load_brief_module()


TOOLS: list[dict[str, Any]] = [
    {
        "name": "research_start",
        "description": (
            "Baut aus Bubble-Inhalt und Brief einen Recherche-Auftrag. Ohne "
            "confirm wird nur die Vorschau zurueckgegeben, kein Lauf gestartet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string", "minLength": 1},
                "brief": {"type": "string", "minLength": 1},
                "depth": {"type": "string", "default": "thorough"},
                "output_style": {"type": "string", "default": "detailed"},
                "citation_style": {"type": "string", "default": "academic_apa"},
                "language": {"type": "string", "default": "german"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["bubble_id", "brief"],
            "additionalProperties": False,
        },
    },
    {
        "name": "research_status",
        "description": "Prueft einen laufenden Auftrag und verbucht ein fertiges Ergebnis.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "minLength": 1}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
]


def _config(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    env = env or os.environ
    url = (env.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise ToolError(
            "configuration_error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set"
        )
    if not url.startswith(("http://", "https://")):
        raise ToolError("configuration_error: SUPABASE_URL must use http or https")
    return url, key


def _request(method: str, path: str, *, params: Mapping[str, str] | None = None, body: Any = None) -> Any:
    url, key = _config()
    endpoint = f"{url}/rest/v1/{path.lstrip('/')}"
    if params:
        endpoint = f"{endpoint}?{urllib.parse.urlencode(params)}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        method=method,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ToolError(f"supabase_http_error: status={exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ToolError("supabase_unreachable") from exc
    if not raw.strip():
        return {"ok": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError("supabase_invalid_response") from exc


def _required_string(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"invalid_arguments: '{key}' must be a non-empty string")
    return value.strip()


def _choice(arguments: Mapping[str, Any], key: str, allowed: tuple[str, ...], default: str) -> str:
    value = arguments.get(key, default)
    if not isinstance(value, str) or value not in allowed:
        raise ToolError(f"invalid_arguments: '{key}' must be one of {', '.join(allowed)}")
    return value


def _read_bubble(bubble_id: str) -> dict:
    rows = _request(
        "GET", "ideas",
        params={"select": "id,title,description", "id": f"eq.{bubble_id}", "limit": "1"},
    )
    if not isinstance(rows, list) or not rows:
        raise ToolError(f"not_found: bubble '{bubble_id}'")
    return rows[0]


def _read_nodes(bubble_id: str) -> list[dict]:
    rows = _request(
        "GET", "canvas_nodes",
        params={"select": "title,content", "linked_idea_id": f"eq.{bubble_id}", "limit": "100"},
    )
    return rows if isinstance(rows, list) else []


def _job_path(job_id: str) -> pathlib.Path:
    return ARTIFACT_DIR / f"research_{job_id}.md"


def _job_state_path(job_id: str) -> pathlib.Path:
    return ARTIFACT_DIR / f"research_{job_id}.job.json"


def _write_job_file(job_id: str, payload: dict) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    _job_state_path(job_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _spawn_agent_call(job_id: str, brief_text: str) -> None:
    raise ToolError("not_implemented: _spawn_agent_call wird in Task 4 gefuellt")


def call_tool(name: str, arguments: Mapping[str, Any]) -> dict:
    if name == "research_start":
        bubble_id = _required_string(arguments, "bubble_id")
        user_brief = _required_string(arguments, "brief")
        depth = _choice(arguments, "depth", DEPTHS, "thorough")
        output_style = _choice(arguments, "output_style", OUTPUT_STYLES, "detailed")
        citation_style = _choice(arguments, "citation_style", CITATION_STYLES, "academic_apa")
        language = arguments.get("language", "german")
        if not isinstance(language, str) or not language.strip():
            raise ToolError("invalid_arguments: 'language' must be a non-empty string")

        bubble = _read_bubble(bubble_id)
        nodes = _read_nodes(bubble_id)
        job_id = brief_mod.new_job_id()
        brief_text = brief_mod.compose_brief(
            bubble_title=bubble.get("title") or bubble_id,
            bubble_nodes=nodes,
            user_brief=user_brief,
            output_path=str(_job_path(job_id)),
            depth=depth,
            output_style=output_style,
            citation_style=citation_style,
            language=language.strip(),
        )

        if not arguments.get("confirm"):
            return {
                "status": "preview",
                "brief": brief_text,
                "note": (
                    "Kein Lauf gestartet. Brief pruefen, bei Bedarf bearbeiten, "
                    "dann mit confirm=true erneut aufrufen."
                ),
            }

        raise ToolError("not_implemented: confirm wird in Task 4 gefuellt")

    if name == "research_status":
        raise ToolError("not_implemented: research_status wird in Task 5 gefuellt")

    raise ToolError(f"unknown_tool: {name}")


def handle_message(message: Mapping[str, Any]) -> dict | None:
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": message_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = call_tool(params.get("name", ""), params.get("arguments") or {})
        except ToolError as exc:
            return {
                "jsonrpc": "2.0", "id": message_id,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            }
        return {
            "jsonrpc": "2.0", "id": message_id,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                ]
            },
        }
    if message_id is None:
        return None
    return {
        "jsonrpc": "2.0", "id": message_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
