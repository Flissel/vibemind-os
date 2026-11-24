"""Karte löschen nie direkt: Rückfrage mit Titel, "Ja, löschen" binnen 2 Minuten, Schlüssel.

Prüflauf 24.11.2026: "Lösche die Karte X" bei offener Bubble fiel an den Planer und scheiterte
nach 15 s. Nur Attrappen: keine Datenbank, kein Agent, kein Modell. Namen erfunden.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import ideen_abkuerzung as ab  # noqa: E402
from core import ideen_fest_contract as fest  # noqa: E402
from core import loesch_bestaetigung as lb  # noqa: E402
from web.routers.introspection import router  # noqa: E402

BUBBLE = {"id": "b0b1e001", "title": "Stadtfest"}
KARTEN = [
    {"id": "a0000001", "title": "Bühnenplan (Entwurf)"},
    {"id": "a0000002", "title": "Sponsoren"},
    {"id": "a0000003", "title": "Druckerei"},
    {"id": "a0000004", "title": "Druckerei"},
]
KONTEXT = {"space": "ideas", "bubble": dict(BUBBLE), "cards": [dict(k) for k in KARTEN], "cards_total": 4}
FRAGE = ("Soll ich die Karte „Bühnenplan (Entwurf)“ löschen? Ihre Verbindungen gehen mit. "
         "Mit „Zurück“ holst du sie wieder. Sag „Ja, löschen“.")


@pytest.fixture(autouse=True)
def _sauber():
    lb.verwerfen()
    lb._schluessel.clear()
    ab._OFFEN.clear()
    yield
    lb.verwerfen()
    ab._OFFEN.clear()


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
        geloescht = [{"id": json.loads(plan.hops[0].arg_template).get("id"), "title": "x"}]
        return {"ok": True, "plan": plan.to_dict(),
                "executed": {"s1": {"ok": True, "result": {"content": [{"type": "text",
                                                                        "text": json.dumps(geloescht)}]},
                                    "error": None, "rendered_arg": plan.hops[0].arg_template}},
                "state": {}, "elapsed_s": 0.01, "replans": 0}


class _Planer:
    async def aplan(self, intent, context=None):
        raise AssertionError("kein Planer")


def _client(monkeypatch, muster=None, planer=None):
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
    app.state.multihop_planner = planer or _Planer()
    app.state.final_synthesizer = None
    if muster is not None:
        app.state.capability_router = muster
    return TestClient(app), app.state.plan_executor


def _satz(client, text, kontext=KONTEXT):
    return client.post("/api/multihop/execute", json={"intent": text, "context": kontext}).json()


@pytest.mark.parametrize("satz", ["Lösche die Karte Bühnenplan (Entwurf)", "Lösche die Idee Bühnenplan (Entwurf)",
                                  "lösche die Notiz „Bühnenplan (Entwurf)“", "Entferne die Karte Bühnenplan (Entwurf)."])
def test_router_fragt_zuerst_und_loescht_erst_auf_ja(monkeypatch, satz) -> None:
    client, ausfuehrer = _client(monkeypatch)
    body = _satz(client, satz)
    assert ausfuehrer.plaene == []
    assert body["rueckfrage"] is True and body["final_text"] == FRAGE
    body = _satz(client, "Ja, löschen")
    (plan,) = ausfuehrer.plaene
    assert [h.capability for h in plan.hops] == ["idea.delete"]
    arg = json.loads(plan.hops[0].arg_template)
    assert arg["id"] == "a0000001"
    assert lb.einloesen(arg["bestaetigung"], "idea.delete", "a0000001")
    assert body["ok"] is True
    assert body["final_text"] == ("Die Karte „Bühnenplan (Entwurf)“ ist gelöscht, mit ihren Verbindungen. "
                                  "Mit „Zurück“ holst du sie wieder.")
    assert body["ziel"] == {"space": "ideas", "bubble_id": BUBBLE["id"], "node_id": None}
    body = _satz(client, "Ja, löschen")                                        # kein zweites Löschen
    assert len(ausfuehrer.plaene) == 1 and body["final_text"] == lb.OHNE_FRAGE


def test_router_mehrere_treffer_erst_wahl_dann_frage(monkeypatch) -> None:
    client, ausfuehrer = _client(monkeypatch)
    body = _satz(client, "Lösche die Karte Druckerei")
    assert body["rueckfrage"] is True and "2 Notizen mit dem Titel „Druckerei“" in body["final_text"]
    body = _satz(client, "die zweite")
    assert ausfuehrer.plaene == [] and body["final_text"].startswith("Soll ich die Karte „Druckerei“ löschen?")
    _satz(client, "Ja, löschen")
    (plan,) = ausfuehrer.plaene
    assert json.loads(plan.hops[0].arg_template)["id"] == "a0000004"


def test_router_anderer_satz_dazwischen_verwirft_die_frage(monkeypatch) -> None:
    client, ausfuehrer = _client(monkeypatch)
    _satz(client, "Lösche die Karte Sponsoren")
    _satz(client, "Wie viele Karten hat diese Bubble?")
    body = _satz(client, "Ja, löschen")
    assert not [p for p in ausfuehrer.plaene if p.hops[0].capability == "idea.delete"]
    assert body["final_text"] == lb.OHNE_FRAGE


def test_router_ohne_offene_bubble_nie_loeschen(monkeypatch) -> None:
    client, ausfuehrer = _client(monkeypatch)
    body = _satz(client, "Lösche die Karte Sponsoren", {"space": "ideas", "bubble": None})
    assert body["rueckfrage"] is True and body["final_text"].startswith("Es ist keine Bubble offen.")
    _satz(client, "Ja, löschen")
    assert ausfuehrer.plaene == []


def test_router_unbekannte_karte_ist_rueckfrage(monkeypatch) -> None:
    client, ausfuehrer = _client(monkeypatch)
    body = _satz(client, "Lösche die Karte Kassenbuch")
    assert body["final_text"] == "Eine Notiz „Kassenbuch“ finde ich in dieser Bubble nicht."
    assert ausfuehrer.plaene == []


# ── L2: offene Bubble löschen, Rückfrage mit der Zahl der Karten ─────────

BUBBLE_FRAGE = ("Soll ich die Bubble „Stadtfest“ löschen? Sie hat 4 Karten; die bleiben gespeichert, gehören "
                "dann aber zu keiner Bubble mehr. Mit „Zurück“ holst du alles wieder. Sag „Ja, löschen“.")


@pytest.mark.parametrize("satz", ["Lösche diese Bubble", "Lösche die Bubble", "lösche die Bubble Stadtfest",
                                  "Entferne die Bubble „Stadtfest“."])
def test_router_bubble_erst_frage_mit_kartenzahl_dann_ja(monkeypatch, satz) -> None:
    client, ausfuehrer = _client(monkeypatch)
    body = _satz(client, satz)
    assert ausfuehrer.plaene == [] and body["rueckfrage"] is True and body["final_text"] == BUBBLE_FRAGE
    body = _satz(client, "Ja, löschen")
    (plan,) = ausfuehrer.plaene
    assert [h.capability for h in plan.hops] == ["bubble.delete"]
    arg = json.loads(plan.hops[0].arg_template)
    assert arg["id"] == BUBBLE["id"] and lb.einloesen(arg["bestaetigung"], "bubble.delete", BUBBLE["id"])
    assert body["final_text"] == ("Die Bubble „Stadtfest“ ist gelöscht. Mit „Zurück“ holst du sie wieder, "
                                  "mit allen Karten.")
    assert body.get("ziel") is None                                           # kein Sprung in die geloeschte


def test_router_bubble_nur_die_offene(monkeypatch) -> None:
    client, ausfuehrer = _client(monkeypatch)
    body = _satz(client, "Lösche die Bubble Sommerfest")
    assert body["final_text"] == ("Gelöscht wird nur die offene Bubble. Öffne „Sommerfest“ und sag dort "
                                  "„Lösche diese Bubble“.")
    assert _satz(client, "Ja, löschen")["final_text"] == lb.OHNE_FRAGE and ausfuehrer.plaene == []
    body = _satz(client, "Lösche diese Bubble", {"space": "ideas", "bubble": None})
    assert body["rueckfrage"] is True and ausfuehrer.plaene == []


def test_bubble_frage_ohne_zahl_und_mit_einer_karte() -> None:
    assert "Sie hat eine Karte;" in lb.frage_bubble("Stadtfest", 1)
    assert "Sie hat keine Karten." in lb.frage_bubble("Stadtfest", 0)
    assert "Wie viele Karten sie hat, weiß ich gerade nicht." in lb.frage_bubble("Stadtfest", None)


def test_bubble_schluessel_gilt_nicht_fuer_eine_karte() -> None:
    lb.fragen("bubble.delete", {"id": BUBBLE["id"]}, "x")
    schluessel = lb.satz_pruefen("Ja, lösche die Bubble")["argumente"]["bestaetigung"]
    assert lb.einloesen(schluessel, "idea.delete", BUBBLE["id"]) is False


def test_nachpruefung_bubble_weg_und_keine_karte_zeigt_auf_sie() -> None:
    erwartet, _ = fest.nachpruefung("bubble.delete", {"id": BUBBLE["id"]}, _quittung([{"id": BUBBLE["id"]}]))
    assert erwartet == {"art": "bubble_geloescht", "id": BUBBLE["id"]}
    assert fest.pruefe_zustand(erwartet, lambda tabelle, filter: [])[0] is True
    karte_haengt = lambda tabelle, filter: [{"id": "a0000001"}] if tabelle == "canvas_nodes" else []
    assert fest.pruefe_zustand(erwartet, karte_haengt)[0] is False


def test_registry_bindet_bubble_delete_fest_an_den_ideen_server() -> None:
    import yaml
    registry = yaml.safe_load((Path(__file__).resolve().parents[3] / "config" / "space_agent_registry.yml")
                              .read_text(encoding="utf-8"))
    bubbles = registry["spaces"]["bubbles"]
    assert bubbles["events"]["bubble.delete"]["tool"] == "bubble_delete"
    assert bubbles["events"]["bubble.delete"]["execution"] == {"kind": "mcp", "server": "spaces-ideas"}
    assert "bubble_delete" in bubbles["mcp_tools"]["spaces-ideas"]


# ── L3: eine Begründung löschen (rücknehmbar, ohne Rückfrage) ────────────

@pytest.mark.parametrize("satz, argumente, antwort", [
    ("Lösche die Begründung 2", {"bubble_id": BUBBLE["id"], "nummer": 2},
     "Begründung 2 ist gelöscht. Mit „Zurück“ holst du sie wieder."),
    ("Lösche Begründung Nr. 1 von dieser Bubble", {"bubble_id": BUBBLE["id"], "nummer": 1},
     "Begründung 1 ist gelöscht. Mit „Zurück“ holst du sie wieder."),
    ("Lösche die Begründung 1 von der Bubble Stadtfest", {"bubble_id": BUBBLE["id"], "nummer": 1},
     "Begründung 1 ist gelöscht. Mit „Zurück“ holst du sie wieder."),
    ("Entferne die Begründung 3 zu Sponsoren", {"bubble_id": BUBBLE["id"], "nummer": 3, "idea_id": "a0000002"},
     "Begründung 3 zu „Sponsoren“ ist gelöscht. Mit „Zurück“ holst du sie wieder."),
])
def test_router_begruendung_loeschen_fest(monkeypatch, satz, argumente, antwort) -> None:
    client, ausfuehrer = _client(monkeypatch)
    body = _satz(client, satz)
    (plan,) = ausfuehrer.plaene
    assert [h.capability for h in plan.hops] == ["idea.rate_reason_delete"]
    assert json.loads(plan.hops[0].arg_template) == argumente
    assert body["final_text"] == antwort


def test_begruendung_nummer_ausserhalb_ist_rueckfrage(monkeypatch) -> None:
    client, ausfuehrer = _client(monkeypatch)
    body = _satz(client, "Lösche die Begründung 0")
    assert ausfuehrer.plaene == [] and body["final_text"] == "Begründungen zähle ich von 1 bis 50, die neueste zuerst."


def test_nachpruefung_begruendung_weg() -> None:
    argumente = {"bubble_id": BUBBLE["id"], "nummer": 1}
    quittung = _quittung({"bubble_id": BUBBLE["id"], "entfernt": {"id": "e0000001", "text": "x", "karten": []}})
    erwartet, _ = fest.nachpruefung("idea.rate_reason_delete", argumente, quittung)
    assert erwartet == {"art": "eintrag_weg", "bubble_id": BUBBLE["id"], "eintrag_id": "e0000001"}
    bleibt = lambda tabelle, filter: [{"id": BUBBLE["id"], "metadata": {"bewertungen": [{"id": "e0000001"}]}}]
    weg = lambda tabelle, filter: [{"id": BUBBLE["id"], "metadata": {"bewertungen": [{"id": "e0000002"}]}}]
    assert fest.pruefe_zustand(erwartet, bleibt)[0] is False
    assert fest.pruefe_zustand(erwartet, weg)[0] is True
    # Mit Karte: der entfernte Eintrag muss auf sie zeigen, sonst ist die Quittung ungueltig.
    assert fest.nachpruefung("idea.rate_reason_delete", {**argumente, "idea_id": "a0000002"}, quittung)[0] is None


# ── L4: offene Bubble zählt als genannt, nie an den Planer ───────────────

@pytest.fixture(scope="module")
def muster_router():
    """Der echte Faehigkeiten-Router, nur Muster (kein Einbetter, kein Modell)."""
    from core.capability_router import CapabilityRouter

    return CapabilityRouter(Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml")


class _ZaehlPlaner:
    def __init__(self):
        self.aufrufe = 0

    async def aplan(self, intent, context=None):
        self.aufrufe += 1
        raise RuntimeError("Planer im Test")


@pytest.mark.parametrize("satz, faehigkeit", [
    ("Erkläre die Idee Sponsoren", "idea_explain"),
    ("Erweitere die Idee Sponsoren", "idea_expand"),
    ("Klassifiziere die Idee Sponsoren", "idea_classify"),
    ("remove the idea Sponsoren", "idea_delete"),
    ("delete idea Sponsoren", "idea_delete"),
])
def test_zustandsabhaengig_bei_offener_bubble_ruhiger_satz_statt_planer(monkeypatch, muster_router,
                                                                       satz, faehigkeit) -> None:
    assert muster_router.route(satz).capability == faehigkeit                  # Vorbedingung: Muster greift
    planer = _ZaehlPlaner()
    client, ausfuehrer = _client(monkeypatch, muster=muster_router, planer=planer)
    body = _satz(client, satz)
    assert planer.aufrufe == 0 and ausfuehrer.plaene == []
    assert body["ok"] is False and body["rueckfrage"] is True and body["space"] == "ideas"
    assert body["final_text"].startswith("Das habe ich nicht verstanden. In Ideen")


def test_ohne_offene_bubble_bleibt_der_alte_weg(monkeypatch, muster_router) -> None:
    import web.routers.introspection as intro

    gesehen = []
    echt = intro._try_capability_shortcut
    monkeypatch.setattr(intro, "_try_capability_shortcut",
                        lambda *a, **k: (gesehen.append(echt(*a, **k)), gesehen[-1])[1])
    client, _ = _client(monkeypatch, muster=muster_router, planer=_ZaehlPlaner())
    _satz(client, "Erkläre die Idee Sponsoren", {"space": "ideas", "bubble": None})
    assert gesehen == [None]                                    # wie bisher: Abkuerzung gibt ab


# ── Bestätigung ───────────────────────────────────────────────────────────

def test_ja_loeschen_nur_nach_genau_dieser_frage(monkeypatch) -> None:
    assert lb.satz_pruefen("Ja, löschen") == {"satz": lb.OHNE_FRAGE}
    lb.fragen("idea.delete", {"id": "a0000002"}, "x")
    jetzt = lb._jetzt()
    monkeypatch.setattr(lb, "_jetzt", lambda: jetzt + lb.GILT_S + 1)
    assert lb.satz_pruefen("ja loeschen") == {"satz": lb.OHNE_FRAGE}          # nach 2 Minuten verfallen


@pytest.mark.parametrize("satz", ["Ja", "Ja, zurück", "Lösche die Karte Sponsoren", "ja, löschen und neu anlegen"])
def test_andere_saetze_sind_keine_bestaetigung(satz) -> None:
    lb.fragen("idea.delete", {"id": "a0000002"}, "x")
    assert lb.satz_pruefen(satz) is None


def test_schluessel_gilt_einmal_und_nur_fuer_diese_karte() -> None:
    lb.fragen("idea.delete", {"id": "a0000002"}, "x")
    schluessel = lb.satz_pruefen("Ja, lösche sie")["argumente"]["bestaetigung"]
    assert lb.einloesen(schluessel, "idea.delete", "a0000003") is False        # andere Karte
    lb.fragen("idea.delete", {"id": "a0000002"}, "x")
    schluessel = lb.satz_pruefen("Ja, löschen")["argumente"]["bestaetigung"]
    assert lb.einloesen(schluessel, "idea.delete", "a0000002") is True
    assert lb.einloesen(schluessel, "idea.delete", "a0000002") is False


# ── Plan-Ausführer: ohne Schlüssel kein Löschen ──────────────────────────

def _hop(arg):
    from core.plan_schema import HopSpec
    return HopSpec(step_id="s1", description="x", capability="idea.delete", arg_template=json.dumps(arg))


def test_plan_ausfuehrer_loescht_ohne_schluessel_nicht(monkeypatch) -> None:
    from core.plan_executor import PlanExecutor

    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *a: [])
    monkeypatch.setattr(fest, "feste_argumente", lambda *a: pytest.fail("ohne Schlüssel kein Aufruf"))
    for arg in ({"id": "a0000002"}, {"id": "a0000002", "bestaetigung": "erfunden"}):
        ergebnis = PlanExecutor()._exec_hop(_hop(arg), {}, plan_ctx={"plan_id": "p", "plan_intent": "x"})
        assert ergebnis.ok is False
        assert ab.fehlersatz(ergebnis.error, "idea.delete") == lb.OHNE_SCHLUESSEL
        assert ab.fehlerart(ergebnis.error) == "hinweis"


def test_plan_ausfuehrer_gibt_den_schluessel_nicht_ans_werkzeug(monkeypatch) -> None:
    from core.plan_executor import PlanExecutor

    gesehen = []
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *a: [])
    monkeypatch.setattr(fest, "feste_argumente", lambda ereignis, arg: (gesehen.append(arg), (None, "halt"))[1])
    lb.fragen("idea.delete", {"id": "a0000002"}, "x")
    arg = lb.satz_pruefen("Ja, löschen")["argumente"]
    ergebnis = PlanExecutor()._exec_hop(_hop(arg), {}, plan_ctx={"plan_id": "p", "plan_intent": "x"})
    assert ergebnis.error == "halt" and gesehen == [{"id": "a0000002"}]


# ── Vertrag und Nachprüfung ───────────────────────────────────────────────

def _quittung(zeilen):
    return {"content": [{"type": "text", "text": json.dumps(zeilen)}]}


def test_vertrag_nimmt_nur_die_kennung() -> None:
    assert fest.feste_argumente("idea.delete", '{"id": "a0000002"}') == ({"id": "a0000002"}, "")
    assert fest.feste_argumente("idea.delete", '{"id": "a0000002", "bestaetigung": "x"}')[0] is None
    assert fest.feste_argumente("idea.delete", '{"title": "Sponsoren"}')[0] is None


def test_nachpruefung_karte_und_verbindungen_weg() -> None:
    erwartet, _ = fest.nachpruefung("idea.delete", {"id": "a0000002", "aenderung_id": "p1"},
                                    _quittung([{"id": "a0000002"}]))
    assert erwartet == {"art": "geloescht", "id": "a0000002"}
    assert fest.nachpruefung("idea.delete", {"id": "a0000002"}, _quittung([]))[0] is None    # gab es nicht
    leer = lambda tabelle, filter: []
    assert fest.pruefe_zustand(erwartet, leer)[0] is True
    kante_bleibt = lambda tabelle, filter: [{"id": "e1"}] if tabelle == "canvas_edges" else []
    assert fest.pruefe_zustand(erwartet, kante_bleibt)[0] is False
    assert fest.pruefe_zustand(erwartet, lambda tabelle, filter: None)[0] is None


def test_registry_bindet_idea_delete_fest_an_den_ideen_server() -> None:
    import yaml
    registry = yaml.safe_load((Path(__file__).resolve().parents[3] / "config" / "space_agent_registry.yml")
                              .read_text(encoding="utf-8"))
    ideas = registry["spaces"]["ideas"]
    assert ideas["events"]["idea.delete"]["tool"] == "idea_delete"
    assert ideas["events"]["idea.delete"]["execution"] == {"kind": "mcp", "server": "spaces-ideas"}
    assert "idea_delete" in ideas["mcp_tools"]["spaces-ideas"]
