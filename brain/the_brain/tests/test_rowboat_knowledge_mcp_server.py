"""Contract tests for the Brain T2 knowledge MCP tools (own server, split out
of mcp_server.py in Fix-Runde 1 to keep that server's closed, read-only,
single-tool contract intact -- see test_rowboat_mcp_server.py)."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from core.knowledge.schema import Beleg, Dokument, Fakt
from core.knowledge.tresor import Tresor


ROOT = Path(__file__).resolve().parents[3]
SERVER_PATH = ROOT / "spaces" / "rowboat" / "knowledge_mcp_server.py"


def load_server():
    if not SERVER_PATH.is_file():
        raise AssertionError("spaces/rowboat/knowledge_mcp_server.py must be versioned")
    spec = importlib.util.spec_from_file_location("spaces_rowboat_knowledge_mcp_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Rowboat knowledge MCP server must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def _knowledge_call(server, name, arguments, request_id=1):
    return server.handle_message({
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


_STAND = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def _valider_dok(deutung="Getestet [B1]."):
    return Dokument(
        typ="bubble", id="a1b2c3d4", titel="Marketing", stand=_STAND,
        fakten=[Fakt(schluessel="status", wert="raw", beleg=1)],
        belege=[Beleg(nr=1, quelle="supabase", ziel="ideas?id=eq.a1b2c3d4&select=status",
                       feld="status", wert="raw", gemessen=_STAND)],
        deutung=deutung,
    )


def test_tools_list_exposes_exactly_the_three_knowledge_tools():
    server = load_server()
    response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    namen = {t["name"] for t in response["result"]["tools"]}
    assert namen == {"knowledge_list", "knowledge_read", "knowledge_write"}


def test_knowledge_write_prueft_vor_dem_schreiben(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    server = load_server()
    dok = {
        "typ": "bubble", "id": "a1b2c3d4", "titel": "M", "stand": "2026-09-23T00:00:00+00:00",
        "fakten": [{"schluessel": "s", "wert": "1", "beleg": 1}],
        "belege": [{"nr": 1, "quelle": "supabase", "ziel": "x", "feld": "s", "wert": "1",
                    "gemessen": "2026-09-23T00:00:00+00:00"}],
        "deutung": "Ohne Beleg.",
    }
    response = _knowledge_call(server, "knowledge_write", {"dokument": dok}, request_id=2)
    payload = _payload(response)
    assert payload["ok"] is False
    assert payload["probleme"]
    assert not list(tmp_path.rglob("*.md"))


def test_knowledge_write_schreibt_gueltiges_dokument_ueber_tresor(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    server = load_server()
    dok = json.loads(_valider_dok().model_dump_json())
    response = _knowledge_call(server, "knowledge_write", {"dokument": dok}, request_id=3)
    assert _payload(response) == {"ok": True, "probleme": []}
    assert (tmp_path / "Bubbles" / "Marketing (a1b2c3).md").exists()


def test_knowledge_list_gibt_geschriebene_dokumente_zurueck(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    Tresor(tmp_path).schreiben(_valider_dok())
    server = load_server()
    response = _knowledge_call(server, "knowledge_list", {}, request_id=4)
    assert _payload(response)["dokumente"] == [{
        "typ": "bubble", "id": "a1b2c3d4", "titel": "Marketing",
        "datei": "Bubbles/Marketing (a1b2c3).md",
    }]


def test_knowledge_list_filtert_nach_typ(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    Tresor(tmp_path).schreiben(_valider_dok())
    server = load_server()
    response = _knowledge_call(server, "knowledge_list", {"typ": "agent"}, request_id=5)
    assert _payload(response)["dokumente"] == []


def test_knowledge_read_liest_geschriebene_datei(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    Tresor(tmp_path).schreiben(_valider_dok())
    server = load_server()
    response = _knowledge_call(server, "knowledge_read",
                                {"datei": "Bubbles/Marketing (a1b2c3).md"}, request_id=6)
    payload = _payload(response)
    assert payload["datei"] == "Bubbles/Marketing (a1b2c3).md"
    assert "# Marketing" in payload["inhalt"]


def test_knowledge_read_lehnt_pfade_ausserhalb_der_wurzel_ab(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    server = load_server()
    for boesartig in ("../../.env", "C:/Windows/win.ini"):
        response = _knowledge_call(server, "knowledge_read", {"datei": boesartig}, request_id=7)
        assert response["result"]["isError"] is True, boesartig


def test_knowledge_read_lehnt_nicht_markdown_ab(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    (tmp_path / "Bubbles").mkdir(parents=True)
    (tmp_path / "Bubbles" / "notiz.txt").write_text("kein markdown", encoding="utf-8")
    server = load_server()
    response = _knowledge_call(server, "knowledge_read", {"datei": "Bubbles/notiz.txt"}, request_id=8)
    assert response["result"]["isError"] is True


def test_unknown_tool_is_rejected():
    server = load_server()
    response = _knowledge_call(server, "rowboat_status", {}, request_id=9)
    assert response["result"]["isError"] is True
