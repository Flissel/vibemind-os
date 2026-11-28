"""Stufen-Protokoll je Satz (Etagen-Ansicht, Baustufe B).

Gegen Attrappen: kein echtes Neo4j, kein Modell. Alle Saetze und Namen sind erfunden.
Was nur im Betrieb pruefbar ist (echte Dauern, Schreiben in den echten Graphen), steht im Commit.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import decision_graph as dgmod  # noqa: E402
from core import entscheidung_graph as eg  # noqa: E402
from core import stufen_protokoll as sp  # noqa: E402
from core.plan_executor import PlanRecorder  # noqa: E402
from web.routers import introspection as intro  # noqa: E402

SATZ = "Leg eine Notiz zu Thema A an"


@pytest.fixture(autouse=True)
def _sauber(monkeypatch):
    monkeypatch.delenv("STUFEN_PROTOKOLL", raising=False)
    monkeypatch.setenv("ENTSCHEIDUNG_GRAPH", "1")
    sp._AKTUELL.set(None)
    eg._SCHREIBER.clear()
    yield
    sp._AKTUELL.set(None)
    eg._SCHREIBER.clear()


# ── Kennungen ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("wert", ["ideen-fest", "idea.create", "bubble_list", "medium", "3-schritte",
                                  "nicht-verstanden:medium", "ideas", 7])
def test_kennung_laesst_kennungen_durch(wert):
    assert sp.kennung(wert) == str(wert)


@pytest.mark.parametrize("wert", [SATZ, "zeig (mir|uns) die liste", "a b", "", None, True, "x" * 49,
                                  r"^leg\s+eine", "ä-umlaut", {"a": 1}, "-fuehrend"])
def test_kennung_macht_alles_andere_unkenntlich(wert):
    assert sp.kennung(wert) == "?"


# ── Marken und Uhr ────────────────────────────────────────────────────────

def test_ohne_beginn_tun_marken_nichts():
    sp.marke("muster")
    sp.station("muster", "ideen-fest")
    assert sp.abholen() is None


def test_marken_ergeben_etagen_mit_dauer():
    sp.beginnen()
    time.sleep(0.02)
    sp.marke("gespraech")
    sp.marke("muster")
    time.sleep(0.03)
    sp.marke("ausfuehrung")
    sp.marke("ergebnis")
    protokoll = sp.abholen()
    assert [p[0] for p in protokoll] == ["eingang", "gespraech", "muster", "ausfuehrung", "ergebnis"]
    assert protokoll[0][2] >= 15 and protokoll[2][2] >= 25
    assert all(isinstance(p[2], int) and p[2] >= 0 for p in protokoll)
    assert sp.abholen() is None           # abgeholt ist abgeholt


def test_gleiche_etage_hintereinander_bleibt_ein_eintrag():
    sp.beginnen()
    sp.marke("planung")
    sp.marke("planung", "planer")
    sp.marke("ergebnis")
    sp.marke("ergebnis")
    assert [(p[0], p[1]) for p in sp.abholen()] == [("eingang", ""), ("planung", "planer"), ("ergebnis", "")]


def test_station_benennt_die_laufende_etage():
    sp.beginnen()
    sp.marke("einordnung")
    sp.station("einordnung", "medium")
    sp.station("planung", "gibt-es-nicht")
    assert sp.abholen()[1][:2] == ("einordnung", "medium")


def test_unbekannte_etage_und_ueberlauf_werden_verworfen():
    sp.beginnen()
    sp.marke("dachboden")
    for _ in range(40):
        sp.marke("muster")
        sp.marke("gespraech")
    assert len(sp.abholen()) == sp.MAX_EINTRAEGE


def test_schalter_aus(monkeypatch):
    monkeypatch.setenv("STUFEN_PROTOKOLL", "0")
    sp.beginnen()
    sp.marke("muster")
    assert sp.abholen() is None


def test_marken_werfen_nie():
    sp.beginnen()
    sp.marke(None)          # type: ignore[arg-type]
    sp.marke(["muster"])    # type: ignore[arg-type]
    sp.station("muster", object())
    sp._AKTUELL.set("kaputt")   # type: ignore[arg-type]
    sp.marke("muster")
    sp.station("muster", "x")
    assert sp.abholen() is None


def test_gleichzeitige_saetze_vermischen_sich_nicht():
    async def satz(etage: str, pause: float):
        sp.beginnen()
        await asyncio.sleep(pause)
        sp.marke(etage)
        await asyncio.sleep(pause)
        return [p[0] for p in sp.abholen()]

    async def beide():
        return await asyncio.gather(satz("muster", 0.02), satz("planung", 0.01))

    eins, zwei = asyncio.run(beide())
    assert eins == ["eingang", "muster"] and zwei == ["eingang", "planung"]


# ── Die drei Listen ───────────────────────────────────────────────────────

def _listen(protokoll, **kw):
    out = sp.listen(protokoll, **kw)
    assert len(out["stufen_etage"]) == len(out["stufen_station"]) == len(out["stufen_ms"])
    return list(zip(out["stufen_etage"], out["stufen_station"], out["stufen_ms"]))


def test_fester_weg_mit_plan():
    zeilen = _listen(
        [("eingang", "", 1), ("gespraech", "", 2), ("muster", "", 6), ("ausfuehrung", "", 800), ("ergebnis", "", 3)],
        entschied="muster", stufe="ideen-fest", reiter="ideas", bereich="ideas", ausgang="erledigt",
        faehigkeit="idea.create", schritte=1,
        executed={"s1": {"capability": "idea.create", "elapsed_s": 0.8, "validator_verdict": {"verified": True}}})
    assert zeilen == [("eingang", "ideas", 1), ("gespraech", "kein-treffer", 2), ("muster", "ideen-fest", 6),
                      ("ausfuehrung", "idea.create", 800), ("pruefung", "validator-ok", -1), ("ergebnis", "ideas", 3)]


def test_planer_weg_mit_klasse_und_mehreren_schritten():
    zeilen = _listen(
        [("eingang", "", 1), ("gespraech", "", 2), ("muster", "", 9), ("einordnung", "medium", 140),
         ("planung", "planer", 5200), ("ausfuehrung", "", 3100), ("ergebnis", "", 900)],
        entschied="planung", stufe="planer", reiter="ideas", bereich="projects", ausgang="erledigt",
        faehigkeit="code.status", schritte=3, executed={"s1": {}, "s2": {}, "s3": {}})
    assert [z[1] for z in zeilen] == ["ideas", "kein-treffer", "kein-treffer", "medium", "planer", "3-schritte",
                                      "projects"]


def test_absage_der_einordnung_traegt_stufe_und_klasse():
    zeilen = _listen(
        [("eingang", "", 1), ("gespraech", "", 2), ("muster", "", 5), ("einordnung", "medium", 130), ("ergebnis", "", 1)],
        entschied="einordnung", stufe="nicht-verstanden", reiter="desktop", bereich=None, ausgang="absage")
    assert zeilen[3] == ("einordnung", "nicht-verstanden:medium", 130)
    assert zeilen[4] == ("ergebnis", "absage", 1)


def test_entscheidung_vor_beginn_ihrer_etage_steht_an_der_letzten_laufenden():
    # Absage mitten in den Mustern: die Etage Einordnung hat nie begonnen.
    zeilen = _listen([("eingang", "", 1), ("gespraech", "", 2), ("muster", "", 4), ("ergebnis", "", 1)],
                     entschied="einordnung", stufe="nicht-verstanden", reiter="ideas", ausgang="absage")
    assert zeilen[2] == ("muster", "nicht-verstanden", 4)


def test_weg_der_selbst_ausfuehrt_bekommt_eigene_ausfuehrung():
    # Die Etage hat selbst ausgefuehrt (kein gemeinsamer Ausfuehrer): Schritt-Dauer herausrechnen.
    zeilen = _listen([("eingang", "", 1), ("gespraech", "", 1300), ("ergebnis", "", 2)],
                     entschied="gespraech", stufe="recherche-gestartet", reiter="ideas", bereich="ideas",
                     ausgang="angenommen", faehigkeit="recherche.start", schritte=1,
                     executed={"s1": {"elapsed_s": 1.2}})
    assert zeilen == [("eingang", "ideas", 1), ("gespraech", "recherche-gestartet", 100),
                      ("ausfuehrung", "recherche.start", 1200), ("ergebnis", "ideas", 2)]


def test_satzteile_kommen_nie_in_die_listen():
    zeilen = _listen([("eingang", "", 1), ("muster", SATZ, 5), ("ausfuehrung", "", 5), ("ergebnis", "", 1)],
                     entschied="planung", stufe="planer", reiter=SATZ, bereich=SATZ, ausgang="erledigt",
                     faehigkeit="zeig (mir|uns) die liste", schritte=1, executed={"s1": {}})
    assert SATZ not in str(zeilen) and "(mir|uns)" not in str(zeilen)
    # Die Stufe selbst ist eine Kennung und bleibt; alles Uebrige wird unkenntlich.
    assert [z[1] for z in zeilen] == ["?", "planer", "?", "?"]


@pytest.mark.parametrize("protokoll", [None, [], "kaputt", [("eingang",)], [None], 7])
def test_listen_sind_leer_statt_zu_werfen(protokoll):
    assert sp.listen(protokoll, entschied="muster", stufe="ideen-fest") == {
        "stufen_etage": [], "stufen_station": [], "stufen_ms": []}


@pytest.mark.parametrize("executed, station", [
    ({"s1": {"validator_verdict": {"verified": True}}, "s2": {"validator_verdict": {"verified": False}}}, "validator-stopp"),
    ({"s1": {"validator_verdict": {"verified": None}}}, "validator-offen"),
    ({"s1": {"tool_calls": [{"approval_status": "requested"}, {"approval_status": "approved"}]}}, "freigabe-offen"),
    ({"s1": {"tool_calls": [{"approval_status": "denied"}]}}, "freigabe-nein"),
    ({"s1": {"tool_calls": [{"approval_status": "approved"}]}}, "freigabe-ok"),
    ({"s1": {"tool_calls": [{"approval_status": "none"}]}}, None),
    ({"s1": {}}, None),
    ("kaputt", None),
])
def test_pruefung_aus_den_schritten(executed, station):
    assert sp.pruefung_aus(executed) == station


# ── Eintrag, Graph, Abfrage ───────────────────────────────────────────────

def test_eintrag_traegt_die_drei_listen():
    eintrag = eg.eintrag_bauen(
        {"trace_id": "tr_ab12", "ok": True, "plan_id": "ideen_1764000000000", "space": "ideas",
         "executed": {"s1": {"capability": "idea.create", "elapsed_s": 0.5}}},
        satz=SATZ, reiter="ideas", laenge=len(SATZ), dauer_ms=520.0,
        stufen=[("eingang", "", 1), ("gespraech", "", 2), ("muster", "", 6), ("ausfuehrung", "", 500), ("ergebnis", "", 2)])
    assert eintrag["stufen_etage"] == ["eingang", "gespraech", "muster", "ausfuehrung", "ergebnis"]
    assert eintrag["stufen_station"][2:4] == ["ideen-fest", "idea.create"]
    assert eintrag["stufen_ms"] == [1, 2, 6, 500, 2]
    assert SATZ not in str([eintrag["stufen_etage"], eintrag["stufen_station"]])


def test_eintrag_ohne_protokoll_hat_leere_listen():
    eintrag = eg.eintrag_bauen({"trace_id": "tr_ab12", "ok": True, "weg": "hoeflichkeit", "executed": {}})
    assert (eintrag["stufen_etage"], eintrag["stufen_station"], eintrag["stufen_ms"]) == ([], [], [])


def test_graph_spiegel_und_abfrage_geben_die_listen_weiter(monkeypatch):
    monkeypatch.setattr(dgmod, "ENABLED", False)
    dg = dgmod.DecisionGraph()
    dg.upsert_entscheidung({"trace_id": "aa11bb22", "weg": "plan-executor", "stufe": "ideen-fest", "ok": True,
                            "bereich": "ideas", "text": SATZ,
                            "stufen_etage": ["eingang", "muster", "ergebnis"],
                            "stufen_station": ["ideas", "ideen-fest", "ideas"], "stufen_ms": [1, 7, 2]})
    out = dg.query_subgraph(50, 0.05)
    ent = next(n["data"] for n in out["nodes"] if n["data"]["type"] == "entscheidung")
    assert ent["stufen_etage"] == ["eingang", "muster", "ergebnis"] and ent["stufen_ms"] == [1, 7, 2]
    sicher = intro._ohne_text(out)
    ent = next(n["data"] for n in sicher["nodes"] if n["data"]["type"] == "entscheidung")
    assert ent["stufen_station"] == ["ideas", "ideen-fest", "ideas"]
    assert SATZ not in str(sicher)


@pytest.mark.parametrize("kaputt", [
    {"stufen_etage": ["eingang", "muster"], "stufen_station": ["ideas"], "stufen_ms": [1, 2]},
    {"stufen_etage": ["eingang"], "stufen_station": ["ideas"], "stufen_ms": ["schnell"]},
    {"stufen_etage": ["eingang"], "stufen_station": [None], "stufen_ms": [1]},
    {"stufen_etage": "eingang", "stufen_station": "ideas", "stufen_ms": 1},
    {},
])
def test_halbe_listen_werden_ganz_verworfen(kaputt):
    assert dgmod.DecisionGraph._stufen_listen(kaputt) == {"stufen_etage": [], "stufen_station": [], "stufen_ms": []}


def test_cypher_schreibt_die_drei_listen():
    cypher = dgmod.DecisionGraph._ENTSCHEIDUNG_CYPHER
    for feld in ("stufen_etage", "stufen_station", "stufen_ms"):
        assert f"e.{feld} = ${feld}" in cypher


# ── Ende zu Ende ueber den Satz-Endpunkt ──────────────────────────────────

class Attrappe:
    def __init__(self) -> None:
        self.eintraege: list = []

    def upsert_entscheidung(self, eintrag, timeout_s=2.0):
        self.eintraege.append(eintrag)
        return True


def _client(monkeypatch, tmp_path, ablauf, graph) -> TestClient:
    monkeypatch.setattr(intro, "_multihop_execute", ablauf)
    app = FastAPI()
    app.include_router(intro.router)
    app.state.plan_executor = SimpleNamespace(recorder=PlanRecorder(path=tmp_path / "h.jsonl"))
    app.state.decision_graph = graph
    return TestClient(app)


def test_satz_schreibt_protokoll_an_die_entscheidung(monkeypatch, tmp_path):
    async def ablauf(request):
        sp.marke("gespraech")
        sp.marke("muster")
        await asyncio.sleep(0.03)
        sp.marke("ausfuehrung")
        await asyncio.sleep(0.02)
        sp.marke("ergebnis")
        return JSONResponse({"ok": True, "trace_id": "tr_e2e1", "plan_id": "ideen_1764000000000", "space": "ideas",
                             "final_text": "Notiert.", "executed": {"s1": {"capability": "idea.create", "elapsed_s": 0.02}}})

    graph = Attrappe()
    client = _client(monkeypatch, tmp_path, ablauf, graph)
    antwort = client.post("/api/multihop/execute", json={"intent": SATZ, "context": {"space": "ideas"}})
    assert antwort.json()["final_text"] == "Notiert."
    assert eg.schreiber_fuer(graph).warten(2.0)
    eintrag = graph.eintraege[0]
    assert eintrag["stufen_etage"] == ["eingang", "gespraech", "muster", "ausfuehrung", "ergebnis"]
    assert eintrag["stufen_station"] == ["ideas", "kein-treffer", "ideen-fest", "idea.create", "ideas"]
    assert eintrag["stufen_ms"][2] >= 20 and eintrag["stufen_ms"][3] >= 10
    assert "stufen" not in antwort.json() and "stufen_ms" not in antwort.json()   # nichts davon in der Antwort
    assert sp._AKTUELL.get() is None


def test_fehler_im_protokoll_verhindert_die_antwort_nicht(monkeypatch, tmp_path):
    async def ablauf(request):
        return JSONResponse({"ok": True, "trace_id": "tr_e2e2", "weg": "hoeflichkeit", "final_text": "Gern.",
                             "executed": {}})

    def kaputt(*a, **k):
        raise RuntimeError("Uhr kaputt")

    # Die Funktionen fangen selbst alles ab; hier faellt sogar das aus (zweiter Riegel im Endpunkt
    # und in eintrag_bauen). Die Antwort kommt, und die Entscheidung wird trotzdem geschrieben.
    for name in ("beginnen", "marke", "abholen", "listen"):
        monkeypatch.setattr(sp, name, kaputt)
    graph = Attrappe()
    client = _client(monkeypatch, tmp_path, ablauf, graph)
    antwort = client.post("/api/multihop/execute", json={"intent": "Danke dir"})
    assert antwort.status_code == 200 and antwort.json()["final_text"] == "Gern."
    assert eg.schreiber_fuer(graph).warten(2.0)
    assert graph.eintraege[0]["weg"] == "hoeflichkeit" and graph.eintraege[0]["stufen_etage"] == []


def test_abgeschaltet_bleibt_die_entscheidung_ohne_listen(monkeypatch, tmp_path):
    monkeypatch.setenv("STUFEN_PROTOKOLL", "0")

    async def ablauf(request):
        sp.marke("muster")
        return JSONResponse({"ok": True, "trace_id": "tr_e2e3", "weg": "hoeflichkeit", "final_text": "Gern.",
                             "executed": {}})

    graph = Attrappe()
    client = _client(monkeypatch, tmp_path, ablauf, graph)
    assert client.post("/api/multihop/execute", json={"intent": "Danke dir"}).status_code == 200
    assert eg.schreiber_fuer(graph).warten(2.0)
    assert graph.eintraege[0]["stufen_etage"] == []


def test_messpunkte_stehen_im_satz_weg():
    """Die Marken sitzen an den Uebergaengen im echten Satz-Weg (Quelltext, kein Lauf)."""
    quelle = Path(intro.__file__).read_text(encoding="utf-8")
    for marke in ('_sp.marke("gespraech")', '_sp.marke("muster")', '_sp.marke("einordnung")',
                  '_sp.station("einordnung", level)', '_sp.marke("planung", "easy-chat")',
                  '_sp.marke("planung", "planer")', '_sp.marke("planung", "som-planner")',
                  '_sp.marke("planung", "som-team")', '_sp.marke("ausfuehrung")', '_sp.marke("ergebnis")'):
        assert marke in quelle, marke
