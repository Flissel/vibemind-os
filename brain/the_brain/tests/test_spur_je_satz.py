"""Jeder Satz hinterlaesst einen Spur-Eintrag, auch auf festen Wegen (Lage-Abfrage 24.11.2026).

Nur trace_id, Weg, ok und Zeit; nie der Satz oder die Antwort.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.plan_executor import PlanRecorder  # noqa: E402
from web.routers import introspection as intro  # noqa: E402

SATZ = "Hallo, geheimer Satz zum Stadtfest"


def _client(monkeypatch, tmp_path, antwort):
    async def _fest(request):
        return antwort

    monkeypatch.setattr(intro, "_multihop_execute", _fest)
    app = FastAPI()
    app.include_router(intro.router)
    recorder = PlanRecorder(path=tmp_path / "multihop_history.jsonl")
    app.state.plan_executor = SimpleNamespace(recorder=recorder)
    return TestClient(app), recorder


def test_fester_weg_ohne_eintrag_bekommt_spur_ohne_satz(monkeypatch, tmp_path) -> None:
    antwort = JSONResponse({"ok": True, "executed": {}, "final_text": "Hallo! Wie kann ich dir helfen?",
                            "trace_id": "tr_spur_1", "weg": "hoeflichkeit"})
    client, recorder = _client(monkeypatch, tmp_path, antwort)
    client.post("/api/multihop/execute", json={"intent": SATZ})
    eintrag = recorder.get_by_trace("tr_spur_1")
    assert eintrag["routed_via"] == "hoeflichkeit" and eintrag["ok"] is True and eintrag["spur"] is True
    zeilen = (tmp_path / "multihop_history.jsonl").read_text(encoding="utf-8")
    assert "tr_spur_1" in zeilen
    assert "geheimer" not in zeilen and "Wie kann ich" not in zeilen
    # Sichtbar in der Liste der Spuren.
    liste = client.get("/api/trace?limit=5").json()["traces"]
    assert [t["routed_via"] for t in liste if t["trace_id"] == "tr_spur_1"] == ["hoeflichkeit"]


def test_vorhandener_eintrag_bleibt_unveraendert(monkeypatch, tmp_path) -> None:
    antwort = JSONResponse({"ok": False, "rueckfrage": True, "final_text": "Soll ich ...?", "trace_id": "tr_spur_2"})
    client, recorder = _client(monkeypatch, tmp_path, antwort)
    recorder.record_lite("tr_spur_2", "x", "ideen-rueckfrage", "Soll ich ...?")
    client.post("/api/multihop/execute", json={"intent": SATZ})
    assert recorder.get_by_trace("tr_spur_2")["routed_via"] == "ideen-rueckfrage"
    zeilen = (tmp_path / "multihop_history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 1


def test_ohne_weg_wird_die_art_der_antwort_genannt(monkeypatch, tmp_path) -> None:
    antwort = JSONResponse({"ok": False, "rueckfrage": True, "final_text": "Welche?", "trace_id": "tr_spur_3"})
    client, recorder = _client(monkeypatch, tmp_path, antwort)
    client.post("/api/multihop/execute", json={"intent": SATZ})
    assert recorder.get_by_trace("tr_spur_3")["routed_via"] == "rueckfrage"


def test_ohne_recorder_oder_trace_id_passiert_nichts(monkeypatch, tmp_path) -> None:
    antwort = JSONResponse({"ok": True, "final_text": "x"})
    client, recorder = _client(monkeypatch, tmp_path, antwort)
    assert client.post("/api/multihop/execute", json={"intent": SATZ}).json()["final_text"] == "x"
    assert not (tmp_path / "multihop_history.jsonl").exists()


def test_record_spur_schreibt_nur_einmal(tmp_path) -> None:
    recorder = PlanRecorder(path=tmp_path / "h.jsonl")
    assert recorder.record_spur("tr_x", "ideen", True) is True
    assert recorder.record_spur("tr_x", "ideen", True) is False
    (zeile,) = (tmp_path / "h.jsonl").read_text(encoding="utf-8").splitlines()
    assert set(json.loads(zeile)) == {"trace_id", "intent", "routed_via", "ok", "ts", "stages", "spur"}
    assert json.loads(zeile)["intent"] == ""
