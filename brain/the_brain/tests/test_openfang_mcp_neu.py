"""Brain T2 Task 10: openfang_agent_recent / openfang_agent_task.

Prueft die MCP-Werkzeuge gegen `openfang/mcp/openfang_agents_server.py` per
gemocktem `requests.request` -- kein Live-OpenFang (:4200 kann aus sein).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("mcp")

P = Path(__file__).resolve().parents[3] / "openfang" / "mcp" / "openfang_agents_server.py"


def _srv():
    spec = importlib.util.spec_from_file_location("openfang_agents_server", P)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _resp(body, status=200):
    r = MagicMock(status_code=status)
    r.json.return_value = body
    r.text = json.dumps(body)
    return r


def test_agent_recent_filtert(monkeypatch):
    s = _srv()
    agents = [{"id": "id1", "name": "brain-planner"}]
    audit = {"entries": [{"agent_id": "id1", "seq": 1}, {"agent_id": "id2", "seq": 2}]}
    with patch("requests.request", side_effect=[_resp(agents), _resp(audit)]):
        out = asyncio.run(s.call_tool("openfang_agent_recent", {"agent": "brain-planner", "n": 5}))
    daten = json.loads(out[0].text)
    assert [e["seq"] for e in daten] == [1]


def test_agent_task_belegt_mit_audit_sequenz(monkeypatch):
    s = _srv()
    with patch("requests.request", side_effect=[
        _resp([{"id": "id1", "name": "brain-planner"}]),
        _resp({"entries": [{"seq": 100}], "tip_hash": "x"}),
        _resp({"response": "erledigt"}),
        _resp({"entries": [{"seq": 101}], "tip_hash": "y"}),
    ]):
        out = asyncio.run(s.call_tool("openfang_agent_task",
                                      {"agent": "brain-planner", "message": "Status?"}))
    d = json.loads(out[0].text)
    assert d["antwort"] == "erledigt"
    assert d["audit_seq_nachher"] > d["audit_seq_vorher"]


def test_agent_task_faellt_auf_reply_zurueck_wenn_response_feld_fehlt(monkeypatch):
    """Controller-Ruling: response-Feld robust behandeln -- reply/text/rohes JSON
    als Fallback, falls die Nachricht-Route (noch) nicht `response` liefert."""
    s = _srv()
    with patch("requests.request", side_effect=[
        _resp([{"id": "id1", "name": "brain-planner"}]),
        _resp({"entries": [{"seq": 5}]}),
        _resp({"reply": "ok-per-reply-feld"}),
        _resp({"entries": [{"seq": 6}]}),
    ]):
        out = asyncio.run(s.call_tool("openfang_agent_task",
                                      {"agent": "brain-planner", "message": "Status?"}))
    d = json.loads(out[0].text)
    assert d["antwort"] == "ok-per-reply-feld"


def test_agent_task_ohne_bekanntes_feld_gibt_rohes_json_zurueck(monkeypatch):
    s = _srv()
    with patch("requests.request", side_effect=[
        _resp([{"id": "id1", "name": "brain-planner"}]),
        _resp({"entries": [{"seq": 5}]}),
        _resp({"status": "queued"}),
        _resp({"entries": [{"seq": 6}]}),
    ]):
        out = asyncio.run(s.call_tool("openfang_agent_task",
                                      {"agent": "brain-planner", "message": "Status?"}))
    d = json.loads(out[0].text)
    assert json.loads(d["antwort"]) == {"status": "queued"}


def test_agent_recent_unbekannter_agent_gibt_fehlertext_statt_ausnahme(monkeypatch):
    s = _srv()
    with patch("requests.request", side_effect=[_resp([])]):
        out = asyncio.run(s.call_tool("openfang_agent_recent", {"agent": "nicht-da"}))
    assert out[0].text.startswith("error:")
    assert "nicht-da" in out[0].text


def test_agent_task_ohne_message_wird_abgelehnt():
    s = _srv()
    out = asyncio.run(s.call_tool("openfang_agent_task", {"agent": "brain-planner"}))
    assert out[0].text.startswith("error:")
