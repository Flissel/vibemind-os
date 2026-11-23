"""Verstehen vor Planen: Schwelle, Space-Saetze, feste Lesesaetze, Space-Wache, Auffangnetz.

Alles mit Attrappen: kein Modell, kein Planer-Lauf, kein Netz. Namen erfunden.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import verstehen as v  # noqa: E402
from core.plan_schema import HopSpec, Plan  # noqa: E402
from web.routers.introspection import router  # noqa: E402


# ── Bausteine ──────────────────────────────────────────────────────────────

def test_space_aus_kontext_kennt_app_kennungen_und_aliase() -> None:
    assert v.space_aus_kontext({"space": "swedesign"}) == "swedesign"
    assert v.space_aus_kontext({"space": "Dashboard"}) == "clawport"
    assert v.space_aus_kontext({"space": "irgendwas"}) is None
    assert v.space_aus_kontext({"eingabe": "getippt"}) is None
    assert v.space_aus_kontext(None) is None


def test_nicht_verstanden_nennt_beispiele_des_space() -> None:
    satz = v.nicht_verstanden("ideas")
    assert satz.startswith("Das habe ich nicht verstanden. In Ideen kann ich z. B. „")
    assert satz.count("„") == 3
    assert v.nicht_verstanden(None) == "Das habe ich nicht verstanden."
    assert "noch keinen Weg" in v.nicht_verstanden("swedesign")


@pytest.mark.parametrize("cls,erwartet", [
    ({"level": "medium", "score": 0.42, "method": "qwen-cosine"}, True),
    ({"level": "medium", "score": 0.72, "method": "qwen-cosine"}, False),
    ({"level": "medium", "score": 0.40, "method": "qwen-cosine-lowconf"}, True),
    ({"level": "easy", "score": 0.30, "method": "qwen-cosine"}, False),
    ({"level": "medium", "score": 0.1, "method": "heuristic"}, False),
    ({"level": "meta", "score": 0.0, "method": "meta-filter"}, False),
])
def test_schwelle(cls, erwartet) -> None:
    assert v.unter_schwelle(cls) is erwartet


def test_schwelle_ist_einstellbar_und_abschaltbar(monkeypatch) -> None:
    cls = {"level": "medium", "score": 0.58, "method": "qwen-cosine"}
    assert v.unter_schwelle(cls)
    monkeypatch.setenv("VERSTEHEN_MIN_COS", "0.5")
    assert not v.unter_schwelle(cls)
    monkeypatch.setenv("VERSTEHEN_MIN_COS", "0.7")
    monkeypatch.setenv("VERSTEHEN_ROUTING", "0")
    assert not v.unter_schwelle(cls)


@pytest.mark.parametrize("satz,space", [
    ("Zeig meine Aufträge", "orders"), ("Zeige alle Aufträge", None),
    ("Welche geplanten Aufgaben gibt es?", "clawport"), ("zeig mir die geplanten Aufgaben", "orders"),
])
def test_feste_lesefrage_bindet_schedule_list(satz, space) -> None:
    assert v.feste_lesefrage(satz, space) == {"capability": "schedule_list"}


def test_feste_lesefrage_nicht_im_ideen_space_und_nicht_fremd() -> None:
    assert v.feste_lesefrage("Zeig meine Aufträge", "ideas") is None
    assert v.feste_lesefrage("Zeig meine Aufträge", "video") is None
    assert v.feste_lesefrage("Wie ist der Stand?", "orders") is None


def test_videoprojekte_ehrlich_noch_nicht() -> None:
    antwort = v.feste_lesefrage("Welche Videoprojekte gibt es?", "video")
    assert antwort and "noch nicht" in antwort["antwort"]


def test_schedule_list_satz() -> None:
    assert v.antwort_schedule_list({"success": True, "tasks": [], "count": 0}) == (
        "Es sind keine geplanten Aufgaben eingetragen.")
    zwei = {"success": True, "count": 2, "tasks": [
        {"title": "Bühnenplan prüfen", "status": "active"}, {"title": "Sponsoren anrufen", "status": "paused"}]}
    assert v.antwort_schedule_list(zwei) == (
        "2 geplante Aufgaben: „Bühnenplan prüfen“ (aktiv), „Sponsoren anrufen“ (pausiert).")
    viele = {"success": True, "tasks": [{"title": f"T{i}", "status": "active"} for i in range(12)]}
    assert v.antwort_schedule_list(viele).endswith("und 2 weitere.")
    assert v.antwort_schedule_list({"success": False}) is None
    assert v.antwort_schedule_list("{'success': True}") is None


def test_space_wache() -> None:
    assert v.fremder_plan(["bubble_stats"], "swedesign")
    assert v.fremder_plan(["idea_list", "bubble_list"], "video")
    assert not v.fremder_plan(["idea_list"], "ideas")
    assert not v.fremder_plan(["bubble_stats", "browser_open"], "swedesign")  # allgemein dabei
    assert not v.fremder_plan(["schedule_list"], "clawport")
    assert not v.fremder_plan(["bubble_stats"], None)
    assert not v.fremder_plan([], "video")


@pytest.mark.parametrize("text", [
    "Multi-hop plan: 0/1 steps succeeded for intent: x",
    "{'success': True, 'tasks': [], 'count': 0}",
    '{"ok": false}', "Timeout waiting for chat_response", "video providers unavailable",
    "Traceback (most recent call last):", "", None,
])
def test_rohtext_erkannt(text) -> None:
    assert v.rohtext(text)


@pytest.mark.parametrize("text", ["Es sind keine geplanten Aufgaben eingetragen.",
                                  "Der Bühnenplan steht, die Sponsoren zahlen bis März."])
def test_satz_ist_kein_rohtext(text) -> None:
    assert not v.rohtext(text)


@pytest.mark.parametrize("ergebnis,leer", [
    (None, True), ("", True), ({}, True), ({"success": False}, True), ({"error": "x"}, True),
    ("Timeout after 30s", True), ({"isError": True, "content": []}, True),
    ({"success": True, "tasks": []}, False), ([], False), ("Fenster: Editor", False),
])
def test_ohne_antwort(ergebnis, leer) -> None:
    assert v.ohne_antwort(ergebnis) is leer


# ── Anker im Grund ─────────────────────────────────────────────────────────

class _Einbetter:
    """Attrappe: Vektor je Text aus festen Achsen, kein Modell."""

    def __init__(self, achsen):
        self.achsen = achsen

    def encode(self, text):
        return np.asarray(self.achsen.get(text, [0.0, 0.0, 1.0]), dtype=np.float32)


def test_grund_nennt_den_wirklich_naechsten_anker() -> None:
    from core.difficulty_router import DifficultyRouter

    r = DifficultyRouter.__new__(DifficultyRouter)
    r._anchors = {"easy": ["hallo"], "medium": ["öffne example.com im browser", "zeig den kalender"],
                  "hard": [], "insane": []}
    r._anchor_mat = None
    r._embedder = _Einbetter({"hallo": [1, 0, 0], "öffne example.com im browser": [0, 1, 0],
                              "zeig den kalender": [0, 0.6, 0.8], "was steht im kalender": [0, 0.5, 0.86]})
    r._embedder_tried = True
    r._complexity_fn = None
    cls = r._classify_base("was steht im kalender")
    assert cls["level"] == "medium" and cls["anchor"] == "zeig den kalender"
    assert "zeig den kalender" in cls["reason"]


# ── Ende-zu-Ende am echten Router, Attrappen fuer Planer und Ausfuehrer ────

class _Recorder:
    def attach_final(self, *a, **k):
        pass

    def record_lite(self, *a, **k):
        pass

    def append_stage(self, *a, **k):
        pass


class _Ausfuehrer:
    recorder = _Recorder()

    def __init__(self, ergebnis=None, ok=True):
        self.plaene = []
        self.ergebnis = ergebnis
        self.ok = ok

    def execute(self, plan, **kw):
        self.plaene.append(plan)
        return {"ok": self.ok, "plan": plan.to_dict(),
                "executed": {"s1": {"ok": self.ok, "result": self.ergebnis, "error": None if self.ok else "boom"}},
                "state": {}, "elapsed_s": 0.01, "replans": 0}


class _Planer:
    def __init__(self, faehigkeit="bubble_stats"):
        self.aufrufe = 0
        self.faehigkeit = faehigkeit

    async def aplan(self, intent, context=None):
        self.aufrufe += 1
        return Plan(plan_id="planer_1", intent=intent, rationale="", final_synthesis_prompt="",
                    estimated_cost_usd=0.0,
                    hops=[HopSpec(step_id="s1", description="x", capability=self.faehigkeit, arg_template="")])


class _Synth:
    def __init__(self, text):
        self.text = text

    async def asynthesize(self, **kw):
        return self.text


class _Klassierer:
    def __init__(self, cls):
        self.cls = cls

    def classify(self, intent):
        return dict(self.cls)


def _client(monkeypatch, cls, ausfuehrer=None, planer=None, synth=None):
    import core.difficulty_router as dr

    monkeypatch.setattr(dr, "get_router", lambda: _Klassierer(cls))
    monkeypatch.setenv("SOM_NOPLAN_FALLBACK", "0")
    monkeypatch.setenv("MULTI_VERB_ROUTING", "0")
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = ausfuehrer or _Ausfuehrer()
    app.state.multihop_planner = planer or _Planer()
    app.state.final_synthesizer = synth
    return TestClient(app), app.state


SCHWACH = {"level": "medium", "score": 0.44, "method": "qwen-cosine", "reason": "x"}
STARK = {"level": "medium", "score": 0.83, "method": "qwen-cosine", "reason": "x"}


def test_unter_schwelle_kein_planer_und_satz_mit_space(monkeypatch) -> None:
    client, state = _client(monkeypatch, SCHWACH)
    body = client.post("/api/multihop/execute",
                       json={"intent": "Wie läuft es so bei den Sponsoren", "context": {"space": "clawport"}}).json()
    assert state.multihop_planner.aufrufe == 0 and state.plan_executor.plaene == []
    assert body["final_text"].startswith("Das habe ich nicht verstanden. In Dashboard kann ich z. B. „")
    assert body["ok"] is False and body["rueckfrage"] is True and body["verstanden"] is False


def test_unter_schwelle_ohne_space(monkeypatch) -> None:
    client, state = _client(monkeypatch, SCHWACH)
    body = client.post("/api/multihop/execute", json={"intent": "Mach mal was mit dem Ding"}).json()
    assert state.multihop_planner.aufrufe == 0
    assert body["final_text"] == "Das habe ich nicht verstanden."


def test_space_ohne_weg_ehrlich_ohne_klassifikation(monkeypatch) -> None:
    client, state = _client(monkeypatch, STARK)
    body = client.post("/api/multihop/execute",
                       json={"intent": "Wie ist der Stand im SWE Design?", "context": {"space": "swedesign"}}).json()
    assert state.multihop_planner.aufrufe == 0 and state.plan_executor.plaene == []
    assert body["final_text"].startswith("Das kann ich in SWE Design noch nicht")


def test_fremder_plan_laeuft_nicht(monkeypatch) -> None:
    client, state = _client(monkeypatch, STARK, planer=_Planer("bubble_stats"))
    body = client.post("/api/multihop/execute",
                       json={"intent": "Welche Aufgaben laufen gerade?", "context": {"space": "orders"}}).json()
    assert state.multihop_planner.aufrufe == 1 and state.plan_executor.plaene == []
    assert body["final_text"].startswith("Das gehört nicht zu Aufträge")


def test_fester_lesesatz_auftraege_deutsch_statt_dict(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"success": True, "tasks": [], "count": 0})
    client, state = _client(monkeypatch, SCHWACH, ausfuehrer=ausfuehrer)
    body = client.post("/api/multihop/execute",
                       json={"intent": "Zeig meine Aufträge", "context": {"space": "orders"}}).json()
    assert state.multihop_planner.aufrufe == 0
    assert [h.capability for h in ausfuehrer.plaene[0].hops] == ["schedule_list"]
    assert body["final_text"] == "Es sind keine geplanten Aufgaben eingetragen."
    assert body["ok"] is True


def test_fester_lesesatz_fehler_ist_kein_haken(monkeypatch) -> None:
    client, _ = _client(monkeypatch, SCHWACH, ausfuehrer=_Ausfuehrer(None, ok=False))
    body = client.post("/api/multihop/execute", json={"intent": "Zeig meine Aufträge"}).json()
    assert body["ok"] is False and body["final_text"] == v.FEHLERSATZ


def test_auffangnetz_ersetzt_rohtext(monkeypatch) -> None:
    client, _ = _client(monkeypatch, STARK, planer=_Planer("browser_open"),
                        ausfuehrer=_Ausfuehrer(None, ok=False),
                        synth=_Synth("Multi-hop plan: 0/1 steps succeeded for intent: x"))
    body = client.post("/api/multihop/execute", json={"intent": "Öffne die Seite"}).json()
    assert body["ok"] is False and body["final_text"] == v.FEHLERSATZ
    assert "Multi-hop plan" in body["fehler"]


def test_kein_haken_ohne_ergebnis(monkeypatch) -> None:
    client, _ = _client(monkeypatch, STARK, planer=_Planer("browser_open"),
                        ausfuehrer=_Ausfuehrer("Timeout after 30s", ok=True),
                        synth=_Synth("Die Seite ist offen."))
    body = client.post("/api/multihop/execute", json={"intent": "Öffne die Seite"}).json()
    assert body["ok"] is False


def test_starker_satz_geht_weiter_an_den_planer(monkeypatch) -> None:
    client, state = _client(monkeypatch, STARK, planer=_Planer("browser_open"),
                            ausfuehrer=_Ausfuehrer({"url": "https://example.com"}),
                            synth=_Synth("Die Seite ist offen."))
    body = client.post("/api/multihop/execute", json={"intent": "Öffne example.com im Browser"}).json()
    assert state.multihop_planner.aufrufe == 1
    assert body["ok"] is True and body["final_text"] == "Die Seite ist offen."


def test_abschalter_laesst_alten_weg(monkeypatch) -> None:
    monkeypatch.setenv("VERSTEHEN_ROUTING", "0")
    client, state = _client(monkeypatch, SCHWACH, planer=_Planer("browser_open"),
                            ausfuehrer=_Ausfuehrer({"url": "x"}), synth=_Synth("Erledigt."))
    client.post("/api/multihop/execute", json={"intent": "Irgendwas", "context": {"space": "swedesign"}})
    assert state.multihop_planner.aufrufe == 1
