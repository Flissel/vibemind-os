"""Strict, durable-ID contract shared by the Ideas MCP execution path."""

from __future__ import annotations

import json
import re
from typing import Any, Optional


_ARGUMENT_FIELDS = {"from_id", "to_id", "edge_type"}
_RECEIPT_FIELDS = {"edge_id", "from_id", "to_id", "edge_type"}
_DURABLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
_EDGE_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$")
CANONICAL_IDEA_CONNECT_TARGET = "mcp:brain-ideas:spaces-ideas:idea_connect"


def is_canonical_durable_id(value: Any) -> bool:
    return isinstance(value, str) and _DURABLE_ID_PATTERN.fullmatch(value) is not None


def is_canonical_edge_type(value: Any) -> bool:
    return isinstance(value, str) and _EDGE_TYPE_PATTERN.fullmatch(value) is not None


def canonical_idea_connect_validator_config() -> dict[str, Any]:
    """Return a fresh canonical validator config for every execution."""
    return {
        "kind": "truth:supabase_edge_ids",
        "on_fail": "block",
        "require_verified": True,
        "postcondition": {"check": "supabase_edge_ids", "expect": "present"},
    }


def canonical_idea_connect_arguments(value: Any) -> tuple[Optional[dict[str, str]], str]:
    """Accept only the MCP tool's durable-ID input shape, without aliases."""
    candidate = value
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError:
            return None, "clarification required: from_id and to_id"
    if not isinstance(candidate, dict) or not set(candidate).issubset(_ARGUMENT_FIELDS):
        return None, "clarification required: only from_id, to_id, and edge_type are accepted"

    from_id = candidate.get("from_id")
    to_id = candidate.get("to_id")
    if not is_canonical_durable_id(from_id) or not is_canonical_durable_id(to_id):
        return None, "clarification required: from_id and to_id"
    if from_id == to_id:
        return None, "clarification required: from_id and to_id must differ"

    arguments = {"from_id": from_id, "to_id": to_id}
    if "edge_type" in candidate:
        edge_type = candidate.get("edge_type")
        if not is_canonical_edge_type(edge_type):
            return None, "clarification required: edge_type is invalid"
        arguments["edge_type"] = edge_type
    return arguments, ""


def extract_idea_connect_mcp_receipt(value: Any) -> tuple[Optional[dict[str, str]], str]:
    """Read the exact JSON text receipt returned by the canonical MCP tool."""
    if not isinstance(value, dict) or value.get("isError") is not False:
        return None, "MCP result envelope is missing or failed"
    content = value.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None, "MCP result receipt is missing"
    item = content[0]
    if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
        return None, "MCP result receipt is invalid"
    try:
        receipt = json.loads(item["text"])
    except json.JSONDecodeError:
        return None, "MCP result receipt is invalid"
    if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_FIELDS:
        return None, "MCP result receipt is invalid"

    arguments, reason = canonical_idea_connect_arguments({
        name: receipt[name] for name in _ARGUMENT_FIELDS if name in receipt
    })
    edge_id = receipt.get("edge_id")
    if (arguments is None or not is_canonical_durable_id(edge_id)
            or "edge_type" not in arguments):
        return None, reason or "MCP result receipt is invalid"
    return {"edge_id": edge_id, **arguments}, ""
