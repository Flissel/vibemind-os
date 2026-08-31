"""Versioned MCP server for the deterministic Ideas and Bubbles operations.

OpenFang starts this server over stdio.  It deliberately has no local
Supabase defaults and no LLM/provider dependency: every tool call requires
the Proxmox Supabase endpoint and service-role credential from its process
environment.
"""

from __future__ import annotations

import json
import os
import re
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
BUBBLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
IDEA_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
EDGE_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$")

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
        "name": "bubble_promote",
        "description": "Promote one canonical top-level bubble into a project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$",
                },
            },
            "required": ["bubble_id"],
            "additionalProperties": False,
        },
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
                "from_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$",
                },
                "to_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$",
                },
                "edge_type": {
                    "type": "string",
                    "default": "related",
                    "minLength": 1,
                    "maxLength": 64,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$",
                },
            },
            "required": ["from_id", "to_id"],
            "additionalProperties": False,
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


def _canonical_bubble_id(arguments: Mapping[str, Any]) -> str:
    """Require the stored bubble id verbatim; names and aliases are not accepted."""
    value = arguments.get("bubble_id")
    if not isinstance(value, str) or not value or value != value.strip():
        raise ToolError("invalid_arguments: 'bubble_id' must be a canonical non-empty string")
    if set(arguments) != {"bubble_id"}:
        raise ToolError("invalid_arguments: bubble_promote only accepts 'bubble_id'")
    if BUBBLE_ID_PATTERN.fullmatch(value) is None:
        raise ToolError("invalid_arguments: 'bubble_id' must be a canonical durable id")
    return value


def _canonical_idea_connect_arguments(arguments: Mapping[str, Any]) -> tuple[str, str, str]:
    """Validate the opaque edge contract without rewriting caller input."""
    allowed = {"from_id", "to_id", "edge_type"}
    if not set(arguments).issubset(allowed):
        raise ToolError("invalid_arguments: idea_connect only accepts from_id, to_id, and edge_type")

    def _opaque_id(name: str) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or not value or value != value.strip():
            raise ToolError(f"invalid_arguments: '{name}' must be a canonical durable id")
        if IDEA_ID_PATTERN.fullmatch(value) is None:
            raise ToolError(f"invalid_arguments: '{name}' must be a canonical durable id")
        return value

    from_id = _opaque_id("from_id")
    to_id = _opaque_id("to_id")
    if from_id == to_id:
        raise ToolError("invalid_arguments: an idea cannot connect to itself")
    edge_type = arguments.get("edge_type", "related")
    if (
        not isinstance(edge_type, str)
        or not edge_type
        or edge_type != edge_type.strip()
        or EDGE_TYPE_PATTERN.fullmatch(edge_type) is None
    ):
        raise ToolError("invalid_arguments: 'edge_type' must be a canonical safe token")
    return from_id, to_id, edge_type


def _read_exact_canvas_node(durable_id: str, argument_name: str) -> None:
    """Require one exact node readback before any edge lookup or mutation."""
    try:
        response = _request(
            "GET",
            "canvas_nodes",
            params={"select": "id", "id": f"eq.{durable_id}", "limit": "1"},
        )
    except ToolError as exc:
        raise ToolError(f"idea_connect_node_unverified: {argument_name}") from exc
    if (
        not isinstance(response, list)
        or len(response) != 1
        or not isinstance(response[0], Mapping)
        or response[0].get("id") != durable_id
    ):
        raise ToolError(f"idea_connect_node_unverified: {argument_name}")


def _normalize_connected_edge(
    response: Any,
    *,
    from_id: str,
    to_id: str,
    edge_type: str,
    error: str,
    allow_reverse: bool,
    require_edge_type_match: bool,
) -> dict[str, str]:
    """Accept exactly one attested edge row and expose only its contract fields."""
    if not isinstance(response, list) or len(response) != 1 or not isinstance(response[0], Mapping):
        raise ToolError(error)
    row = response[0]
    edge_id = row.get("id")
    actual_from_id = row.get("from_node_id")
    actual_to_id = row.get("to_node_id")
    actual_edge_type = row.get("edge_type")
    if (
        not isinstance(edge_id, str)
        or IDEA_ID_PATTERN.fullmatch(edge_id) is None
        or not isinstance(actual_from_id, str)
        or not isinstance(actual_to_id, str)
        or not isinstance(actual_edge_type, str)
        or EDGE_TYPE_PATTERN.fullmatch(actual_edge_type) is None
        or (require_edge_type_match and actual_edge_type != edge_type)
        or (actual_from_id, actual_to_id) not in (
            ((from_id, to_id), (to_id, from_id)) if allow_reverse else ((from_id, to_id),)
        )
    ):
        raise ToolError(error)
    return {
        "edge_id": edge_id,
        "from_id": actual_from_id,
        "to_id": actual_to_id,
        "edge_type": actual_edge_type,
    }


def _read_existing_connected_edge(
    from_id: str,
    to_id: str,
    edge_type: str,
) -> dict[str, str] | None:
    """Perform fixed-direction exact filters; never interpolate a PostgREST expression."""
    query = {"select": "id,from_node_id,to_node_id,edge_type", "limit": "1"}
    for source_id, target_id in ((from_id, to_id), (to_id, from_id)):
        try:
            response = _request(
                "GET",
                "canvas_edges",
                params={
                    **query,
                    "from_node_id": f"eq.{source_id}",
                    "to_node_id": f"eq.{target_id}",
                },
            )
        except ToolError as exc:
            raise ToolError("idea_connect_lookup_unverified") from exc
        if response == []:
            continue
        return _normalize_connected_edge(
            response,
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_type,
            error="idea_connect_existing_unverified",
            allow_reverse=True,
            require_edge_type_match=False,
        )
    return None


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


def _compensate_project(project_id: str, error: str) -> None:
    """Remove an unlinked project and never turn an uncertain promotion into success."""
    try:
        _request("DELETE", "projects", params={"id": f"eq.{project_id}"})
    except ToolError as exc:
        raise ToolError(f"{error}; compensation_failed: {exc}") from exc
    raise ToolError(error)


def _is_definitive_project_post_error(error: ToolError) -> bool:
    match = re.fullmatch(r"supabase_http_error: status=(\d{3})", str(error))
    return match is not None and 400 <= int(match.group(1)) < 500


def _owned_promotion_project(
    project: Mapping[str, Any],
    *,
    project_id: str,
    bubble_id: str,
    promotion_attempt_id: str,
) -> bool:
    metadata = project.get("metadata")
    return (
        project.get("id") == project_id
        and project.get("from_idea_id") == bubble_id
        and isinstance(metadata, Mapping)
        and metadata.get("promotion_attempt_id") == promotion_attempt_id
        and metadata.get("source_bubble_id") == bubble_id
    )


def _read_owned_promotion_project(
    *,
    project_id: str,
    bubble_id: str,
    promotion_attempt_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read back a project and attest it belongs to this promotion attempt."""
    try:
        project = _first_row(_request(
            "GET",
            "projects",
            params={"select": "*", "id": f"eq.{project_id}", "limit": "1"},
        ))
    except ToolError as exc:
        return None, f"readback_failed: {exc}"
    if project is None:
        return None, "not_found"
    if not _owned_promotion_project(
        project,
        project_id=project_id,
        bubble_id=bubble_id,
        promotion_attempt_id=promotion_attempt_id,
    ):
        return None, "ownership_mismatch"
    return project, None


def _reconcile_ambiguous_project_post_failure(
    *,
    project_id: str,
    bubble_id: str,
    promotion_attempt_id: str,
    create_error: str,
) -> None:
    """Clean up only a project conclusively owned by an ambiguous failed POST."""
    project, reason = _read_owned_promotion_project(
        project_id=project_id,
        bubble_id=bubble_id,
        promotion_attempt_id=promotion_attempt_id,
    )
    if project is None:
        if reason == "not_found":
            raise ToolError(create_error)
        if reason is not None and reason.startswith("readback_failed: "):
            reason = reason.removeprefix("readback_failed: ")
        raise ToolError(f"{create_error}; cleanup_skipped_unverified: {reason}")
    _compensate_project(project_id, create_error)


def _reconcile_unrepresented_project_create(
    *,
    project_id: str,
    bubble_id: str,
    promotion_attempt_id: str,
) -> None:
    """Fail closed after an HTTP-success create without a verifiable representation."""
    project, reason = _read_owned_promotion_project(
        project_id=project_id,
        bubble_id=bubble_id,
        promotion_attempt_id=promotion_attempt_id,
    )
    if project is None:
        raise ToolError(f"bubble_promotion_create_unverified: {reason}")
    _compensate_project(project_id, "bubble_promotion_create_unverified")


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
    if name == "bubble_promote":
        bubble_id = _canonical_bubble_id(arguments)
        bubble = _first_row(_request(
            "GET",
            "ideas",
            params={
                "select": "*",
                "id": f"eq.{bubble_id}",
                "parent_id": "is.null",
                "limit": "1",
            },
        ))
        if bubble is None:
            raise ToolError("bubble_not_found")

        title = bubble.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ToolError("bubble_invalid: title must be a non-empty string")
        project_id = uuid.uuid4().hex[:8]
        promotion_attempt_id = uuid.uuid4().hex
        project_row = {
            "id": project_id,
            "name": title.strip(),
            "description": str(bubble.get("description") or "").strip(),
            "status": "active",
            "from_idea_id": bubble_id,
            "metadata": {
                "source_space": "bubbles",
                "source_bubble_id": bubble_id,
                "source_score": bubble.get("score", 0),
                "promotion_attempt_id": promotion_attempt_id,
            },
        }
        try:
            project = _first_row(_request("POST", "projects", body=project_row))
        except ToolError as exc:
            create_error = f"bubble_promotion_create_failed: {exc}"
            if _is_definitive_project_post_error(exc):
                raise ToolError(create_error) from exc
            _reconcile_ambiguous_project_post_failure(
                project_id=project_id,
                bubble_id=bubble_id,
                promotion_attempt_id=promotion_attempt_id,
                create_error=create_error,
            )
        if project is None:
            _reconcile_unrepresented_project_create(
                project_id=project_id,
                bubble_id=bubble_id,
                promotion_attempt_id=promotion_attempt_id,
            )
        if not _owned_promotion_project(
            project,
            project_id=project_id,
            bubble_id=bubble_id,
            promotion_attempt_id=promotion_attempt_id,
        ):
            raise ToolError("bubble_promotion_create_unverified")

        link_error: str | None = None
        try:
            linked = _first_row(_request(
                "PATCH",
                "ideas",
                params={
                    "id": f"eq.{bubble_id}",
                    "parent_id": "is.null",
                    "promoted_to_project_id": "is.null",
                },
                body={"status": "promoted", "promoted_to_project_id": project_id},
            ))
            if linked is None:
                link_error = "link_returned_no_row"
            elif linked.get("id") != bubble_id or linked.get("promoted_to_project_id") != project_id:
                link_error = "link_mismatch"
        except ToolError as exc:
            link_error = str(exc)
        if link_error is not None:
            _compensate_project(project_id, f"bubble_promotion_link_failed: {link_error}")
        return project
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
        from_id, to_id, edge_type = _canonical_idea_connect_arguments(arguments)
        _read_exact_canvas_node(from_id, "from_id")
        _read_exact_canvas_node(to_id, "to_id")
        existing = _read_existing_connected_edge(from_id, to_id, edge_type)
        if existing is not None:
            return existing
        try:
            created = _request(
                "POST",
                "canvas_edges",
                body={
                    "id": uuid.uuid4().hex,
                    "from_node_id": from_id,
                    "to_node_id": to_id,
                    "edge_type": edge_type,
                },
            )
        except ToolError as exc:
            raise ToolError("idea_connect_create_unverified") from exc
        return _normalize_connected_edge(
            created,
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_type,
            error="idea_connect_create_unverified",
            allow_reverse=False,
            require_edge_type_match=True,
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
