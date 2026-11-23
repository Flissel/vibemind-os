"""White Paper nie direkt: Rückfrage mit Umfang, "Ja, erstellen" binnen 2 Minuten, Schlüssel.

Nur Attrappen: keine Datenbank, kein Agent, kein Modell. Namen erfunden.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import agent_auftraege as aa  # noqa: E402
from core import ideen_dokument as dok  # noqa: E402
from core import whitepaper_bestaetigung as wp  # noqa: E402
from web.routers.introspection import router  # noqa: E402

BUBBLE = {"id": "b0b1e001", "title": "Stadtfest"}
KONTEXT = {"space": "ideas", "bubble": dict(BUBBLE)}


def _karte(i, titel, text="", x=0, y=0):
    return {"id": i, "title": titel, "content": text, "x": x, "y": y, "node_type": "idea",
            "linked_idea_id": BUBBLE["id"]}


KARTEN = [
    _karte("a0000001", "Überblick", "Ein Fest für die ganze Straße.", 0, 0),
    _karte("a0000002", "Bühnenplan", "Zwei Bühnen ab 16 Uhr.", -200, 200),
    _karte("a0000003", "Lichtanlage", "Leihgerät vom Verein.", -200, 400),
    _karte("a0000004", "Sponsoren", "Drei Zusagen.", 200, 200),
    _karte("a0000006", "Strom", "Strom", 200, 400),                       # nur Titel = ohne Inhalt
]
KANTEN = [
    {"from_node_id": "a0000001", "to_node_id": "a0000002", "edge_type": "parent_child"},
    {"from_node_id": "a0000002", "to_node_id": "a0000003", "edge_type": "parent_child"},
    {"from_node_id": "a0000001", "to_node_id": "a0000004", "edge_type": "parent_child"},
    {"from_node_id": "a0000004", "to_node_id": "a0000006", "edge_type": "related"},
]


class Lesen(dok.Lesen):
    def bubble(self, kennung):
        return dict(BUBBLE) if kennung == BUBBLE["id"] else None

    def bubbles_mit_titel(self, titel):
        return [dict(BUBBLE)] if titel.casefold() == BUBBLE["title"].casefold() else []

    def karten_der_bubble(self, kennung):
        return [dict(k) for k in KARTEN]

    def karten(self, kennungen):
        return [dict(k) for k in KARTEN if k["id"] in kennungen]

    def kanten(self, kennungen):
        return list(KANTEN)


@pytest.fixture(autouse=True)
def _sauber(monkeypatch):
    wp.verwerfen()
    wp._schluessel.clear()
    monkeypatch.setattr(dok, "DatenbankLesen", Lesen)
    monkeypatch.delenv("IDEEN_WHITEPAPER_MAX_LAEUFE", raising=False)
    yield
    wp.verwerfen()


# ── Umfang ohne Start ─────────────────────────────────────────────────────

def test_frage_nennt_laeufe_karten_abschnitte_und_leere() -> None:
    satz, ok = dok.white_paper_frage({"bubble_id": BUBBLE["id"]}, lesen=Lesen())
    assert ok is True
    assert satz == ("Das White Paper für „Stadtfest“ braucht einen Lauf (4 lesbare Karten in einem Abschnitt, "
                    "1 Karte ohne Inhalt wird nur genannt). Sag „Ja, erstellen“, dann beginne ich.")


def test_frage_mit_mehreren_laeufen(monkeypatch) -> None:
    monkeypatch.setattr(dok, "KARTEN_JE_TEIL", 1)
    satz, ok = dok.white_paper_frage({"bubble_id": BUBBLE["id"], "max_laeufe": 5}, lesen=Lesen())
    assert ok is True and satz.startswith("Das White Paper für „Stadtfest“ braucht etwa 3 Läufe "
                                          "(4 lesbare Karten in 3 Abschnitten, 1 Karte ohne Inhalt")


def test_ueber_der_grenze_nur_mit_bis_zu(monkeypatch) -> None:
    monkeypatch.setattr(dok, "KARTEN_JE_TEIL", 1)
    monkeypatch.setenv("IDEEN_WHITEPAPER_MAX_LAEUFE", "2")
    satz, ok = dok.white_paper_frage({"bubble_id": BUBBLE["id"]}, lesen=Lesen())
    assert ok is False and "mit bis zu 3 Läufen" in satz


# ── Bestätigung ───────────────────────────────────────────────────────────

def test_ja_erstellen_nur_nach_genau_dieser_frage(monkeypatch) -> None:
    assert wp.satz_pruefen("Ja, erstellen") == {"satz": wp.OHNE_FRAGE}
    wp.fragen({"bubble_id": "b0b1e001"})
    assert wp.satz_pruefen("Zeig die Notizen dieser Bubble") is None       # anderer Satz verwirft
    assert wp.satz_pruefen("Ja, erstellen") == {"satz": wp.OHNE_FRAGE}
    wp.fragen({"bubble_id": "b0b1e001"})
    jetzt = wp._jetzt()
    monkeypatch.setattr(wp, "_jetzt", lambda: jetzt + wp.GILT_S + 1)
    assert wp.satz_pruefen("ja erstellen") == {"satz": wp.OHNE_FRAGE}       # nach 2 Minuten verfallen


@pytest.mark.parametrize("satz", ["Ja, erstellen", "ja erstellen", "Ja, erstelle es", "Ja erstellen!"])
def test_bestaetigung_gibt_einen_schluessel_genau_einmal(satz) -> None:
    wp.fragen({"bubble_id": "b0b1e001", "max_laeufe": 20})
    antwort = wp.satz_pruefen(satz)["argumente"]
    assert antwort["bubble_id"] == "b0b1e001" and antwort["max_laeufe"] == 20
    assert wp.einloesen(antwort["bestaetigung"]) is True
    assert wp.einloesen(antwort["bestaetigung"]) is False


@pytest.mark.parametrize("satz", ["Ja, zurück", "Ja", "Erstelle ein White Paper", "ja, erstellen und dann löschen"])
def test_andere_saetze_sind_keine_bestaetigung(satz) -> None:
    wp.fragen({"bubble_id": "b0b1e001"})
    assert wp.satz_pruefen(satz) is None


def test_plan_ausfuehrer_startet_white_paper_ohne_schluessel_nicht(monkeypatch) -> None:
    from core.ideen_abkuerzung import fehlersatz
    from core.plan_executor import PlanExecutor
    from core.plan_schema import HopSpec

    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", True)
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: pytest.fail("ohne Bestätigung kein Auftrag"))
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *a: [])
    for arg in ({"bubble_id": BUBBLE["id"]}, {"bubble_id": BUBBLE["id"], "bestaetigung": "erfunden"}):
        hop = HopSpec(step_id="s1", description="x", capability="idea_whitepaper", arg_template=json.dumps(arg))
        ergebnis = PlanExecutor()._exec_hop(hop, {}, plan_ctx={"plan_id": "p", "plan_intent": "x",
                                                               "antwortkanal": {"art": "api"}})
        assert ergebnis.ok is False
        assert fehlersatz(ergebnis.error, "idea_whitepaper").startswith("Ein White Paper starte ich nur nach Rückfrage")


# ── Router ────────────────────────────────────────────────────────────────

class _Recorder:
    def get(self, *_a):
        return None

    def record(self, *a, **k):
        pass

    def __getattr__(self, _name):
        return lambda *a, **k: None


class _Ausfuehrer:
    recorder = _Recorder()

    def __init__(self):
        self.plaene = []

    def execute(self, plan, **kw):
        self.plaene.append(plan)
        return {"ok": False, "pending": True, "plan": plan.to_dict(),
                "executed": {"s1": {"ok": False, "pending": True, "result": None, "error": None}},
                "state": {}, "elapsed_s": 0.01, "replans": 0}


class _Planer:
    aufrufe = 0

    async def aplan(self, intent, context=None):
        _Planer.aufrufe += 1
        raise AssertionError("kein Planer")


def _client(monkeypatch):
    import core.difficulty_router as dr

    class _K:
        def classify(self, intent):
            return {"level": "medium", "score": 0.9, "method": "qwen-cosine", "reason": "x"}

    monkeypatch.setattr(dr, "get_router", lambda: _K())
    monkeypatch.setenv("SOM_NOPLAN_FALLBACK", "0")
    monkeypatch.setenv("MULTI_VERB_ROUTING", "0")
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = _Ausfuehrer()
    app.state.multihop_planner = _Planer()
    app.state.final_synthesizer = None
    return TestClient(app), app.state.plan_executor


def _satz(client, text, kontext=KONTEXT):
    return client.post("/api/multihop/execute", json={"intent": text, "context": kontext}).json()


def test_router_fragt_zuerst_und_startet_erst_auf_ja(monkeypatch) -> None:
    client, ausfuehrer = _client(monkeypatch)
    body = _satz(client, "Erstelle ein White Paper aus dieser Bubble")
    assert ausfuehrer.plaene == []
    assert body["rueckfrage"] is True and body["final_text"].startswith("Das White Paper für „Stadtfest“ braucht")
    _satz(client, "Ja, erstellen")
    (plan,) = ausfuehrer.plaene
    assert [h.capability for h in plan.hops] == ["idea_whitepaper"]
    arg = json.loads(plan.hops[0].arg_template)
    assert arg["bubble_id"] == BUBBLE["id"] and wp.einloesen(arg["bestaetigung"])
    body = _satz(client, "Ja, erstellen")                                    # kein zweiter Start
    assert len(ausfuehrer.plaene) == 1 and body["final_text"] == wp.OHNE_FRAGE


def test_router_anderer_satz_dazwischen_verwirft_die_frage(monkeypatch) -> None:
    client, ausfuehrer = _client(monkeypatch)
    _satz(client, "Erstelle ein White Paper aus dieser Bubble")
    _satz(client, "Fasse diese Bubble zusammen")                             # startet direkt (ein Lauf)
    body = _satz(client, "Ja, erstellen")
    assert [h.capability for p in ausfuehrer.plaene for h in p.hops] == ["idea_summarize"]
    assert body["final_text"] == wp.OHNE_FRAGE


def test_router_ueber_der_grenze_kein_ja(monkeypatch) -> None:
    monkeypatch.setattr(dok, "KARTEN_JE_TEIL", 1)
    monkeypatch.setenv("IDEEN_WHITEPAPER_MAX_LAEUFE", "2")
    client, ausfuehrer = _client(monkeypatch)
    body = _satz(client, "Erstelle ein White Paper aus dieser Bubble")
    assert "mit bis zu 3 Läufen" in body["final_text"]
    _satz(client, "Ja, erstellen")
    assert ausfuehrer.plaene == []
    body = _satz(client, "Erstelle ein White Paper aus dieser Bubble mit bis zu 3 Läufen")
    assert "Sag „Ja, erstellen“" in body["final_text"]
    _satz(client, "Ja, erstellen")
    (plan,) = ausfuehrer.plaene
    assert json.loads(plan.hops[0].arg_template)["max_laeufe"] == 3
