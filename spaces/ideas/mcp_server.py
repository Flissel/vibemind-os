"""Versioned MCP server for the deterministic Ideas and Bubbles operations.

OpenFang starts this server over stdio.  It deliberately has no local
Supabase defaults and no LLM/provider dependency: every tool call requires
the Proxmox Supabase endpoint and service-role credential from its process
environment.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Mapping


SERVER_NAME = "spaces-ideas"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT_SECONDS = 15

TOOLS: list[dict[str, Any]] = [
    {
        "name": "bubble_list",
        "description": "List top-level Idea-space bubbles.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100}},
        },
    },
    {
        "name": "bubble_get",
        "description": "Get a top-level bubble by its durable id.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1}}, "required": ["id"]},
    },
    {
        "name": "bubble_create",
        "description": "Create a top-level bubble in the Ideas table.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "description": {"type": "string", "default": ""},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            "required": ["title"],
        },
    },
    {
        "name": "bubble_update",
        "description": "Update a top-level bubble by durable id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id"],
        },
    },
    {
        "name": "bubble_delete",
        "description": "Delete one top-level bubble by durable id.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1}}, "required": ["id"]},
    },
    {
        "name": "idea_list",
        "description": "List canvas ideas, optionally scoped to a bubble id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string"},
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "idea_get",
        "description": "Get one canvas idea by durable id.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1}}, "required": ["id"]},
    },
    {
        "name": "idea_create",
        "description": "Create a canvas idea inside a durable bubble id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "content": {"type": "string", "default": ""},
                "node_type": {"type": "string", "default": "note"},
                "x": {"type": "integer", "default": 0},
                "y": {"type": "integer", "default": 0},
            },
            "required": ["bubble_id", "title"],
        },
    },
    {
        "name": "idea_update",
        "description": "Update a canvas idea by durable id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "node_type": {"type": "string"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "idea_delete",
        "description": "Delete a canvas idea and its incident edges by durable id.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1}}, "required": ["id"]},
    },
    {
        "name": "idea_connect",
        "description": "Create a durable canvas edge between two canvas idea ids.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_id": {"type": "string", "minLength": 1},
                "to_id": {"type": "string", "minLength": 1},
                "edge_type": {"type": "string", "default": "related"},
            },
            "required": ["from_id", "to_id"],
        },
    },
    {
        "name": "idea_disconnect",
        "description": "Delete one durable canvas edge by id.",
        "inputSchema": {"type": "object", "properties": {"edge_id": {"type": "string", "minLength": 1}}, "required": ["edge_id"]},
    },
]


class ToolError(Exception):
    """Expected, safe-to-return tool errors."""


def _required_string(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"invalid_arguments: '{name}' must be a non-empty string")
    return value.strip()


def _bounded_limit(arguments: Mapping[str, Any], default: int) -> int:
    value = arguments.get("limit", default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ToolError("invalid_arguments: 'limit' must be an integer from 1 to 100")
    return value


def _config(env: Mapping[str, str]) -> tuple[str, str]:
    url = (env.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise ToolError(
            "configuration_error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set"
        )
    if not url.startswith(("http://", "https://")):
        raise ToolError("configuration_error: SUPABASE_URL must use http or https")
    return url, key


def _request(
    method: str,
    path: str,
    *,
    params: Mapping[str, str] | None = None,
    body: Any = None,
    env: Mapping[str, str] | None = None,
) -> Any:
    url, key = _config(env or os.environ)
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
    except urllib.error.URLError as exc:
        raise ToolError("supabase_unreachable") from exc
    except OSError as exc:
        raise ToolError("supabase_unreachable") from exc
    if not raw.strip():
        return {"ok": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError("supabase_invalid_response") from exc


def _patch_fields(
    arguments: Mapping[str, Any],
    allowed: set[str],
    *,
    string_fields: set[str],
    string_list_fields: set[str] | None = None,
    integer_fields: set[str] | None = None,
) -> dict[str, Any]:
    fields = {key: arguments[key] for key in allowed if key in arguments}
    if not fields:
        raise ToolError("invalid_arguments: at least one mutable field is required")
    for field in string_fields & fields.keys():
        if not isinstance(fields[field], str):
            raise ToolError(f"invalid_arguments: '{field}' must be a string")
    for field in (string_list_fields or set()) & fields.keys():
        value = fields[field]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ToolError(f"invalid_arguments: '{field}' must be an array of strings")
    for field in (integer_fields or set()) & fields.keys():
        value = fields[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolError("invalid_arguments: 'x' and 'y' must be integers")
    return fields


def _first_row(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _normalize_idea_title_content(title: str, content: str) -> tuple[str, str]:
    """Keep canvas titles compact using the current SupabaseIdeasClient rules."""
    if len(title) > 90 and not content:
        separator_index = -1
        for separator in (": ", ":\n", "\n", " - ", " — "):
            index = title.find(separator)
            if 0 < index <= 90:
                separator_index = index
                break
        if separator_index > 0:
            content = title[separator_index:].lstrip(":-—\n ").strip()
            title = title[:separator_index].strip()
        else:
            content = title.strip()
            title = title[:80].rstrip() + "…"
    if len(title) > 120:
        if not content:
            content = title
        title = title[:117].rstrip() + "…"
    return title, content


def call_tool(name: str, arguments: Mapping[str, Any]) -> Any:
    """Call one explicit Ideas/Bubbles operation; no arbitrary table access."""
    if name == "bubble_list":
        return _request(
            "GET",
            "ideas",
            params={
                "select": "id,title,parent_id,score,status,metadata",
                "parent_id": "is.null",
                "limit": str(_bounded_limit(arguments, 20)),
            },
        )
    if name == "bubble_get":
        return _request("GET", "ideas", params={"select": "*", "id": f"eq.{_required_string(arguments, 'id')}", "parent_id": "is.null", "limit": "1"})
    if name == "bubble_create":
        tags = arguments.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ToolError("invalid_arguments: 'tags' must be an array of strings")
        title = _required_string(arguments, "title")
        description = arguments.get("description", "")
        if not isinstance(description, str):
            raise ToolError("invalid_arguments: 'description' must be a string")
        existing = _first_row(_request(
            "GET",
            "ideas",
            params={"select": "*", "title": f"ilike.{title}", "parent_id": "is.null", "limit": "1"},
        ))
        if existing is not None:
            return existing
        return _request(
            "POST",
            "ideas",
            body={
                "id": uuid.uuid4().hex[:8],
                "title": title,
                "description": description,
                "parent_id": None,
                "score": 0,
                "status": "active",
                "source": "spaces-ideas-mcp",
                "tags": tags,
                "metadata": {},
            },
        )
    if name == "bubble_update":
        return _request(
            "PATCH",
            "ideas",
            params={"id": f"eq.{_required_string(arguments, 'id')}", "parent_id": "is.null"},
            body=_patch_fields(
                arguments,
                {"title", "description", "tags"},
                string_fields={"title", "description"},
                string_list_fields={"tags"},
            ),
        )
    if name == "bubble_delete":
        return _request("DELETE", "ideas", params={"id": f"eq.{_required_string(arguments, 'id')}", "parent_id": "is.null"})
    if name == "idea_list":
        bubble_id = arguments.get("bubble_id")
        params = {"select": "*", "limit": str(_bounded_limit(arguments, 50))}
        if bubble_id is not None:
            if not isinstance(bubble_id, str) or not bubble_id.strip():
                raise ToolError("invalid_arguments: 'bubble_id' must be a non-empty string")
            params["linked_idea_id"] = f"eq.{bubble_id.strip()}"
        return _request("GET", "canvas_nodes", params=params)
    if name == "idea_get":
        return _request("GET", "canvas_nodes", params={"select": "*", "id": f"eq.{_required_string(arguments, 'id')}", "limit": "1"})
    if name == "idea_create":
        node_type = arguments.get("node_type", "note")
        content = arguments.get("content", "")
        x, y = arguments.get("x", 0), arguments.get("y", 0)
        if not isinstance(node_type, str) or not node_type.strip():
            raise ToolError("invalid_arguments: 'node_type' must be a non-empty string")
        if not isinstance(content, str):
            raise ToolError("invalid_arguments: 'content' must be a string")
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
            raise ToolError("invalid_arguments: 'x' and 'y' must be integers")
        title, content = _normalize_idea_title_content(
            _required_string(arguments, "title"), content,
        )
        bubble_id = _required_string(arguments, "bubble_id")
        existing = _first_row(_request(
            "GET",
            "canvas_nodes",
            params={"select": "*", "linked_idea_id": f"eq.{bubble_id}", "title": f"ilike.{title}", "limit": "1"},
        ))
        if existing is not None:
            return existing
        return _request(
            "POST",
            "canvas_nodes",
            body={
                "id": uuid.uuid4().hex[:8],
                "node_type": node_type.strip(),
                "title": title,
                "content": content,
                "x": x,
                "y": y,
                "linked_idea_id": bubble_id,
                "metadata": {"width": 200.0, "height": 100.0},
            },
        )
    if name == "idea_update":
        return _request(
            "PATCH",
            "canvas_nodes",
            params={"id": f"eq.{_required_string(arguments, 'id')}"},
            body=_patch_fields(
                arguments,
                {"title", "content", "node_type", "x", "y"},
                string_fields={"title", "content", "node_type"},
                integer_fields={"x", "y"},
            ),
        )
    if name == "idea_delete":
        idea_id = _required_string(arguments, "id")
        _request("DELETE", "canvas_edges", params={"from_node_id": f"eq.{idea_id}"})
        _request("DELETE", "canvas_edges", params={"to_node_id": f"eq.{idea_id}"})
        return _request("DELETE", "canvas_nodes", params={"id": f"eq.{idea_id}"})
    if name == "idea_connect":
        from_id = _required_string(arguments, "from_id")
        to_id = _required_string(arguments, "to_id")
        if from_id == to_id:
            raise ToolError("invalid_arguments: an idea cannot connect to itself")
        edge_type = arguments.get("edge_type", "related")
        if not isinstance(edge_type, str) or not edge_type.strip():
            raise ToolError("invalid_arguments: 'edge_type' must be a non-empty string")
        existing = _first_row(_request(
            "GET",
            "canvas_edges",
            params={
                "select": "*",
                "or": f"(and(from_node_id.eq.{from_id},to_node_id.eq.{to_id}),and(from_node_id.eq.{to_id},to_node_id.eq.{from_id}))",
                "limit": "1",
            },
        ))
        if existing is not None:
            return existing
        return _request(
            "POST",
            "canvas_edges",
            body={"id": uuid.uuid4().hex, "from_node_id": from_id, "to_node_id": to_id, "edge_type": edge_type.strip()},
        )
    if name == "idea_disconnect":
        return _request("DELETE", "canvas_edges", params={"id": f"eq.{_required_string(arguments, 'edge_id')}"})
    raise ToolError(f"unknown_tool: {name}")


def _tool_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
        "isError": is_error,
    }


def handle_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """Handle the supported JSON-RPC MCP methods without writing to stdout."""
    request_id = message.get("id")
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, Mapping):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result({"error": "invalid_arguments: params must be an object"}, is_error=True)}
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result({"error": "invalid_arguments: name and arguments are required"}, is_error=True)}
        try:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(call_tool(name, arguments))}
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result({"error": str(exc)}, is_error=True)}
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "method not found"},
    }


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, Mapping):
                raise ValueError("request must be an object")
            response = handle_message(message)
        except (ValueError, json.JSONDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
