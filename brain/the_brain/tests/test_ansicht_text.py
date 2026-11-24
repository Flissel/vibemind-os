"""Satztexte aus /api/decision_graph/query nur fuer die Brain-Ansicht (Sicherheitsbefund 24.11.2026).

Gegen einen Spiegel ohne Neo4j. Nur im Betrieb pruefbar: welche Koepfe Electron und der
Browser an /brain wirklich schicken.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import decision_graph as dgmod  # noqa: E402
from web.routers import introspection as intro  # noqa: E402

SATZ = "Plane den Bühnenplan fürs Stadtfest mit der Druckerei"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DECISION_GRAPH_TEXT", raising=False)
    monkeypatch.setattr(dgmod, "ENABLED", False)
    dg = dgmod.DecisionGraph()
    dg.upsert_plan("plan_b0b1e001", SATZ, ok=True, hop_count=1)
    dg.upsert_entscheidung({"trace_id": "t1", "weg": "plan-executor", "stufe": "planer", "ok": True,
                            "bereich": "ideas", "plan_id": "plan_b0b1e001", "text": SATZ,
                            "faehigkeit": "idea_add", "schritte": 1, "laenge": len(SATZ)})
    app = FastAPI()
    app.include_router(intro.router)
    app.state.decision_graph = dg
    return TestClient(app)


def _abfrage(client, **koepfe):
    r = client.get("/api/decision_graph/query", headers=koepfe)
    assert r.status_code == 200
    return r


def test_ansicht_vom_selben_ursprung_sieht_den_satz(client):
    r = _abfrage(client, **{"Sec-Fetch-Site": "same-origin"})
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
def test_alle_anderen_bekommen_nur_kennung_und_laenge(client, koepfe):
    r = _abfrage(client, **koepfe)
    assert "Stadtfest" not in r.text and "Druckerei" not in r.text
    d = r.json()
    assert d["text_ausgeblendet"] is True
    knoten = {n["data"]["id"]: n["data"] for n in d["nodes"]}
    assert knoten["satz:t1"]["label"] == "Satz" and knoten["satz:t1"]["intent"] == ""
    assert knoten["satz:t1"]["laenge"] == len(SATZ)
    assert knoten["plan_b0b1e001"]["label"] == "Plan"
    # Struktur bleibt: Entscheidung, Bereich und Kanten
    assert knoten["ent_t1"]["label"] == "planer: idea_add"
    kanten = {(e["data"]["type"], e["data"]["source"], e["data"]["target"]) for e in d["edges"]}
    assert ("entschieden_durch", "satz:t1", "ent_t1") in kanten


def test_schalter_immer_und_nie(client, monkeypatch):
    monkeypatch.setenv("DECISION_GRAPH_TEXT", "immer")
    assert SATZ in _abfrage(client).text
    monkeypatch.setenv("DECISION_GRAPH_TEXT", "nie")
    assert SATZ not in _abfrage(client, **{"Sec-Fetch-Site": "same-origin"}).text


def test_ansicht_laedt_ihre_daten_vom_selben_ursprung():
    """Die Ansicht fragt mit relativem Pfad: der Browser schickt dann same-origin."""
    html = (Path(__file__).resolve().parents[1] / "web" / "templates"
            / "unified_brain_dashboard.html").read_text(encoding="utf-8")
    assert "fetch('/api/decision_graph/query" in html
