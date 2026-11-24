"""Routing 24.11.2026: ziel {space} je Antwort, Beispielsätze je Bereich, „Zurück“ aus der Übersicht.

Nur Attrappen: keine Datenbank, kein Agent, kein Modell. Namen und Kennungen erfunden.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN))

from core import beispielsaetze as bs  # noqa: E402
from core import ideen_abkuerzung as abk  # noqa: E402
from core import verstehen as v  # noqa: E402
from core import zurueck_vorsicht as zv  # noqa: E402
from core.antwort_ziel import bereich_der_schritte, ziel_ergaenzen  # noqa: E402
from core.plan_executor import _ziel_des_plans  # noqa: E402
from web.routers.introspection import router  # noqa: E402

REGISTRY = frozenset({"ideas", "bubbles", "desktop", "rowboat", "coding", "schedule", "thebrain"})


@pytest.fixture(autouse=True)
def _sauber():
    zv._offen.clear()
    zv._voriger.clear()
    yield
    zv._offen.clear()
    zv._voriger.clear()


# ── (a) ziel {space} ────────────────────────────────────────────────────


def test_ziel_aus_dem_feld_space() -> None:
    daten = ziel_ergaenzen({"ok": True, "final_text": "x", "space": "desktop"}, REGISTRY)
    assert daten["ziel"] == {"space": "desktop"}


def test_ziel_aus_den_schritten_ohne_feld_space() -> None:
    daten = {"ok": True, "final_text": "x", "executed": {
        "s1": {"ok": True, "space": "rowboat"}, "s2": {"ok": True, "space": "rowboat"}}}
    assert ziel_ergaenzen(daten, REGISTRY)["ziel"] == {"space": "rowboat"}


def test_kein_ziel_wenn_die_schritte_in_mehreren_bereichen_liegen() -> None:
    daten = {"ok": True, "final_text": "x", "executed": {
        "s1": {"ok": True, "space": "rowboat"}, "s2": {"ok": True, "space": "desktop"}}}
    assert "ziel" not in ziel_ergaenzen(daten, REGISTRY)


def test_ein_schritt_ohne_bereich_verhindert_das_ziel() -> None:
    assert bereich_der_schritte([{"ok": True, "space": "desktop"}, {"ok": True, "capability": "", "target": ""}]) is None


@pytest.mark.parametrize("daten", [
    {"ok": False, "final_text": "x", "space": "desktop"},
    {"ok": True, "rueckfrage": True, "final_text": "x", "space": "desktop"},
    {"ok": True, "hinweis": True, "final_text": "x", "space": "desktop"},
    {"ok": True, "pending": True, "final_text": "x", "space": "desktop"},
    {"ok": True, "status": "abgebrochen", "final_text": "x", "space": "ideas"},
    {"ok": True, "verstanden": False, "final_text": "x", "space": "desktop"},
    {"ok": True, "fehler": "RuntimeError", "final_text": "x", "space": "desktop"},
    {"ok": True, "final_text": "x", "space": "marketing"},          # nicht in der Registry
    {"ok": True, "final_text": "Hallo!"},                            # Höflichkeit: kein Bereich
])
def test_kein_ziel(daten) -> None:
    assert "ziel" not in ziel_ergaenzen(dict(daten), REGISTRY)


def test_vorhandenes_ziel_bleibt() -> None:
    ziel = {"space": "ideas", "bubble_id": "b0b1e001", "node_id": "a0000001"}
    assert ziel_ergaenzen({"ok": True, "space": "ideas", "ziel": dict(ziel)}, REGISTRY)["ziel"] == ziel


def test_registry_kennt_die_namen_wirklich() -> None:
    daten = ziel_ergaenzen({"ok": True, "final_text": "x", "space": "rowboat"})
    assert daten["ziel"] == {"space": "rowboat"}       # Name der Registry, die App bildet roarboot ab


@pytest.mark.parametrize("kontext_space", ["desktop", "Desktop"])
def test_kein_ziel_im_eigenen_bereich(kontext_space) -> None:
    daten = {"ok": True, "final_text": "x", "space": "desktop"}
    assert "ziel" not in ziel_ergaenzen(daten, REGISTRY, kontext_space=kontext_space)


@pytest.mark.parametrize("space,kontext_space", [("rowboat", "roarboot"), ("coding", "projects"),
                                                 ("bubbles", "ideas"), ("ideas", "ideas")])
def test_namen_der_registry_gelten_als_der_bereich_der_app(space, kontext_space) -> None:
    daten = {"ok": True, "final_text": "x", "space": space}
    assert "ziel" not in ziel_ergaenzen(daten, REGISTRY | {space}, kontext_space=kontext_space)


def test_ziel_aus_einem_anderen_bereich() -> None:
    daten = {"ok": True, "final_text": "x", "space": "rowboat"}
    assert ziel_ergaenzen(daten, REGISTRY, kontext_space="ideas")["ziel"] == {"space": "rowboat"}


def test_plan_completed_bleibt_bei_den_ideen() -> None:
    """Das Ereignis meldet auch Pläne ohne Menschensatz; dort springt die App nicht."""
    hr = SimpleNamespace(capability="desktop_fenster", target="", ok=True, pending=False, rendered_arg="",
                         result=None)
    plan = SimpleNamespace(hops=[SimpleNamespace(capability="desktop_fenster", arg_template="")], ziel_bubble_id="")
    assert _ziel_des_plans(plan, True, {"s1": hr}) is None


# ── (b) Beispielsätze ───────────────────────────────────────────────────

KONTEXT_BUBBLE = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"},
                  "cards": [{"id": "a0000001", "title": "Sponsoren"}], "selection": []}


@pytest.mark.parametrize("space,satz", [(s, b) for s, e in v.SPACES.items() for b in e.beispiele])
def test_jeder_beispielsatz_nimmt_den_festen_weg_ohne_modell(space, satz) -> None:
    """Der Beleg für /api/beispielsaetze: jeder ausgelieferte Satz trifft ohne Modell seinen festen Aufruf."""
    if space == "ideas":
        treffer = abk.loese(satz, KONTEXT_BUBBLE)
        assert treffer is not None and "rueckfrage" not in treffer and treffer.get("ereignis"), treffer
    else:
        treffer = v.feste_lesefrage(satz, space)
        assert treffer and treffer.get("capability") in v.FESTE_ANTWORTEN, (space, satz, treffer)


def test_hoechstens_fuenf_und_nur_aus_der_belegten_liste() -> None:
    for space, eintrag in v.SPACES.items():
        saetze = bs.saetze_fuer(space)
        assert len(saetze) <= bs.MAX_SAETZE
        assert all(s in eintrag.beispiele for s in saetze)


@pytest.mark.parametrize("gefragt,app", [("roarboot", "roarboot"), ("rowboat", "roarboot"), ("coding", "projects"),
                                         ("Desktop", "desktop"), ("schedule", "orders"), ("thebrain", "thebrain")])
def test_namen_der_app_und_der_registry(gefragt, app) -> None:
    assert bs.app_kennung(gefragt) == app
    assert bs.saetze_fuer(gefragt) == list(v.SPACES[app].beispiele)[:bs.MAX_SAETZE]


def _client(monkeypatch, ausfuehrer=None, muster=None):
    import core.difficulty_router as dr

    class _K:
        def classify(self, intent):
            return {"level": "medium", "score": 0.9, "method": "qwen-cosine", "reason": "x"}

    class _Planer:
        async def aplan(self, intent, context=None):
            raise AssertionError("kein Planer")

    monkeypatch.setattr(dr, "get_router", lambda: _K())
    monkeypatch.setenv("SOM_NOPLAN_FALLBACK", "0")
    monkeypatch.setenv("MULTI_VERB_ROUTING", "0")
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = ausfuehrer
    app.state.multihop_planner = _Planer()
    app.state.final_synthesizer = None
    if muster is not None:
        app.state.capability_router = muster
    return TestClient(app)


def test_stelle_beispielsaetze(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = client.get("/api/beispielsaetze", params={"space": "roarboot"}).json()
    assert body == {"space": "roarboot", "saetze": ["Welche Agenten gibt es in Rowboat?"]}
    assert client.get("/api/beispielsaetze", params={"space": "gibtsnicht"}).json() == {
        "space": "gibtsnicht", "saetze": []}
    assert client.get("/api/beispielsaetze").json() == {"space": "", "saetze": []}
    for leer in ("marketing", "events", "swedesign"):
        assert client.get("/api/beispielsaetze", params={"space": leer}).json()["saetze"] == []


# ── (c) „Zurück“ aus der Ideen-Übersicht ohne offene Bubble ─────────────

def _zurueck_muster():
    """Die echten Muster aus data/capabilities.yaml, als Router-Attrappe."""
    katalog = yaml.safe_load((BRAIN / "data" / "capabilities.yaml").read_text(encoding="utf-8"))
    (eintrag,) = [e for e in katalog if isinstance(e, dict) and e.get("capability") == "aenderung_zurueck"]
    muster = [re.compile(m, re.IGNORECASE) for m in eintrag["match_patterns"]]

    class _Treffer:
        capability = "aenderung_zurueck"
        match_method = "regex"
        has_execution_target = True

    class _CR:
        def route(self, intent):
            return _Treffer() if any(m.search(intent) for m in muster) else None

        def get_capability(self, name):
            return {"arg_kwarg": eintrag.get("arg_kwarg")}

    return _CR()


class _Recorder:
    def __getattr__(self, _name):
        return lambda *a, **k: None


class _Ausfuehrer:
    recorder = _Recorder()

    def __init__(self):
        self.plaene = []

    def execute(self, plan, **kw):
        self.plaene.append(plan)
        quittung = {"isError": False, "content": [{"type": "text", "text": json.dumps(
            {"gruppe": "ideen_1", "anzahl": 3, "eintraege": [
                {"tabelle": "ideas", "vorgang": "DELETE", "titel": "Stadtfest"}]})}]}
        return {"ok": True, "plan": plan.to_dict(), "executed": {"s1": {
                    "ok": True, "result": quittung, "error": None, "capability": plan.hops[0].capability,
                    "target": "mcp:brain-ideas:spaces-ideas:aenderung_zurueck",
                    "rendered_arg": plan.hops[0].arg_template}},
                "state": {}, "elapsed_s": 0.01, "replans": 0}


@pytest.mark.parametrize("kontext", [
    {"space": "ideas", "bubbles": [{"id": "b0b1e001", "title": "Messe"}]},   # Übersicht
    {"space": "ideas", "bubble": None, "bubbles": []},                         # nach dem Verlassen
    {"space": "ideas"},
])
@pytest.mark.parametrize("satz", ["Zurück", "Mach das rückgängig", "Stell die gelöschte Bubble wieder her"])
def test_zurueck_aus_der_uebersicht_nimmt_die_letzte_aenderung_zurueck(monkeypatch, kontext, satz) -> None:
    ausfuehrer = _Ausfuehrer()
    client = _client(monkeypatch, ausfuehrer, _zurueck_muster())
    body = client.post("/api/multihop/execute", json={"intent": satz, "context": kontext}).json()
    (plan,) = ausfuehrer.plaene
    assert [h.capability for h in plan.hops] == ["aenderung_zurueck"]
    assert body["ok"] is True and "rueckfrage" not in body, body
    assert body["final_text"].startswith("Zurückgenommen:"), body["final_text"]
    assert "ziel" not in body          # schon in den Ideen: kein Sprung, keine Bubble wird geöffnet


def test_zurueck_aus_einem_anderen_bereich_springt_in_die_ideen(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer()
    client = _client(monkeypatch, ausfuehrer, _zurueck_muster())
    body = client.post("/api/multihop/execute", json={"intent": "Zurück", "context": {"space": "desktop"}}).json()
    assert [h.capability for h in ausfuehrer.plaene[0].hops] == ["aenderung_zurueck"]
    assert body["ok"] is True and body["ziel"] == {"space": "ideas"}
