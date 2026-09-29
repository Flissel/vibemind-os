from datetime import datetime, timezone

import pytest

from core.knowledge import quellen as q
from core.knowledge.schema import pruefen

T = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def fake(antworten):
    """antworten: dict (quelle, ziel-praefix) -> (daten, gesamt, status)"""
    def abfragen(quelle, ziel, mit_zaehlung=False):
        for (qq, praefix), wert in antworten.items():
            if qq == quelle and ziel.startswith(praefix):
                if isinstance(wert, Exception):
                    raise wert
                return wert
        raise AssertionError(f"unerwartete Abfrage {quelle} {ziel}")
    return abfragen


def test_bubble_dokument(monkeypatch):
    monkeypatch.setattr(q, "abfragen", fake({
        ("supabase", "ideas?"): ([{"id": "a1b2c3d4", "title": "Marketing", "status": "raw",
                                   "score": 40, "updated_at": "2026-09-20T10:00:00+00:00",
                                   "promoted_to_project_id": None}], None, 200),
        ("supabase", "canvas_nodes?"): ([], 12, 200),
        ("supabase", "swe_design_runs?"): ([], None, 200),
    }))
    [d] = q.bubbles(T)
    assert d.typ == "bubble" and d.titel == "Marketing"
    assert {f.schluessel: f.wert for f in d.fakten} == {
        "status": "raw", "score": "40", "zuletzt_geaendert": "2026-09-20T10:00:00+00:00", "knoten": "12"}
    assert pruefen(d) == []


def test_agent_zaehlt_audit(monkeypatch):
    monkeypatch.setattr(q, "abfragen", fake({
        ("openfang", "/api/agents"): ([{"id": "id1", "name": "brain-planner", "state": "Running",
                                        "ready": True, "model_provider": "ollama",
                                        "model_name": "qwen", "last_active": "2026-09-23T11:00:00Z"}],
                                      None, 200),
        ("openfang", "/api/audit/recent"): ({"entries": [
            {"agent_id": "id1", "outcome": "ok"}, {"agent_id": "id1", "outcome": "error: x"},
            {"agent_id": "id2", "outcome": "ok"}]}, None, 200),
    }))
    [d] = q.agents(T)
    f = {x.schluessel: x.wert for x in d.fakten}
    assert f["aktionen_ok"] == "1" and f["aktionen_fehler"] == "1" and f["zustand"] == "Running"
    assert d.links == ["PC-Zustand (system)"]
    assert pruefen(d) == []


def test_ausgefallene_quelle_liefert_nichts_und_zaehlt(monkeypatch):
    q.FEHLER.clear()
    monkeypatch.setattr(q, "abfragen", fake({
        ("openfang", "/api/agents"): RuntimeError("Transport: weg"),
    }))
    monkeypatch.setattr(q, "bubbles", lambda jetzt: [])
    monkeypatch.setattr(q, "coding_projekte", lambda jetzt: [])
    monkeypatch.setattr(q, "pc_zustand", lambda jetzt: None)
    monkeypatch.setattr(q, "user", lambda jetzt: None)
    assert q.alle(T) == []
    assert q.FEHLER["agents"] == 1


def test_user_ohne_inhalte_nur_zahlen(monkeypatch):
    monkeypatch.setattr(q, "abfragen", fake({
        ("supabase", "flowzen_checkins?"): ([{"mood": "focused", "energy": 7,
                                             "created_at": "2026-09-23T08:00:00+00:00"}], None, 200),
        ("supabase", "conversation_history?"): ([], 5120, 200),
        ("supabase", "ideas?"): ([], None, 200),
    }))
    d = q.user(T)
    f = {x.schluessel: x.wert for x in d.fakten}
    assert f == {"stimmung": "focused", "energie": "7",
                 "checkin_zeit": "2026-09-23T08:00:00+00:00", "gespraechszeilen": "5120"}
    # Datenschutz: keine Gespraechsinhalte als Fakt
    assert not any("text" in x.schluessel for x in d.fakten)
