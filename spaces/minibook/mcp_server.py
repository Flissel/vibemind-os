"""Read-only stdio MCP status probe for the Minibook projection service."""

from __future__ import annotations

import ipaddress
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping


SERVER_NAME = "spaces-minibook"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT_SECONDS = 5

TOOLS: list[dict[str, Any]] = [{
    "name": "minibook_status",
    "description": "Read the configured Minibook HTTP status using HEAD.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}]


class ToolError(Exception):
    """Expected, safe-to-return tool error."""

    def __init__(self, message: str, payload: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = dict(payload or {"error": message})


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Preserve the original HEAD request and expose every 3xx response."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def _open_head_without_redirect(request: urllib.request.Request, *, timeout: float):
    """Open one HEAD request without following or converting redirects."""
    return urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _invalid_url() -> ToolError:
    return ToolError("configuration_error: MINIBOOK_STATUS_URL is invalid")


def _is_noncanonical_numeric_host(hostname: str) -> bool:
    labels = hostname.split(".")
    return (
        hostname.isdigit()
        or hostname.startswith(("0x", "0o"))
        or (len(labels) > 1 and all(label.isdigit() for label in labels))
    )


def _minibook_status_url(env: Mapping[str, str]) -> str:
    raw_url = (env.get("MINIBOOK_STATUS_URL") or "").strip()
    if not raw_url:
        raise ToolError("configuration_error: MINIBOOK_STATUS_URL is required")
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        _ = parsed.port
    except (TypeError, ValueError):
        raise _invalid_url() from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise _invalid_url()
    if parsed.username is not None or parsed.password is not None:
        raise _invalid_url()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise _invalid_url()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if _is_noncanonical_numeric_host(hostname):
            raise _invalid_url()
    else:
        mapped = getattr(address, "ipv4_mapped", None)
        if (
            address.is_loopback
            or address.is_unspecified
            or (mapped is not None and (mapped.is_loopback or mapped.is_unspecified))
        ):
            raise _invalid_url()
    return raw_url.rstrip("/")


def minibook_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Probe the explicitly configured service once; never use a fallback URL."""
    url = _minibook_status_url(env or os.environ)
    try:
        request = urllib.request.Request(url, method="HEAD")
        with _open_head_without_redirect(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.getcode())
    except urllib.error.HTTPError as exc:
        return {
            "ok": 200 <= exc.code < 300,
            "source": "minibook-http",
            "http_status": exc.code,
        }
    except Exception as exc:
        raise ToolError(
            "minibook_unverified",
            {"ok": False, "source": "minibook-http", "error": type(exc).__name__},
        ) from exc
    return {
        "ok": 200 <= status < 300,
        "source": "minibook-http",
        "http_status": status,
    }


def _tool_result(payload: Mapping[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
        "isError": is_error,
    }


def handle_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {}},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, Mapping) or params.get("name") != "minibook_status":
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": "unknown_tool: minibook_status is the only supported tool"}, is_error=True,
            )}
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": "invalid_arguments: arguments must be an object"}, is_error=True,
            )}
        if arguments:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": "invalid_arguments: minibook_status accepts no arguments"}, is_error=True,
            )}
        try:
            payload = minibook_status()
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _tool_result(payload, is_error=payload.get("ok") is not True),
            }
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(exc.payload, is_error=True)}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}}


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
