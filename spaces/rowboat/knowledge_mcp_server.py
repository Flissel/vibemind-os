"""Stdio MCP server for the Brain T2 knowledge vault (read/write).

Deliberately separate from `mcp_server.py` (the closed, deterministic,
read-only `rowboat_status` probe, live since 2026-08-04 with its own
closed-tool contract test). This server owns the three knowledge tools
instead so `mcp_server.py`'s "exactly one read-only tool" contract stays
intact. Writing only ever happens through `Tresor.schreiben` (checked
before every write, never a raw file write).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

# Brain T2: dieselbe Tresor-Logik wie der Kurator (Pruefung vor dem Schreiben).
_BRAIN = Path(__file__).resolve().parents[2] / "brain" / "the_brain"
if str(_BRAIN) not in sys.path:
    sys.path.insert(0, str(_BRAIN))


SERVER_NAME = "spaces-rowboat-knowledge"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
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
        "description": "Wissensdokument schreiben - nur wenn Fakten und Deutung belegt und alle Belege an ihrer Quelle bestaetigt sind.",
        "inputSchema": {"type": "object", "properties": {"dokument": {"type": "object"}},
                         "required": ["dokument"]},
    },
]


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
    # Brain T2 Schlusspruefung M3 (Datenschutz): nur die oberste Ebene der
    # Wissensordner (ORDNER) und Hubs/ - andere Rowboat-Ordner (z.B.
    # Bewerbung/, Notes/) und Unterordner wie Veraltet/ bleiben verborgen.
    from core.knowledge.schema import ORDNER
    teile = kandidat.relative_to(wurzel_resolved).parts
    if len(teile) != 2 or teile[0] not in set(ORDNER.values()) | {"Hubs"}:
        return None
    return kandidat


def _knowledge_tool_call(name: str, arguments: Any) -> dict[str, Any]:
    """Bearbeitet knowledge_list/knowledge_read/knowledge_write. Schreiben laeuft
    ausschliesslich ueber Tresor.schreiben (Pruefung vor jedem Schreiben)."""
    from core.knowledge import nachfrage
    from core.knowledge.schema import ORDNER, Dokument, dateiname, pruefen
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
    # Innere Konsistenz zuerst (billig, ohne Netz) ...
    probleme = pruefen(dok)
    if probleme:
        return _tool_result({"ok": False, "probleme": probleme})
    # ... dann Brain T2 Schlusspruefung I7: jeder Beleg muss an seiner
    # Quelle bestaetigt werden (True). False = falsch, None = nicht pruefbar
    # -> ablehnen, nichts schreiben. So kann ein Agent keine erfundenen
    # Fakten/Belege in den Tresor bringen.
    nicht_bestaetigt = [
        f"Beleg B{b.nr} an der Quelle nicht bestaetigt: {b.quelle} {b.ziel} {b.feld}"
        for b in dok.belege if nachfrage.nachfragen(b) is not True
    ]
    if nicht_bestaetigt:
        return _tool_result({"ok": False, "probleme": nicht_bestaetigt})
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
        if name not in ("knowledge_list", "knowledge_read", "knowledge_write"):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": f"unknown_tool: {name!r} is not supported"}, is_error=True,
            )}
        return {"jsonrpc": "2.0", "id": request_id,
                "result": _knowledge_tool_call(name, params.get("arguments"))}
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
