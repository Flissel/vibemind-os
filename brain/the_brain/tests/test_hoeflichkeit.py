"""Blosser Gruss, Dank, ja/ok: feste Antwort ohne Modell statt "Das habe ich nicht verstanden".

Lauf 22 der Pruefung. Nur Attrappen, Namen erfunden.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import hoeflichkeit as hf  # noqa: E402
from core import ideen_abkuerzung as ab  # noqa: E402
from core import loesch_bestaetigung as lb  # noqa: E402
from web.routers.introspection import router  # noqa: E402

KONTEXT = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"},
           "cards": [{"id": "a0000002", "title": "Sponsoren"}], "cards_total": 1}


@pytest.fixture(autouse=True)
def _sauber():
    lb.verwerfen()
    ab._OFFEN.clear()
    yield
    lb.verwerfen()
    ab._OFFEN.clear()


class _Recorder:
    def __getattr__(self, _name):
        return lambda *a, **k: None


class _Ausfuehrer:
    recorder = _Recorder()

    def __init__(self):
        self.plaene = []

    def execute(self, plan, **kw):
        self.plaene.append(plan)
        return {"ok": True, "plan": plan.to_dict(), "executed": {}, "state": {}, "elapsed_s": 0.0, "replans": 0}


class _Planer:
    def __init__(self):
        self.aufrufe = 0

    async def aplan(self, intent, context=None):
        self.aufrufe += 1
        raise RuntimeError("Planer im Test")


def _client(monkeypatch):
    import core.difficulty_router as dr

    class _K:
        def classify(self, intent):
            return {"level": "easy", "score": 0.95, "method": "qwen-cosine", "reason": "anker danke"}

    monkeypatch.setattr(dr, "get_router", lambda: _K())
    monkeypatch.setenv("SOM_NOPLAN_FALLBACK", "0")
    monkeypatch.setenv("MULTI_VERB_ROUTING", "0")
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = _Ausfuehrer()
    app.state.multihop_planner = _Planer()
    app.state.final_synthesizer = None
    return TestClient(app), app.state


def _satz(client, text, kontext=KONTEXT):
    return client.post("/api/multihop/execute", json={"intent": text, "context": kontext}).json()


@pytest.mark.parametrize("satz, antwort", [
    ("Hallo", hf.GRUSS), ("hallo!", hf.GRUSS), ("Guten Morgen", hf.GRUSS), ("Hi Claude", hf.GRUSS),
    ("Danke", hf.DANK), ("Danke schön!", hf.DANK), ("Vielen Dank.", hf.DANK), ("thanks", hf.DANK),
    ("ok", hf.JA_OHNE_FRAGE), ("Ja", hf.JA_OHNE_FRAGE), ("Alles klar!", hf.ZUSTIMMUNG), ("okay", hf.JA_OHNE_FRAGE),
    ("nein", hf.NEIN_OHNE_FRAGE), ("Nee.", hf.NEIN_OHNE_FRAGE), ("lieber nicht", hf.NEIN_OHNE_FRAGE),
])
def test_router_feste_hoeflichkeit_ohne_modell(monkeypatch, satz, antwort) -> None:
    client, state = _client(monkeypatch)
    body = _satz(client, satz)
    assert body["ok"] is True and body["final_text"] == antwort and body["space"] == "ideas"
    assert state.plan_executor.plaene == [] and state.multihop_planner.aufrufe == 0
    body = _satz(client, satz, {"space": "clawport"})                         # auch aus anderen Reitern
    assert body["final_text"] == antwort


@pytest.mark.parametrize("satz", ["Danke, und jetzt ordne die Karten", "Hallo, zeig die Notizen dieser Bubble",
                                  "ok ordne die Karten", "Ja, löschen", "Gute Idee"])
def test_mehr_als_hoeflichkeit_ist_keine(satz) -> None:
    assert hf.antwort(satz, offen=False) is None


def test_ok_bei_offener_loeschfrage_ist_kein_alles_klar(monkeypatch) -> None:
    client, state = _client(monkeypatch)
    assert _satz(client, "Lösche die Karte Sponsoren")["rueckfrage"] is True
    body = _satz(client, "ok")
    assert body["final_text"] != hf.ZUSTIMMUNG and state.plan_executor.plaene == []
    assert _satz(client, "Ja, löschen")["final_text"] == lb.OHNE_FRAGE      # "ok" hat die Frage verworfen


def test_danke_bei_offener_loeschfrage_verwirft_sie(monkeypatch) -> None:
    client, state = _client(monkeypatch)
    _satz(client, "Lösche die Karte Sponsoren")
    assert _satz(client, "Danke")["final_text"] == hf.DANK
    assert _satz(client, "Ja, löschen")["final_text"] == lb.OHNE_FRAGE
    assert state.plan_executor.plaene == []


def test_offen_liest_nur() -> None:
    lb.fragen("idea.delete", {"id": "a0000002"}, "x")
    assert hf.etwas_offen() is True
    assert hf.etwas_offen() is True                                           # nichts verworfen
    lb.verwerfen()
    assert hf.etwas_offen() is False


def test_nein_bei_offener_loeschfrage_schliesst_sie_fest(monkeypatch) -> None:
    client, state = _client(monkeypatch)
    assert _satz(client, "Lösche die Karte Sponsoren")["rueckfrage"] is True
    body = _satz(client, "nein")
    assert body["final_text"] == hf.NEIN_OFFEN and body.get("weg") == "hoeflichkeit"
    assert _satz(client, "Ja, löschen")["final_text"] == lb.OHNE_FRAGE
    assert state.plan_executor.plaene == []


def test_nein_bei_offenem_befoerdern_sagt_es_genau(monkeypatch) -> None:
    from core import befoerdern_bestaetigung as bf

    client, state = _client(monkeypatch)
    bf.fragen({"bubble_id": "b0b1e001"}, "")
    try:
        body = _satz(client, "Nein")
    finally:
        bf.verwerfen()
    assert body["ok"] is True and body["weg"] == "abgelehnt" and body["final_text"] == bf.ABGELEHNT
    assert body["executed"] == {} and state.plan_executor.plaene == []
    assert _satz(client, "Ja")["final_text"] == hf.JA_OHNE_FRAGE


@pytest.mark.parametrize("satz", ["Gut", "passt", "super"])
def test_lob_bleibt_alles_klar(satz) -> None:
    assert hf.antwort(satz, offen=False) == hf.ZUSTIMMUNG
