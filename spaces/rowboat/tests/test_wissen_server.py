"""Tests fuer den MCP-Server rowboat-wissen (Werkzeuge, Schemas, Einstellungen)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wissen_suche.einbetten import FakeEinbetter  # noqa: E402
from wissen_suche.server import WERKZEUG_BESCHREIBUNG, baue_server, einstellungen_laden  # noqa: E402

PAKET = Path(__file__).resolve().parents[1] / "wissen_suche"


@pytest.fixture
def server(tmp_path):
    wissen = tmp_path / "wissen"
    (wissen / "Notes").mkdir(parents=True)
    (wissen / "Notes" / "Rente.md").write_text("# Rente\nRentenplanung mit Anna", encoding="utf-8")
    e = einstellungen_laden({"WISSEN_ORDNER": str(wissen), "WISSEN_INDEX": str(tmp_path / "i" / "w.sqlite"),
                             "EMBED_MODELL": FakeEinbetter.modell, "EMBED_DIM": str(FakeEinbetter.dim)})
    return baue_server(e, FakeEinbetter())


def _text(ergebnis) -> str:
    # mcp 1.9: Liste von Inhalten; neuere Fassungen: (Inhalte, strukturiert)
    inhalte = ergebnis[0] if isinstance(ergebnis, tuple) else ergebnis
    return inhalte[0].text


def test_werkzeuge_und_schemas(server):
    werkzeuge = {w.name: w for w in asyncio.run(server.list_tools())}
    assert set(werkzeuge) == {"wissen_suchen", "wissen_stand"}
    schema = werkzeuge["wissen_suchen"].inputSchema
    assert schema["required"] == ["frage"]
    assert schema["properties"]["anzahl"]["type"] == "integer"
    assert schema["properties"]["anzahl"]["default"] == 8
    assert werkzeuge["wissen_suchen"].description == WERKZEUG_BESCHREIBUNG
    assert WERKZEUG_BESCHREIBUNG == (
        "Durchsucht das Rowboat-Wissen (Notizen, Kontakt-Steckbriefe, Newsletter, Wochenbild) nach "
        "BEDEUTUNG, auch ohne exakte Wörter. Liefert Pfade unter knowledge/, die mit file-readText "
        "geöffnet werden können. Ist der PC aus, kommen Stichwort-Treffer mit Hinweis.")


def test_aufruf_liefert_json(server):
    erg = json.loads(_text(asyncio.run(server.call_tool("wissen_suchen", {"frage": "Rente"}))))
    assert "modus" in erg
    assert erg["treffer"][0]["pfad"] == "knowledge/Notes/Rente.md"     # leerer Index -> Stichwort
    stand = json.loads(_text(asyncio.run(server.call_tool("wissen_stand", {}))))
    assert {"fertig", "offen", "fehler", "abschnitte", "modell", "pc_erreichbar", "letzte_runde"} <= set(stand)


def test_server_einstellungen(server):
    assert server.name == "wissen"
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 3240
    assert server.settings.stateless_http is True and server.settings.json_response is True
    assert server.settings.streamable_http_path == "/mcp"


def test_einstellungen_strip_und_vorgaben():
    e = einstellungen_laden({"OLLAMA_URL": "http://x:1\r", "EMBED_DIM": " 512\r\n", "WISSEN_PORT": "3241\r"})
    assert e.ollama_url == "http://x:1"
    assert e.dim == 512 and e.port == 3241
    v = einstellungen_laden({})
    assert (v.wissen_ordner, v.index_datei, v.ollama_url, v.modell, v.dim, v.num_ctx, v.intervall_s, v.port) == (
        "/wissen", "/index/wissen.sqlite", "http://100.84.127.84:11435", "qwen3-embedding:0.6b",
        1024, 2048, 600, 3240)
    assert einstellungen_laden({"OLLAMA_URL": "  "}).ollama_url == "http://100.84.127.84:11435"


def test_gesund_route(server):
    from starlette.testclient import TestClient

    with TestClient(server.streamable_http_app()) as client:
        antwort = client.get("/gesund")
    assert antwort.status_code == 200 and antwort.json() == {"ok": True}


def test_server_bindet_nur_loopback():
    text = (PAKET / "server.py").read_text(encoding="utf-8")
    assert "0.0.0.0" not in text
    assert "127.0.0.1" in text
