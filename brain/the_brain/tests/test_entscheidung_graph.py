"""Jeder Satz als Entscheidung im Neo4j-Graphen (Routing-Papier 24.11.2026, Schritt 1).

Gegen eine Attrappe: kein echtes Neo4j. Was nur im Betrieb pruefbar ist (Treiber, Zeitgrenze
gegen den echten Server, Darstellung im Dashboard), steht im Commit.
"""
from __future__ import annotations

import json
import sys
import threading
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
from core.plan_executor import PlanRecorder  # noqa: E402
from web.routers import introspection as intro  # noqa: E402

SATZ = "Plane den Bühnenplan fürs Stadtfest mit der Druckerei"
ERLAUBT = {"trace_id", "ts", "reiter", "laenge", "weg", "stufe", "ok", "dauer_ms", "bereich",
           "bereich_quelle", "plan_id", "text", "faehigkeit", "schritte",
           # Stufen-Protokoll (Baustufe B): nur Kennungen und Zahlen, siehe test_stufen_protokoll.py
           "stufen_etage", "stufen_station", "stufen_ms"}


class Attrappe:
    """Nimmt Eintraege an wie DecisionGraph.upsert_entscheidung."""

    def __init__(self, warten_s: float = 0.0, fehler: bool = False) -> None:
        self.eintraege: list = []
        self.warten_s = warten_s
        self.fehler = fehler
        self.zeitgrenzen: list = []

    def upsert_entscheidung(self, eintrag, timeout_s=2.0):
        self.zeitgrenzen.append(timeout_s)
        if self.warten_s:
            time.sleep(self.warten_s)
        if self.fehler:
            raise RuntimeError("neo4j weg")
        self.eintraege.append(eintrag)
        return True


@pytest.fixture(autouse=True)
def _schalter(monkeypatch):
    monkeypatch.setenv("ENTSCHEIDUNG_GRAPH", "1")
    eg._SCHREIBER.clear()
    yield
    eg._SCHREIBER.clear()


# ── Stufe und Bereich ─────────────────────────────────────────────────────

@pytest.mark.parametrize("plan_id, stufe", [
    ("shortcut_1700000000000", "abkuerzung-regex"), ("ideen_1", "ideen-fest"), ("lesen_1", "lesesatz-fest"),
    ("sales_1", "sales-fest"), ("recherche_1", "recherche-fest"), ("fenster-1-0001", "desktop-fest"),
    ("augen-1-0001", "desktop-fest"), ("plan_a0000001", "planer"), ("irgendwas", "plan"),
    (None, None), ("", None),
])
def test_stufe_aus_plan(plan_id, stufe) -> None:
    assert eg.stufe_aus_plan(plan_id) == stufe


def test_stufe_trennt_festes_muster_vom_planer() -> None:
    fest = eg.eintrag_bauen({"trace_id": "t1", "plan_id": "shortcut_1", "ok": True}, routed_via="plan-executor")
    planer = eg.eintrag_bauen({"trace_id": "t2", "plan_id": "plan_b0b1e001", "ok": True}, routed_via="plan-executor")
    assert fest["weg"] == planer["weg"] == "plan-executor"
    assert (fest["stufe"], planer["stufe"]) == ("abkuerzung-regex", "planer")


def test_ohne_plan_ist_die_stufe_der_weg() -> None:
    e = eg.eintrag_bauen({"trace_id": "t3", "ok": True, "weg": "hoeflichkeit"})
    assert (e["weg"], e["stufe"], e["plan_id"]) == ("hoeflichkeit", "hoeflichkeit", None)


@pytest.mark.parametrize("daten, weg", [
    ({"rueckfrage": True}, "rueckfrage"), ({"hinweis": True}, "hinweis"), ({"pending": True}, "angenommen"),
    ({}, "ohne-weg"),
])
def test_weg_ohne_angabe_wie_in_der_spur(daten, weg) -> None:
    assert eg.eintrag_bauen({"trace_id": "t", **daten})["weg"] == weg


def test_fehlerstatus_ergibt_weg_fehler() -> None:
    assert eg.eintrag_bauen({"trace_id": "t"}, status_code=500)["weg"] == "fehler"


def test_bereich_aus_ziel_vor_antwort_vor_reiter() -> None:
    e = eg.eintrag_bauen({"trace_id": "t", "ok": True, "ziel": {"space": "rowboat"}, "space": "desktop"},
                         reiter="ideas")
    assert (e["bereich"], e["bereich_quelle"], e["reiter"]) == ("roarboot", "ziel", "ideas")
    e = eg.eintrag_bauen({"trace_id": "t", "ok": False, "rueckfrage": True, "space": "desktop"}, reiter="ideas")
    assert (e["bereich"], e["bereich_quelle"]) == ("desktop", "antwort")
    e = eg.eintrag_bauen({"trace_id": "t", "ok": False, "rueckfrage": True}, reiter="Dashboard")
    assert (e["bereich"], e["bereich_quelle"], e["reiter"]) == ("clawport", "reiter", "clawport")
    e = eg.eintrag_bauen({"trace_id": "t", "ok": False})
    assert (e["bereich"], e["bereich_quelle"]) == (None, "keiner")


def test_bereich_aus_den_schritten_auch_wenn_sie_misslangen() -> None:
    e = eg.eintrag_bauen({"trace_id": "t", "ok": False, "executed": {
        "s1": {"ok": False, "capability": "schedule_list", "target": ""}}})
    assert (e["bereich"], e["bereich_quelle"]) == ("orders", "schritte")


def test_eintrag_mit_satzanfang_aber_ohne_antwort() -> None:
    daten = {"trace_id": "t", "ok": True, "final_text": "Antwort zur Druckerei", "intent": SATZ,
             "weg": "ideen", "executed": {"s1": {"ok": True, "result": {"text": "Sponsoren"}}}}
    e = eg.eintrag_bauen(daten, satz=SATZ, reiter="ideas", laenge=len(SATZ), dauer_ms=12.7)
    assert set(e) == ERLAUBT
    assert e["text"] == SATZ
    text = json.dumps(e, ensure_ascii=False)
    assert "Antwort zur" not in text and "Sponsoren" not in text
    assert (e["laenge"], e["dauer_ms"]) == (len(SATZ), 12)


def test_satztext_gekuerzt_wie_plan_intent() -> None:
    e = eg.eintrag_bauen({"trace_id": "t"}, satz="x" * 1000)
    assert len(e["text"]) == eg.TEXT_MAX == 300
    assert eg.eintrag_bauen({"trace_id": "t"}, satz="   ")["text"] is None
    assert eg.eintrag_bauen({"trace_id": "t"})["text"] is None


@pytest.mark.parametrize("executed, faehigkeit, schritte, beschriftung", [
    ({"s1": {"capability": "bubble_list"}}, "bubble_list", 1, "abkuerzung-regex: bubble_list"),
    ({"s1": {"capability": "idea_add"}, "s2": {"capability": "idea_connect"},
      "s3": {"capability": "idea_list"}}, "idea_add", 3, "abkuerzung-regex: 3 Schritte"),
    ({}, None, 0, "abkuerzung-regex"),
    (None, None, 0, "abkuerzung-regex"),
])
def test_erste_faehigkeit_und_beschriftung(executed, faehigkeit, schritte, beschriftung) -> None:
    e = eg.eintrag_bauen({"trace_id": "t", "plan_id": "shortcut_1", "executed": executed})
    assert (e["faehigkeit"], e["schritte"]) == (faehigkeit, schritte)
    assert eg.beschriftung(e["stufe"], e["faehigkeit"], e["schritte"]) == beschriftung


@pytest.mark.parametrize("daten", [None, "x", {}, {"trace_id": ""}, {"trace_id": 5}])
def test_ohne_trace_id_kein_eintrag(daten) -> None:
    assert eg.eintrag_bauen(daten) is None


def test_ok_nur_als_wahrheitswert() -> None:
    assert eg.eintrag_bauen({"trace_id": "t", "ok": "ja"})["ok"] is None


# ── Schreiber: nie aufhalten, nie scheitern ──────────────────────────────

def test_melden_blockiert_nicht_bei_langsamem_graphen() -> None:
    graph = Attrappe(warten_s=0.5)
    beginn = time.monotonic()
    assert eg.melden(graph, {"trace_id": "t"}) is True
    assert time.monotonic() - beginn < 0.1
    assert eg.schreiber_fuer(graph).warten(2.0)
    assert len(graph.eintraege) == 1


def test_volle_schlange_wird_verworfen_und_gezaehlt() -> None:
    sperre = threading.Event()

    class Haengt(Attrappe):
        def upsert_entscheidung(self, eintrag, timeout_s=2.0):
            sperre.wait(2.0)

    graph = Haengt()
    schreiber = eg.Schreiber(graph, maxsize=2)
    eg._SCHREIBER[id(graph)] = schreiber
    angenommen = [eg.melden(graph, {"trace_id": f"t{i}"}) for i in range(6)]
    sperre.set()
    assert angenommen.count(False) >= 3
    assert schreiber.zaehler["verworfen"] == angenommen.count(False)


def test_fehler_im_graphen_wird_gezaehlt_und_geschluckt() -> None:
    graph = Attrappe(fehler=True)
    assert eg.melden(graph, {"trace_id": "t"}) is True
    assert eg.schreiber_fuer(graph).warten(2.0)
    assert eg.zaehler(graph)["fehler"] == 1 and eg.zaehler(graph)["geschrieben"] == 0


def test_zeitgrenze_wird_mitgegeben(monkeypatch) -> None:
    monkeypatch.setenv("ENTSCHEIDUNG_GRAPH_ZEIT_S", "0.5")
    graph = Attrappe()
    eg.melden(graph, {"trace_id": "t"})
    assert eg.schreiber_fuer(graph).warten(2.0)
    assert graph.zeitgrenzen == [0.5]


def test_schalter_aus(monkeypatch) -> None:
    monkeypatch.setenv("ENTSCHEIDUNG_GRAPH", "0")
    graph = Attrappe()
    assert eg.melden(graph, {"trace_id": "t"}) is False
    assert graph.eintraege == [] and "gemeldet" not in eg.zaehler(graph)


def test_melden_ohne_graph_oder_eintrag() -> None:
    assert eg.melden(None, {"trace_id": "t"}) is False
    assert eg.melden(Attrappe(), None) is False
    assert eg.melden(object(), {"trace_id": "t"}) is False


# ── DecisionGraph: Spiegel, Ansicht, Cypher ──────────────────────────────

def _graph_offline(monkeypatch) -> dgmod.DecisionGraph:
    monkeypatch.setattr(dgmod, "ENABLED", False)
    return dgmod.DecisionGraph()


def test_spiegel_zeigt_entscheidung_und_bereich(monkeypatch) -> None:
    dg = _graph_offline(monkeypatch)
    dg.upsert_plan("ideen_1", "", ok=True, hop_count=1)
    dg.upsert_entscheidung({"trace_id": "t1", "weg": "plan-executor", "stufe": "ideen-fest", "ok": True,
                            "bereich": "ideas", "bereich_quelle": "ziel", "plan_id": "ideen_1",
                            "text": SATZ, "faehigkeit": "idea_summarize", "schritte": 1})
    dg.upsert_entscheidung({"trace_id": "t2", "weg": "nicht-verstanden", "stufe": "nicht-verstanden",
                            "ok": False, "bereich": "ideas", "bereich_quelle": "reiter", "plan_id": None})
    bild = dg.query_subgraph()
    typen = [n["data"]["type"] for n in bild["nodes"]]
    assert typen.count("entscheidung") == 2 and typen.count("bereich") == 1
    assert typen.count("satz") == 2
    knoten = {n["data"]["id"]: n["data"] for n in bild["nodes"]}
    assert knoten["satz:t1"]["label"] == SATZ[:36] and knoten["satz:t1"]["intent"] == SATZ
    assert knoten["satz:t2"]["label"] == "Satz"
    assert knoten["ent_t1"]["label"] == "ideen-fest: idea_summarize"
    kanten = {(e["data"]["type"], e["data"]["source"], e["data"]["target"]) for e in bild["edges"]}
    assert ("entschieden_durch", "satz:t1", "ent_t1") in kanten
    assert ("entschieden_durch", "satz:t2", "ent_t2") in kanten
    assert ("waehlt", "ent_t1", "bereich:ideas") in kanten and ("waehlt", "ent_t2", "bereich:ideas") in kanten
    assert ("fuehrt_zu", "ent_t1", "ideen_1") in kanten
    assert bild["stats"]["entscheidungen"] == 2
    assert dg.stats_counters["writes_entscheidungen"] == 2


def test_keine_kante_zu_einem_plan_ausserhalb_des_bildes(monkeypatch) -> None:
    dg = _graph_offline(monkeypatch)
    dg.upsert_entscheidung({"trace_id": "t1", "weg": "plan-executor", "plan_id": "plan_b0b1e001"})
    bild = dg.query_subgraph()
    ids = {n["data"]["id"] for n in bild["nodes"]}
    for kante in bild["edges"]:
        assert kante["data"]["source"] in ids and kante["data"]["target"] in ids


def test_spiegel_ist_begrenzt(monkeypatch) -> None:
    dg = _graph_offline(monkeypatch)
    for i in range(300):
        dg.upsert_entscheidung({"trace_id": f"t{i}", "weg": "ideen"})
    assert len(dg._mem_entscheidungen) == 200


class _Sitzung:
    def __init__(self, aufrufe):
        self.aufrufe = aufrufe

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **werte):
        self.aufrufe.append((query, werte))
        return SimpleNamespace(consume=lambda: None)


def test_cypher_mit_zeitgrenze_satzanfang_ohne_antwort(monkeypatch) -> None:
    neo4j = pytest.importorskip("neo4j")
    dg = _graph_offline(monkeypatch)
    aufrufe: list = []
    dg._connected = True
    dg._driver = SimpleNamespace(session=lambda: _Sitzung(aufrufe))
    dg.upsert_entscheidung({"trace_id": "t1", "weg": "ideen", "stufe": "ideen-fest", "ok": True,
                            "reiter": "ideas", "laenge": 40, "bereich": "ideas", "plan_id": "ideen_1",
                            "text": SATZ, "faehigkeit": "idea_list", "schritte": 2,
                            "final_text": "Antwort"},
                           timeout_s=0.7)
    ((query, werte),) = aufrufe
    assert isinstance(query, neo4j.Query) and query.timeout == 0.7
    assert "intent" not in werte and "final_text" not in werte
    assert (werte["text"], werte["faehigkeit"], werte["schritte"]) == (SATZ, "idea_list", 2)
    assert "s.text = $text" in query.text and "e.faehigkeit = $faehigkeit" in query.text
    for teil in ("ENTSCHIEDEN_DURCH", "WAEHLT", "IM_REITER", "FUEHRT_ZU"):
        assert teil in query.text
    assert werte["eid"] == "ent_t1"


def test_neo4j_fehler_wird_weitergereicht_und_gezaehlt(monkeypatch) -> None:
    pytest.importorskip("neo4j")
    dg = _graph_offline(monkeypatch)

    def _kaputt():
        raise RuntimeError("weg")

    dg._connected = True
    dg._driver = SimpleNamespace(session=_kaputt)
    with pytest.raises(RuntimeError):
        dg.upsert_entscheidung({"trace_id": "t1"})
    assert dg.stats_counters["errors"] == 1
    assert len(dg._mem_entscheidungen) == 1  # der Spiegel hat ihn trotzdem


# ── Endpunkt: jeder Satz wird gemeldet, der Satz wartet nicht ────────────

def _client(monkeypatch, tmp_path, antwort, graph):
    async def _fest(request):
        return antwort

    monkeypatch.setattr(intro, "_multihop_execute", _fest)
    app = FastAPI()
    app.include_router(intro.router)
    app.state.plan_executor = SimpleNamespace(recorder=PlanRecorder(path=tmp_path / "h.jsonl"))
    app.state.decision_graph = graph
    return TestClient(app)


@pytest.mark.parametrize("antwort, weg", [
    ({"ok": True, "executed": {}, "final_text": "Hallo!", "weg": "hoeflichkeit"}, "hoeflichkeit"),
    ({"ok": False, "rueckfrage": True, "verstanden": False, "executed": {}, "final_text": "Nicht verstanden.",
      "weg": "nicht-verstanden", "space": "desktop"}, "nicht-verstanden"),
    ({"ok": False, "rueckfrage": True, "final_text": "Welche Bubble?"}, "rueckfrage"),
])
def test_jeder_satz_wird_gemeldet(monkeypatch, tmp_path, antwort, weg) -> None:
    graph = Attrappe()
    daten = {**antwort, "trace_id": "tr_e1"}
    client = _client(monkeypatch, tmp_path, JSONResponse(daten), graph)
    antwort_http = client.post("/api/multihop/execute", json={"intent": SATZ, "context": {"space": "desktop"}})
    assert antwort_http.json()["final_text"] == daten["final_text"]
    assert eg.schreiber_fuer(graph).warten(2.0)
    (e,) = graph.eintraege
    assert e["trace_id"] == "tr_e1" and e["weg"] == weg and e["reiter"] == "desktop"
    assert e["laenge"] == len(SATZ) and isinstance(e["dauer_ms"], int)
    assert e["text"] == SATZ
    assert daten["final_text"] not in json.dumps(e, ensure_ascii=False)


def test_plan_weg_bekommt_stufe_und_plan(monkeypatch, tmp_path) -> None:
    graph = Attrappe()
    daten = {"ok": True, "plan_id": "shortcut_1", "trace_id": "tr_e2", "final_text": "fertig",
             "executed": {"s1": {"ok": True, "capability": "desktop_fenster", "target": "", "space": "desktop"}}}
    client = _client(monkeypatch, tmp_path, JSONResponse(daten), graph)
    client.app.state.plan_executor.recorder.record({"trace_id": "tr_e2", "plan_id": "shortcut_1",
                                                    "routed_via": "plan-executor", "intent": ""})
    client.post("/api/multihop/execute", json={"intent": SATZ, "context": {"space": "ideas", "bubble": None}})
    assert eg.schreiber_fuer(graph).warten(2.0)
    (e,) = graph.eintraege
    assert (e["weg"], e["stufe"], e["plan_id"]) == ("plan-executor", "abkuerzung-regex", "shortcut_1")
    assert e["bereich"] == "desktop"
    assert (e["faehigkeit"], e["schritte"]) == ("desktop_fenster", 1)


def test_kaputter_graph_haelt_den_satz_nicht_auf(monkeypatch, tmp_path) -> None:
    graph = Attrappe(warten_s=1.0, fehler=True)
    client = _client(monkeypatch, tmp_path, JSONResponse(
        {"ok": True, "final_text": "Hallo!", "trace_id": "tr_e3", "weg": "hoeflichkeit"}), graph)
    beginn = time.monotonic()
    assert client.post("/api/multihop/execute", json={"intent": SATZ}).json()["final_text"] == "Hallo!"
    assert time.monotonic() - beginn < 0.9


def test_ohne_graph_laeuft_alles_wie_vorher(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, JSONResponse(
        {"ok": True, "final_text": "Hallo!", "trace_id": "tr_e4", "weg": "hoeflichkeit"}), None)
    assert client.post("/api/multihop/execute", json={"intent": SATZ}).json()["final_text"] == "Hallo!"


def test_stats_nennt_die_zaehler(monkeypatch, tmp_path) -> None:
    dg = _graph_offline(monkeypatch)
    client = _client(monkeypatch, tmp_path, JSONResponse(
        {"ok": True, "final_text": "Hallo!", "trace_id": "tr_e5", "weg": "hoeflichkeit"}), dg)
    client.post("/api/multihop/execute", json={"intent": SATZ})
    assert eg.schreiber_fuer(dg).warten(2.0)
    zaehler = client.get("/api/decision_graph/stats").json()["entscheidung_schreiber"]
    # Ohne Neo4j ist nichts geschrieben: nur der Spiegel hat den Eintrag (Befund aus dem Betrieb).
    assert zaehler["gemeldet"] == 1 and zaehler["geschrieben"] == 0 and zaehler["nur_spiegel"] == 1
    assert zaehler["zustand"] == "nicht_verbunden" and zaehler["grund"]


def test_geschrieben_zaehlt_nur_was_neo4j_bestaetigt(monkeypatch) -> None:
    pytest.importorskip("neo4j")
    dg = _graph_offline(monkeypatch)
    assert dg.upsert_entscheidung({"trace_id": "t0"}) is False
    dg._connected = True
    dg._driver = SimpleNamespace(session=lambda: _Sitzung([]))
    assert dg.upsert_entscheidung({"trace_id": "t1"}) is True
    schreiber = eg.schreiber_fuer(dg)
    assert schreiber.einen_schreiben({"trace_id": "t2"}) is True
    dg._connected = False
    assert schreiber.einen_schreiben({"trace_id": "t3"}) is False
    assert (schreiber.zaehler["geschrieben"], schreiber.zaehler["nur_spiegel"]) == (1, 1)
    assert eg.zaehler(dg)["zustand"] == "nicht_verbunden"
