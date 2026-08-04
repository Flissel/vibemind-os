"""Closed, read-only stdio MCP status probe for Flowzen Supabase rows."""

from __future__ import annotations

from datetime import datetime
import json
import os
import re
import sys
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request


SERVER_NAME = "spaces-flowzen"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT_SECONDS = 15
_MOODS = frozenset({"energized", "focused", "calm", "tired", "anxious"})
_TIME_WINDOWS = frozenset({
    "early_morning", "morning", "midday", "afternoon", "evening", "night",
})
_CHECKIN_SELECT = "mood,energy,time_window,hour,created_at"
_ACTIVITY_SELECT = "id,event_type,time_window,hour,created_at"
_ACCEPTED_EVENT = re.compile(r"^recommendation_accepted:fzr_[0-9a-f]{16}$")
_OBSERVED_EVENT = re.compile(r"^recommendation_presented:fzr_[0-9a-f]{16}$")

TOOLS: list[dict[str, Any]] = [{
    "name": "flowzen_status",
    "description": "Read the latest independent Flowzen check-in and activity rows.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}]


class ToolError(Exception):
    """Expected public failure, deliberately without provider details."""

    def __init__(self) -> None:
        super().__init__("flowzen_status_unverified")
        self.payload = {"error": "flowzen_status_unverified"}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Expose redirects as failures so service-role headers never follow them."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def _open_without_redirect(request: urllib.request.Request, *, timeout: float):
    """Issue one request while preserving every redirect as an HTTP error."""
    return urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _config(env: Mapping[str, str]) -> tuple[str, str]:
    raw_url = (env.get("SUPABASE_URL") or "").strip()
    service_role_key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not raw_url or not service_role_key:
        raise ToolError()
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        _ = parsed.port
    except (TypeError, ValueError):
        raise ToolError() from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ToolError()
    return raw_url.rstrip("/"), service_role_key


def _read_latest(base_url: str, service_role_key: str, table: str, select: str) -> Mapping[str, Any] | None:
    query = urllib.parse.urlencode({
        "select": select,
        "order": "created_at.desc",
        "limit": "1",
    })
    request = urllib.request.Request(
        f"{base_url}/rest/v1/{table}?{query}",
        method="GET",
        headers={
            "Accept": "application/json",
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
        },
    )
    try:
        with _open_without_redirect(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read()
        if not isinstance(raw, bytes):
            raise ToolError()
        body = json.loads(raw.decode("utf-8"))
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError() from exc
    if not isinstance(body, list) or len(body) > 1:
        raise ToolError()
    if not body:
        return None
    row = body[0]
    if not isinstance(row, Mapping):
        raise ToolError()
    return row


def _bounded_integer(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        return None
    return value


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return value


def _checkin_projection(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    mood = row.get("mood")
    time_window = row.get("time_window")
    energy = _bounded_integer(row.get("energy"), minimum=0, maximum=10)
    hour = _bounded_integer(row.get("hour"), minimum=0, maximum=23)
    created_at = _timestamp(row.get("created_at"))
    if mood not in _MOODS or time_window not in _TIME_WINDOWS or energy is None or hour is None or created_at is None:
        raise ToolError()
    return {
        "mood": mood,
        "energy": energy,
        "time_window": time_window,
        "hour": hour,
        "created_at": created_at,
    }


def _activity_projection(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    activity_id = row.get("id")
    event_type = row.get("event_type")
    time_window = row.get("time_window")
    hour = _bounded_integer(row.get("hour"), minimum=0, maximum=23)
    created_at = _timestamp(row.get("created_at"))
    if (
        not isinstance(activity_id, str)
        or not activity_id.strip()
        or not isinstance(event_type, str)
        or not event_type.strip()
        or hour is None
        or created_at is None
    ):
        raise ToolError()
    if _ACCEPTED_EVENT.fullmatch(event_type) is not None:
        if time_window == "":
            projected_time_window = None
        elif time_window in _TIME_WINDOWS:
            projected_time_window = time_window
        else:
            raise ToolError()
        status = "accepted"
    elif _OBSERVED_EVENT.fullmatch(event_type) is not None and time_window in _TIME_WINDOWS:
        projected_time_window = time_window
        status = "observed"
    else:
        raise ToolError()
    return {
        "status": status,
        "time_window": projected_time_window,
        "hour": hour,
        "created_at": created_at,
    }


def flowzen_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read exactly one latest row from each Flowzen source without mutation."""
    base_url, service_role_key = _config(env if env is not None else os.environ)
    checkin = _read_latest(base_url, service_role_key, "flowzen_checkins", _CHECKIN_SELECT)
    activity = _read_latest(base_url, service_role_key, "flowzen_activity", _ACTIVITY_SELECT)
    return {
        "ok": True,
        "source": "flowzen-supabase",
        "event_id": "rose.status",
        "status": {
            "latest_checkin": _checkin_projection(checkin),
            "latest_activity": _activity_projection(activity),
        },
        "mutated": False,
        "observation": "independent-latest-rows",
    }


def _tool_result(payload: Mapping[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
        "isError": is_error,
    }


def handle_message(message: Any) -> dict[str, Any] | None:
    """Handle the intentionally tiny MCP JSON-RPC surface."""
    if not isinstance(message, Mapping) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    if "id" not in message:
        return None
    request_id = message["id"]
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int, type(None))):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    method = message.get("method")
    if not isinstance(method, str):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {}},
        }}
    if method == "tools/list":
        if "params" in message and (
            not isinstance(message["params"], Mapping) or message["params"]
        ):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "invalid params"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params")
        arguments = params.get("arguments", {}) if isinstance(params, Mapping) else None
        if (
            not isinstance(params, Mapping)
            or params.get("name") != "flowzen_status"
            or not isinstance(arguments, Mapping)
            or arguments
        ):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                ToolError().payload, is_error=True,
            )}
        try:
            status = flowzen_status()
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                exc.payload, is_error=True,
            )}
        return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(status)}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}}


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            response = handle_message(message)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
