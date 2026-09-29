"""Read-only stdio MCP status probe for the canonical Rowboat service."""

from __future__ import annotations

import json
import os
import sys
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

# Brain T2: dieselbe Tresor-Logik wie der Kurator (Pruefung vor dem Schreiben).
_BRAIN = Path(__file__).resolve().parents[2] / "brain" / "the_brain"
if str(_BRAIN) not in sys.path:
    sys.path.insert(0, str(_BRAIN))


SERVER_NAME = "spaces-rowboat"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT_SECONDS = 5

TOOLS: list[dict[str, Any]] = [
    {
        "name": "rowboat_status",
        "description": "Read the configured Rowboat HTTP status using HEAD.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "knowledge_list",
        "description": "Wissensdokumente auflisten (optional nach typ).",
        "inputSchema": {"type": "object", "properties": {"typ": {"type": "string"}}},
    },
    {
        "name": "knowledge_read",
        "description": "Ein Wissensdokument (Markdown) lesen.",
        "inputSchema": {"type": "object", "properties": {"datei": {"type": "string"}},
                         "required": ["datei"]},
    },
    {
        "name": "knowledge_write",
        "description": "Wissensdokument schreiben - nur wenn Fakten und Deutung belegt sind.",
        "inputSchema": {"type": "object", "properties": {"dokument": {"type": "object"}},
                         "required": ["dokument"]},
    },
]


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
    return ToolError("configuration_error: ROWBOAT_URL is invalid")


def _is_noncanonical_numeric_host(hostname: str) -> bool:
    labels = hostname.split(".")
    return (
        hostname.isdigit()
        or hostname.startswith(("0x", "0o"))
        or (len(labels) > 1 and all(label.isdigit() for label in labels))
    )


def _rowboat_url(env: Mapping[str, str]) -> str:
    raw_url = (env.get("ROWBOAT_URL") or "").strip()
    if not raw_url:
        raise ToolError("configuration_error: ROWBOAT_URL is required")
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


def rowboat_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Probe the explicitly configured service once; never use a fallback URL."""
    url = _rowboat_url(env or os.environ)
    try:
        request = urllib.request.Request(url, method="HEAD")
        with _open_head_without_redirect(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.getcode())
    except urllib.error.HTTPError as exc:
        return {
            "ok": 200 <= exc.code < 400,
            "source": "rowboat-http",
            "url": url,
            "http_status": exc.code,
        }
    except Exception as exc:
        raise ToolError(
            "rowboat_unverified",
            {"ok": False, "source": "rowboat-http", "url": url, "error": type(exc).__name__},
        ) from exc
    return {
        "ok": 200 <= status < 400,
        "source": "rowboat-http",
        "url": url,
        "http_status": status,
    }


def _tool_result(payload: Mapping[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
        "isError": is_error,
    }


def _knowledge_root() -> Path:
    """KNOWLEDGE_DIR wird pro Aufruf gelesen, nicht beim Import (Tresor-Default gilt nur als Fallback)."""
    return Path(os.environ.get("KNOWLEDGE_DIR") or Path.home() / ".rowboat" / "knowledge")


def _sicherer_wissenspfad(wurzel: Path, datei: str) -> "Path | None":
    """Loest `datei` relativ zum Wissensordner auf. Liefert None bei Pfaden ausserhalb
    der Wurzel (auch absolute Pfade und `..`) oder wenn die Datei nicht auf `.md` endet."""
    try:
        kandidat = (wurzel / datei).resolve()
        wurzel_resolved = wurzel.resolve()
    except (OSError, ValueError):
        return None
    if kandidat != wurzel_resolved and wurzel_resolved not in kandidat.parents:
        return None
    if kandidat.suffix.lower() != ".md":
        return None
    return kandidat


def _knowledge_tool_call(name: str, arguments: Any) -> dict[str, Any]:
    """Bearbeitet knowledge_list/knowledge_read/knowledge_write. Schreiben laeuft
    ausschliesslich ueber Tresor.schreiben (Pruefung vor jedem Schreiben)."""
    from core.knowledge.schema import ORDNER, Dokument, dateiname
    from core.knowledge.tresor import Tresor

    if not isinstance(arguments, Mapping):
        return _tool_result({"error": "invalid_arguments: arguments must be an object"}, is_error=True)
    tresor = Tresor(_knowledge_root())

    if name == "knowledge_list":
        typ = arguments.get("typ")
        dokumente = [
            {"typ": d.typ, "id": d.id, "titel": d.titel, "datei": f"{ORDNER[d.typ]}/{dateiname(d)}.md"}
            for d in tresor.alle() if not typ or d.typ == typ
        ]
        return _tool_result({"dokumente": dokumente})

    if name == "knowledge_read":
        datei = arguments.get("datei")
        if not isinstance(datei, str) or not datei:
            return _tool_result({"error": "invalid_arguments: datei is required"}, is_error=True)
        pfad = _sicherer_wissenspfad(tresor.wurzel, datei)
        if pfad is None or not pfad.is_file():
            return _tool_result(
                {"error": "invalid_path: datei must be a markdown file inside the knowledge root"},
                is_error=True,
            )
        return _tool_result({"datei": datei, "inhalt": pfad.read_text(encoding="utf-8")})

    # knowledge_write
    try:
        dok = Dokument(**arguments["dokument"])
    except Exception as exc:
        return _tool_result({"ok": False, "probleme": [f"Schema: {exc}"]})
    ok, probleme = tresor.schreiben(dok)
    return _tool_result({"ok": ok, "probleme": probleme})


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
        if not isinstance(params, Mapping):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": "invalid_params: params must be an object"}, is_error=True,
            )}
        name = params.get("name")
        if name in ("knowledge_list", "knowledge_read", "knowledge_write"):
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": _knowledge_tool_call(name, params.get("arguments"))}
        if name != "rowboat_status":
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": f"unknown_tool: {name!r} is not supported"}, is_error=True,
            )}
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": "invalid_arguments: arguments must be an object"}, is_error=True,
            )}
        if arguments:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": "invalid_arguments: rowboat_status accepts no arguments"}, is_error=True,
            )}
        try:
            payload = rowboat_status()
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(payload)}
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
