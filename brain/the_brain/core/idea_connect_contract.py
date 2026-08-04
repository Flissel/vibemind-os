"""Strict, durable-ID contract shared by the Ideas MCP execution path."""

from __future__ import annotations

import json
from typing import Any, Optional


_ARGUMENT_FIELDS = {"from_id", "to_id", "edge_type"}
_RECEIPT_FIELDS = {"edge_id", "from_id", "to_id", "edge_type"}


def _safe_text(value: Any, *, maximum: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or any(ord(char) < 32 for char in cleaned):
        return None
    return cleaned


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

    from_id = _safe_text(candidate.get("from_id"), maximum=200)
    to_id = _safe_text(candidate.get("to_id"), maximum=200)
    if not from_id or not to_id:
        return None, "clarification required: from_id and to_id"

    arguments = {"from_id": from_id, "to_id": to_id}
    if "edge_type" in candidate:
        edge_type = _safe_text(candidate.get("edge_type"), maximum=80)
        if not edge_type:
            return None, "clarification required: edge_type must be a nonblank string"
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
    edge_id = _safe_text(receipt.get("edge_id"), maximum=200)
    if arguments is None or edge_id is None or "edge_type" not in arguments:
        return None, reason or "MCP result receipt is invalid"
    return {"edge_id": edge_id, **arguments}, ""
