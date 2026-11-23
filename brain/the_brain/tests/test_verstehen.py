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
    assert v.nicht_verstanden("swedesign").endswith("Im Bereich „SWE Design“ gibt es dafür noch keinen Weg.")
    assert v.nicht_verstanden("desktop") == "Das habe ich nicht verstanden."  # Weg da, nur kein fester Satz


@pytest.mark.parametrize("satz,space", [
    ("Wie ist der Stand im SWE Design?", "swedesign"), ("Welche Teams laufen gerade in der Agent Farm?", "agentfarm"),
    ("Welche Simulationen gibt es in MiroFish?", "mirofish"), ("Wie viele Kontakte habe ich im Sales?", "sales"),
    ("Wie laufen meine Marketing-Kampagnen?", "marketing"), ("Was ist im Desktop offen?", "desktop"),
    ("Welche geplanten Aufgaben gibt es im Dashboard?", "clawport"), ("Welche Agenten gibt es in Rowboat?", "roarboot"),
    ("Was steht im Ideen-Bereich?", "ideas"),
    ("Ich will Spanisch lernen", None), ("Welche Events stehen als nächstes an?", None), ("Zeig meine Aufträge", None),
    ("Zeig mir meine Bubbles", None), ("Mein Video ist fertig", None),
])
def test_genannter_space(satz, space) -> None:
    assert v.genannter_space(satz) == space


def test_kein_weg_satz() -> None:
    assert v.kein_weg("swedesign") == (
        "Im Bereich „SWE Design“ kann ich das noch nicht; dafür gibt es noch keinen Weg. Geändert habe ich nichts.")


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


def test_geeichte_vorgabe(monkeypatch) -> None:
    monkeypatch.delenv("VERSTEHEN_MIN_COS", raising=False)
    assert v.schwelle() == 0.68
    # hoechster gemessener Unsinn-Satz auf einer Planer-Stufe ("lorem ipsum ..." -> hard, 0,675)
    assert v.unter_schwelle({"level": "hard", "score": 0.675, "method": "qwen-cosine"})
    # echter Satz ohne festen Weg, der weiter zum Planer darf ("Wie ist der Code-Status?", 0,723)
    assert not v.unter_schwelle({"level": "medium", "score": 0.723, "method": "qwen-cosine"})


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


@pytest.mark.parametrize("space", ["ideas", "video", "desktop", "roarboot", None])
def test_feste_lesefrage_gilt_in_jedem_reiter(space) -> None:
    # Nachmessung 23.11.2026: die App schickte aus jedem Reiter 'ideas'; der feste Satz lief
    # dann ueber den Planer (14 s). Feste Saetze gelten unabhaengig von context.space.
    assert v.feste_lesefrage("Welche geplanten Aufgaben gibt es?", space) == {"capability": "schedule_list"}
    assert v.feste_lesefrage("Zeig meine Aufträge", space) == {"capability": "schedule_list"}


def test_feste_lesefrage_nur_am_satzanfang() -> None:
    assert v.feste_lesefrage("Wie ist der Stand?", "orders") is None
    assert v.feste_lesefrage("Halt fest: Zeig meine Aufträge morgen", "ideas") is None


def test_videoprojekte_ehrlich_noch_nicht() -> None:
    antwort = v.feste_lesefrage("Welche Videoprojekte gibt es?", "video")
    assert antwort and "noch nicht" in antwort["antwort"]
    # Im Ideen-Bereich ist das ein Notizsatz, kein "noch nicht".
    assert v.feste_lesefrage("Halt fest: Videoprojekte prüfen", "ideas") is None


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


def test_mehrdeutiger_plan() -> None:
    assert not v.gemischter_plan(["bubble_stats"])                    # eindeutig, auch fremd
    assert not v.gemischter_plan(["idea_list", "bubble_list"])         # ein Space
    assert not v.gemischter_plan(["bubble_stats", "browser_open"])     # allgemein zaehlt nicht
    assert not v.gemischter_plan(["schedule_list", "schedule.cancel"])
    assert not v.gemischter_plan([])
    assert v.gemischter_plan(["video_team_status", "bubble_stats"])
    assert v.gemischter_plan(["rowboat_chat", "schedule_list"])


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


class _MehrPlaner(_Planer):
    def __init__(self, faehigkeiten):
        super().__init__()
        self.faehigkeiten = faehigkeiten

    async def aplan(self, intent, context=None):
        self.aufrufe += 1
        return Plan(plan_id="planer_2", intent=intent, rationale="", final_synthesis_prompt="",
                    estimated_cost_usd=0.0,
                    hops=[HopSpec(step_id=f"s{i}", description="x", capability=c, arg_template="")
                          for i, c in enumerate(self.faehigkeiten, 1)])


@pytest.fixture(scope="module")
def echter_muster_router():
    """Der echte Faehigkeiten-Router, nur Muster (kein Einbetter, kein Modell)."""
    from core.capability_router import CapabilityRouter

    return CapabilityRouter(Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml")


def _client(monkeypatch, cls, ausfuehrer=None, planer=None, synth=None, muster=None):
    import core.difficulty_router as dr

    monkeypatch.setattr(dr, "get_router", lambda: _Klassierer(cls))
    monkeypatch.setenv("SOM_NOPLAN_FALLBACK", "0")
    monkeypatch.setenv("MULTI_VERB_ROUTING", "0")
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = ausfuehrer or _Ausfuehrer()
    app.state.multihop_planner = planer or _Planer()
    app.state.final_synthesizer = synth
    if muster is not None:
        app.state.capability_router = muster
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


NV = "Das habe ich nicht verstanden."
KEIN = "Im Bereich „{}“ gibt es dafür noch keinen Weg."

# Die 16 Saetze aus dem Rundgang 23.11.2026 (Satz, Space der App -> erwarteter Weg). Der
# Klassierer liefert wie gemessen einen schwachen Cosinus; der Muster-Router ist der echte.
RUNDGANG = [
    ("Welche Bubbles gibt es?", "ideas", ("lauf", "bubble_list")),
    ("Wie ist der Code-Status?", "projects", ("nicht-verstanden", NV + " " + KEIN.format("Project Space"))),
    ("Welche Fenster sind gerade auf dem Desktop offen?", "desktop", ("nicht-verstanden", NV)),
    ("Wie ist der Stand im SWE Design?", "swedesign", ("kein-weg", "Im Bereich „SWE Design“ kann ich das noch nicht")),
    ("Welche Agenten gibt es in Rowboat?", "roarboot", ("nicht-verstanden", NV)),
    ("Welche geplanten Aufgaben gibt es im Dashboard?", "clawport", ("lauf", "schedule_list")),
    ("Welche Videoprojekte gibt es?", "video", ("noch-nicht", "Die Liste der Videoprojekte kann ich noch nicht")),
    ("Welche Teams laufen gerade in der Agent Farm?", "agentfarm",
     ("kein-weg", "Im Bereich „Agent Farm“ kann ich das noch nicht")),
    ("Woran denkt das Gehirn gerade?", "thebrain", ("nicht-verstanden", NV + " " + KEIN.format("The Brain"))),
    ("Was steht in meinem Journal?", "flowzen", ("nicht-verstanden", NV)),
    ("Welche Simulationen gibt es in MiroFish?", "mirofish", ("nicht-verstanden", NV)),
    ("Wie viele Kontakte habe ich im Sales?", "sales", ("nicht-verstanden", NV)),
    ("Wie laufen meine Marketing-Kampagnen?", "marketing", ("kein-weg", "Im Bereich „Marketing“ kann ich das noch nicht")),
    ("Welche Kurse gibt es?", "learning", ("nicht-verstanden", NV + " " + KEIN.format("Lernen"))),
    ("Welche Events stehen als nächstes an?", "events", ("nicht-verstanden", NV + " " + KEIN.format("Events"))),
    ("Zeig meine Aufträge", "orders", ("lauf", "schedule_list")),
]


@pytest.mark.parametrize("satz,space,erwartet", RUNDGANG, ids=[r[1] for r in RUNDGANG])
def test_rundgang_saetze(monkeypatch, echter_muster_router, satz, space, erwartet) -> None:
    ausfuehrer = _Ausfuehrer({"success": True, "tasks": [], "count": 0})
    client, state = _client(monkeypatch, SCHWACH, ausfuehrer=ausfuehrer, muster=echter_muster_router)
    body = client.post("/api/multihop/execute", json={"intent": satz, "context": {"space": space}}).json()
    assert state.multihop_planner.aufrufe == 0, "kein Planer-Lauf bei schwachem Cosinus"
    weg, wert = erwartet
    if weg == "lauf":
        assert [h.capability for p in ausfuehrer.plaene for h in p.hops] == [wert]
        assert "Multi-hop plan" not in str(body.get("final_text"))
    else:
        assert ausfuehrer.plaene == [], "kein Aufruf, kein Agent"
        assert body["weg"] == weg
        assert body["final_text"].startswith(wert) if weg != "nicht-verstanden" else body["final_text"] == wert


# ── Sprünge, die heute laufen, bleiben (Rückfall-Schutz) ──────────────────

@pytest.mark.parametrize("space", ["roarboot", "swedesign", "video", None])
def test_bubbles_aus_jedem_reiter_springt_weiter(monkeypatch, echter_muster_router, space) -> None:
    ausfuehrer = _Ausfuehrer({"content": [{"type": "text", "text": "[]"}]})
    client, state = _client(monkeypatch, SCHWACH, ausfuehrer=ausfuehrer, muster=echter_muster_router)
    kontext = {"space": space} if space else {}
    client.post("/api/multihop/execute", json={"intent": "Zeig mir meine Bubbles", "context": kontext})
    assert [h.capability for p in ausfuehrer.plaene for h in p.hops] == ["bubble_list"]
    assert state.multihop_planner.aufrufe == 0


def _bubble_liste(monkeypatch, zeilen):
    import web.routers.introspection as intro

    gesucht = []

    async def suchen(name):
        gesucht.append(name)
        return zeilen

    monkeypatch.setattr(intro, "_bubbles_suchen", suchen)
    return gesucht


def test_bubble_name_im_satz() -> None:
    assert v.bubble_name_im_satz("Halt fest: Preise prüfen in der Bubble Stadtfest") == (
        "Stadtfest", "Halt fest: Preise prüfen")
    assert v.bubble_name_im_satz("Notiz: Bühne bauen in der Bubble „Sommer Fest“.")[0] == "Sommer Fest"
    assert v.bubble_name_im_satz("Halt fest: Preise prüfen") == (None, "Halt fest: Preise prüfen")


def test_halt_fest_aus_video_schreibt_in_die_genannte_bubble(monkeypatch, echter_muster_router) -> None:
    gesucht = _bubble_liste(monkeypatch, [{"id": "b0b1e001", "title": "Stadtfest"},
                                          {"id": "b0b1e002", "title": "Stadtfest Nachlese"}])
    ausfuehrer = _Ausfuehrer({"content": [{"type": "text", "text": '{"id": "a0000009"}'}]})
    client, state = _client(monkeypatch, SCHWACH, ausfuehrer=ausfuehrer, muster=echter_muster_router)
    client.post("/api/multihop/execute", json={"intent": "Halt fest: Preise prüfen in der Bubble Stadtfest",
                                               "context": {"space": "video"}})
    assert gesucht == ["Stadtfest"] and state.multihop_planner.aufrufe == 0
    hop = ausfuehrer.plaene[0].hops[0]
    assert "b0b1e001" in hop.arg_template and "Preise prüfen" in hop.arg_template
    assert "Bubble Stadtfest" not in hop.arg_template


@pytest.mark.parametrize("zeilen,teil", [
    ([{"id": "b0b1e001", "title": "Stadtfest"}, {"id": "b0b1e003", "title": "stadtfest"}], "Es gibt 2 Bubbles"),
    ([], "Ich finde keine Bubble mit dem Titel „Stadtfest“"),
    (None, "kann ich gerade nicht lesen"),
])
def test_halt_fest_aus_video_fragt_bei_unklarer_bubble(monkeypatch, echter_muster_router, zeilen, teil) -> None:
    _bubble_liste(monkeypatch, zeilen)
    ausfuehrer = _Ausfuehrer({"x": 1})
    client, state = _client(monkeypatch, SCHWACH, ausfuehrer=ausfuehrer, muster=echter_muster_router)
    body = client.post("/api/multihop/execute", json={"intent": "Halt fest: Preise prüfen in der Bubble Stadtfest",
                                                      "context": {"space": "video"}}).json()
    assert ausfuehrer.plaene == [] and state.multihop_planner.aufrufe == 0
    assert body["space"] == "ideas" and body["weg"] == "ideen" and teil in body["final_text"]


def test_mehrere_bubbles_die_zweite_waehlt_und_fuehrt_den_satz_aus(monkeypatch, echter_muster_router) -> None:
    v._bubble_offen.clear()
    _bubble_liste(monkeypatch, [{"id": "b0b1e001", "title": "Stadtfest"}, {"id": "b0b1e003", "title": "stadtfest"}])
    ausfuehrer = _Ausfuehrer({"content": [{"type": "text", "text": '{"id": "a0000009"}'}]})
    client, state = _client(monkeypatch, SCHWACH, ausfuehrer=ausfuehrer, muster=echter_muster_router)
    body = client.post("/api/multihop/execute", json={"intent": "Halt fest: Preise prüfen in der Bubble Stadtfest",
                                                      "context": {"space": "video"}}).json()
    assert "1. „Stadtfest“; 2. „stadtfest“" in body["final_text"] and "die erste" in body["final_text"]
    client.post("/api/multihop/execute", json={"intent": "die zweite", "context": {"space": "video"}})
    (plan,) = ausfuehrer.plaene
    assert "b0b1e003" in plan.hops[0].arg_template and "Preise prüfen" in plan.hops[0].arg_template
    assert state.multihop_planner.aufrufe == 0
    # Danach ist nichts mehr offen: "die zweite" fuehrt nicht noch einmal aus.
    client.post("/api/multihop/execute", json={"intent": "die zweite", "context": {"space": "video"}})
    assert len(ausfuehrer.plaene) == 1


def test_mehrere_bubbles_anderer_satz_verwirft_die_frage(monkeypatch, echter_muster_router) -> None:
    v._bubble_offen.clear()
    _bubble_liste(monkeypatch, [{"id": "b0b1e001", "title": "Stadtfest"}, {"id": "b0b1e003", "title": "Stadtfest"}])
    ausfuehrer = _Ausfuehrer({"content": [{"type": "text", "text": "[]"}]})
    client, _ = _client(monkeypatch, SCHWACH, ausfuehrer=ausfuehrer, muster=echter_muster_router)
    client.post("/api/multihop/execute", json={"intent": "Halt fest: Preise prüfen in der Bubble Stadtfest",
                                               "context": {"space": "video"}})
    client.post("/api/multihop/execute", json={"intent": "Zeig mir meine Bubbles", "context": {"space": "video"}})
    client.post("/api/multihop/execute", json={"intent": "die zweite", "context": {"space": "video"}})
    assert [h.capability for p in ausfuehrer.plaene for h in p.hops] == ["bubble_list"]


def test_zurueck_aus_rowboat_greift_nicht_ins_ideen_canvas(monkeypatch, echter_muster_router) -> None:
    ausfuehrer = _Ausfuehrer({"x": 1})
    client, _ = _client(monkeypatch, SCHWACH, ausfuehrer=ausfuehrer, muster=echter_muster_router)
    body = client.post("/api/multihop/execute", json={"intent": "Nimm das zurück", "context": {"space": "roarboot"}}).json()
    assert body.get("space") != "ideas"


def test_screenshot_aus_sales_geht_an_den_desktop(monkeypatch, echter_muster_router) -> None:
    ausfuehrer = _Ausfuehrer({"bild": "ok"})
    client, state = _client(monkeypatch, SCHWACH, ausfuehrer=ausfuehrer, muster=echter_muster_router)
    client.post("/api/multihop/execute", json={"intent": "Mach einen Screenshot", "context": {"space": "sales"}})
    assert [h.capability for p in ausfuehrer.plaene for h in p.hops] == ["desktop_screenshot"]
    assert state.multihop_planner.aufrufe == 0


# ── Regel "Planer nur im Space mit Weg" (unabhaengig vom cos) ─────────────

# So schickt die App den Ideen-Bereich: immer mit Bubble, Karten oder Bubble-Liste.
IDEEN = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"}, "cards": []}


@pytest.mark.parametrize("kontext,erwartet", [
    ({"space": "ideas"}, None),
    ({"space": "ideas", "bubble": None, "cards": None, "bubbles": None}, None),
    ({"space": "ideas", "cards": []}, "ideas"),
    ({"space": "ideas", "bubbles": [{"id": "b0b1e001", "title": "Stadtfest"}]}, "ideas"),
    ({"space": "Ideen", "bubble": {"id": "b0b1e001"}}, "ideas"),
    ({"space": "clawport"}, "clawport"),
])
def test_ideen_kontext_ohne_bubble_gilt_als_unbekannt(kontext, erwartet) -> None:
    assert v.space_aus_kontext(kontext) == erwartet


def test_falscher_ideen_kontext_laesst_den_planer_zu(monkeypatch, echter_muster_router) -> None:
    # Nachmessung: aus jedem Reiter kam context.space='ideas' ohne Bubble; die Regel liess den
    # Planer dann ueberall zu. Ohne Belege gilt der Bereich als unbekannt.
    client, state = _client(monkeypatch, STARK, planer=_Planer("idea_list"), muster=echter_muster_router,
                            ausfuehrer=_Ausfuehrer({"x": 1}))
    body = client.post("/api/multihop/execute", json={"intent": "Welche Notizen zum Thema Bühne habe ich schon",
                                                      "context": {"space": "ideas"}}).json()
    assert state.multihop_planner.aufrufe == 0 and state.plan_executor.plaene == []
    assert body["weg"] == "nicht-verstanden" and body["final_text"] == "Das habe ich nicht verstanden."

@pytest.mark.parametrize("cls", [SCHWACH, STARK], ids=["schwach", "stark"])
def test_im_ideen_bereich_darf_freie_sprache_an_den_planer(monkeypatch, echter_muster_router, cls) -> None:
    ausfuehrer = _Ausfuehrer({"x": 1})
    client, state = _client(monkeypatch, cls, ausfuehrer=ausfuehrer, planer=_Planer("idea_list"),
                            muster=echter_muster_router, synth=_Synth("Hier sind die Notizen."))
    client.post("/api/multihop/execute",
                json={"intent": "Welche Notizen zum Thema Bühne habe ich schon", "context": IDEEN})
    assert state.multihop_planner.aufrufe == 1 and len(ausfuehrer.plaene) == 1


def test_kurztext_im_ideen_bereich_geht_nicht_an_den_planer(monkeypatch, echter_muster_router) -> None:
    client, state = _client(monkeypatch, STARK, muster=echter_muster_router)
    body = client.post("/api/multihop/execute", json={"intent": "Rom Wetter", "context": IDEEN}).json()
    assert state.multihop_planner.aufrufe == 0
    assert body["final_text"].startswith("Das habe ich nicht verstanden. In Ideen kann ich z. B. „")


@pytest.mark.parametrize("space", ["roarboot", "clawport", "desktop", None])
def test_ausserhalb_der_ideen_kein_planer_auch_bei_starkem_cos(monkeypatch, echter_muster_router, space) -> None:
    client, state = _client(monkeypatch, STARK, muster=echter_muster_router)
    kontext = {"space": space} if space else {}
    body = client.post("/api/multihop/execute",
                       json={"intent": "Wie viele Notizen hat Stadtfest eigentlich", "context": kontext}).json()
    assert state.multihop_planner.aufrufe == 0 and state.plan_executor.plaene == []
    assert body["weg"] == "nicht-verstanden"


def test_regel_abschaltbar(monkeypatch, echter_muster_router) -> None:
    monkeypatch.setenv("PLANER_NUR_MIT_WEG", "0")
    client, state = _client(monkeypatch, STARK, planer=_Planer("bubble_stats"), muster=echter_muster_router,
                            ausfuehrer=_Ausfuehrer({"anzahl": 3}), synth=_Synth("Drei."))
    client.post("/api/multihop/execute", json={"intent": "Wie viele Notizen hat Stadtfest eigentlich",
                                               "context": {"space": "roarboot"}})
    assert state.multihop_planner.aufrufe == 1


def test_planer_spaces_einstellbar(monkeypatch) -> None:
    assert v.planer_erlaubt("ideas", "Welche Notizen habe ich")
    assert not v.planer_erlaubt("ideas", "Rom Wetter")
    assert not v.planer_erlaubt("roarboot", "Welche Agenten gibt es")
    monkeypatch.setenv("VERSTEHEN_PLANER_SPACES", "ideas, Rowboat")
    assert v.planer_erlaubt("roarboot", "Welche Agenten gibt es")


def test_sicherer_planer_plan_aus_ideen_laeuft_auch_in_fremden_space(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"monitore": 2})
    client, state = _client(monkeypatch, STARK, planer=_Planer("desktop_status"), ausfuehrer=ausfuehrer,
                            synth=_Synth("Zwei Monitore sind angeschlossen."))
    body = client.post("/api/multihop/execute",
                       json={"intent": "Wie viele Monitore hängen gerade am Rechner",
                             "context": IDEEN}).json()
    assert [h.capability for h in ausfuehrer.plaene[0].hops] == ["desktop_status"]
    assert body["ok"] is True


def test_mehrdeutiger_planer_plan_laeuft_nicht(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"x": 1})
    client, state = _client(monkeypatch, STARK, ausfuehrer=ausfuehrer,
                            planer=_MehrPlaner(["video_team_status", "bubble_stats"]))
    body = client.post("/api/multihop/execute",
                       json={"intent": "Wie läuft es bei Stadtfest?", "context": IDEEN}).json()
    assert state.multihop_planner.aufrufe == 1 and ausfuehrer.plaene == []
    assert body["weg"] == "mehrdeutig"
    assert body["final_text"].startswith("Das habe ich nicht verstanden. In Ideen kann ich z. B.")


def test_genannter_space_ohne_weg_schlaegt_starken_cosinus(monkeypatch) -> None:
    client, state = _client(monkeypatch, STARK, planer=_Planer("bubble_stats"))
    body = client.post("/api/multihop/execute",
                       json={"intent": "Wie ist der Stand im SWE Design?", "context": IDEEN}).json()
    assert state.multihop_planner.aufrufe == 0 and state.plan_executor.plaene == []
    assert body["weg"] == "kein-weg"


def test_fester_lesesatz_auftraege_deutsch_statt_dict(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"success": True, "tasks": [], "count": 0})
    client, state = _client(monkeypatch, SCHWACH, ausfuehrer=ausfuehrer)
    body = client.post("/api/multihop/execute",
                       json={"intent": "Zeig meine Aufträge", "context": {"space": "orders"}}).json()
    assert state.multihop_planner.aufrufe == 0
    assert [h.capability for h in ausfuehrer.plaene[0].hops] == ["schedule_list"]
    assert body["final_text"] == "Es sind keine geplanten Aufgaben eingetragen."
    assert body["ok"] is True


@pytest.mark.parametrize("kontext", [{"space": "ideas"}, {"space": "ideas", "bubble": {"id": "b0b1e001",
                                     "title": "Stadtfest"}}, {"space": "clawport"}, {}])
def test_fester_satz_eines_anderen_space_laeuft_aus_jedem_reiter(monkeypatch, kontext) -> None:
    ausfuehrer = _Ausfuehrer({"success": True, "tasks": [], "count": 0})
    client, state = _client(monkeypatch, STARK, ausfuehrer=ausfuehrer, planer=_Planer("idea_list"))
    body = client.post("/api/multihop/execute",
                       json={"intent": "Welche geplanten Aufgaben gibt es?", "context": kontext}).json()
    assert state.multihop_planner.aufrufe == 0
    assert [h.capability for h in ausfuehrer.plaene[0].hops] == ["schedule_list"]
    assert body["final_text"] == "Es sind keine geplanten Aufgaben eingetragen." and body["ok"] is True


def test_fester_lesesatz_fehler_ist_kein_haken(monkeypatch) -> None:
    client, _ = _client(monkeypatch, SCHWACH, ausfuehrer=_Ausfuehrer(None, ok=False))
    body = client.post("/api/multihop/execute", json={"intent": "Zeig meine Aufträge"}).json()
    assert body["ok"] is False and body["final_text"] == v.FEHLERSATZ


def test_auffangnetz_ersetzt_rohtext(monkeypatch) -> None:
    client, _ = _client(monkeypatch, STARK, planer=_Planer("browser_open"),
                        ausfuehrer=_Ausfuehrer(None, ok=False),
                        synth=_Synth("Multi-hop plan: 0/1 steps succeeded for intent: x"))
    body = client.post("/api/multihop/execute", json={"intent": "Öffne die Seite bitte",
                                                      "context": IDEEN}).json()
    assert body["ok"] is False and body["final_text"] == v.FEHLERSATZ
    assert "Multi-hop plan" in body["fehler"]


def test_kein_haken_ohne_ergebnis(monkeypatch) -> None:
    client, _ = _client(monkeypatch, STARK, planer=_Planer("browser_open"),
                        ausfuehrer=_Ausfuehrer("Timeout after 30s", ok=True),
                        synth=_Synth("Die Seite ist offen."))
    body = client.post("/api/multihop/execute", json={"intent": "Öffne die Seite bitte",
                                                      "context": IDEEN}).json()
    assert body["ok"] is False


def test_starker_satz_geht_weiter_an_den_planer(monkeypatch) -> None:
    client, state = _client(monkeypatch, STARK, planer=_Planer("browser_open"),
                            ausfuehrer=_Ausfuehrer({"url": "https://example.com"}),
                            synth=_Synth("Die Seite ist offen."))
    body = client.post("/api/multihop/execute", json={"intent": "Öffne example.com im Browser",
                                                      "context": IDEEN}).json()
    assert state.multihop_planner.aufrufe == 1
    assert body["ok"] is True and body["final_text"] == "Die Seite ist offen."


def test_abschalter_laesst_alten_weg(monkeypatch) -> None:
    monkeypatch.setenv("VERSTEHEN_ROUTING", "0")
    client, state = _client(monkeypatch, SCHWACH, planer=_Planer("browser_open"),
                            ausfuehrer=_Ausfuehrer({"url": "x"}), synth=_Synth("Erledigt."))
    client.post("/api/multihop/execute", json={"intent": "Irgendwas", "context": {"space": "swedesign"}})
    assert state.multihop_planner.aufrufe == 1
