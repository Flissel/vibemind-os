"""Satztexte aus /api/decision_graph/query nur fuer die Brain-Ansicht (Sicherheitsbefunde 24.11.2026).

Gegen einen Spiegel ohne Neo4j. Nur im Betrieb pruefbar: welche Koepfe Electron und der
Browser an /brain wirklich schicken.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import decision_graph as dgmod  # noqa: E402
from web.routers import introspection as intro  # noqa: E402

SATZ = "Plane den Bühnenplan fürs Stadtfest mit der Druckerei"
ANSICHT = {"Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def dg(monkeypatch):
    monkeypatch.delenv("DECISION_GRAPH_TEXT", raising=False)
    monkeypatch.delenv("DECISION_GRAPH_HOSTS", raising=False)
    monkeypatch.setattr(dgmod, "ENABLED", False)
    dg = dgmod.DecisionGraph()
    dg.upsert_plan("plan_b0b1e001", SATZ, ok=True, hop_count=1)
    dg.upsert_entscheidung({"trace_id": "t1", "weg": "plan-executor", "stufe": "planer", "ok": True,
                            "bereich": "ideas", "plan_id": "plan_b0b1e001", "text": SATZ,
                            "faehigkeit": "idea_add", "schritte": 1, "laenge": len(SATZ)})
    # Ein Cluster, dessen Beschriftung und Thema aus Inhalt stammen.
    dg._mem_clusters["c1"] = {"id": "c1", "label": "Sponsoren Druckerei", "topic": "Druckerei Stadtfest",
                              "activation": 0.9, "member_count": 3}
    return dg


def _client(dg, host: str = "localhost:5000") -> TestClient:
    app = FastAPI()
    app.include_router(intro.router)
    app.state.decision_graph = dg
    return TestClient(app, base_url=f"http://{host}")


def _abfrage(client, **koepfe):
    r = client.get("/api/decision_graph/query", headers=koepfe)
    assert r.status_code == 200
    return r


@pytest.mark.parametrize("host", ["localhost:5000", "127.0.0.1:5000", "localhost:5002", "[::1]:5000"])
def test_ansicht_vom_selben_ursprung_sieht_den_satz(dg, host):
    # Host-Kopf direkt gesetzt: der TestClient kann "[::1]" in der Basis-URL nicht lesen.
    r = _abfrage(_client(dg), Host=host, **ANSICHT)
    assert SATZ in r.text
    knoten = {n["data"]["id"]: n["data"] for n in r.json()["nodes"]}
    assert knoten["satz:t1"]["label"] == SATZ[:36]
    assert "text_ausgeblendet" not in r.json()


@pytest.mark.parametrize("koepfe", [
    {},                                   # curl, andere Dienste
    {"Sec-Fetch-Site": "cross-site"},     # fremde Webseite im Browser des Betreibers
    {"Sec-Fetch-Site": "same-site"},      # anderer Port auf localhost
    {"Sec-Fetch-Site": "none"},           # direkt in die Adresszeile getippt
])
def test_ohne_ansicht_nur_erlaubte_felder(dg, koepfe):
    r = _abfrage(_client(dg), **koepfe)
    for wort in ("Stadtfest", "Druckerei", "Sponsoren", "Bühnenplan"):
        assert wort not in r.text, wort
    d = r.json()
    assert d["text_ausgeblendet"] is True
    knoten = {n["data"]["id"]: n["data"] for n in d["nodes"]}
    assert knoten["satz:t1"] == {"id": "satz:t1", "type": "satz", "trace_id": "t1", "reiter": "",
                                 "laenge": len(SATZ), "ts": knoten["satz:t1"]["ts"], "label": "Satz"}
    assert knoten["plan_b0b1e001"]["label"] == "Plan" and "intent" not in knoten["plan_b0b1e001"]
    assert knoten["c1"]["label"] == "Cluster" and "topic" not in knoten["c1"]
    assert knoten["c1"]["activation"] == 0.9
    assert knoten["ent_t1"]["label"] == "planer: idea_add"
    assert knoten["bereich:ideas"]["label"] == "ideas"
    kanten = {(e["data"]["type"], e["data"]["source"], e["data"]["target"]) for e in d["edges"]}
    assert ("entschieden_durch", "satz:t1", "ent_t1") in kanten


@pytest.mark.parametrize("host", ["boese.example:5000", "boese.example", "localhost.boese.example:5000",
                                  "127.0.0.1.nip.example:5000"])
def test_dns_rebinding_fremder_host_bekommt_keinen_text(dg, host):
    r = _abfrage(_client(dg, host), **ANSICHT)
    assert SATZ not in r.text and r.json()["text_ausgeblendet"] is True


def test_weitere_hosts_per_umgebung(dg, monkeypatch):
    monkeypatch.setenv("DECISION_GRAPH_HOSTS", "brain.lokal")
    assert SATZ in _abfrage(_client(dg, "brain.lokal:5000"), **ANSICHT).text


def test_unbekannte_knotenart_und_neue_felder_fallen_weg():
    out = {"connected": True, "error": "geheim", "nodes": [
        {"data": {"id": "x1", "type": "neu", "label": "Druckerei", "notiz": "Stadtfest"}},
        {"data": {"id": "p1", "type": "plan", "label": "Stadtfest", "neues_feld": "Druckerei", "ok": True}},
    ], "edges": [{"data": {"id": "e1", "source": "p1", "target": "x1", "type": "t", "text": "Sponsoren"}}]}
    sauber = intro._ohne_text(out)
    text = json.dumps(sauber, ensure_ascii=False)
    for wort in ("Stadtfest", "Druckerei", "Sponsoren", "geheim"):
        assert wort not in text, wort
    assert sauber["nodes"][0]["data"] == {"id": "x1", "type": "neu", "label": "?"}
    assert sauber["nodes"][1]["data"] == {"id": "p1", "type": "plan", "ok": True, "label": "Plan"}


def test_schalter_immer_und_nie(dg, monkeypatch):
    monkeypatch.setenv("DECISION_GRAPH_TEXT", "immer")
    assert SATZ in _abfrage(_client(dg, "boese.example")).text
    monkeypatch.setenv("DECISION_GRAPH_TEXT", "nie")
    assert SATZ not in _abfrage(_client(dg), **ANSICHT).text


def test_ansicht_laedt_ihre_daten_vom_selben_ursprung():
    """Die Ansicht fragt mit relativem Pfad: der Browser schickt dann same-origin."""
    html = (Path(__file__).resolve().parents[1] / "web" / "templates"
            / "unified_brain_dashboard.html").read_text(encoding="utf-8")
    assert "fetch('/api/decision_graph/query" in html
