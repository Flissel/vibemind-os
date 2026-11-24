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
SPUR = "a0000001"  # trace_id in der Form hex/Ziffern
THEMA = "topic:Druckerei Stadtfest"  # Cluster-Kennung mit Klartext


@pytest.fixture
def dg(monkeypatch):
    monkeypatch.delenv("DECISION_GRAPH_TEXT", raising=False)
    monkeypatch.delenv("DECISION_GRAPH_HOSTS", raising=False)
    monkeypatch.setattr(dgmod, "ENABLED", False)
    dg = dgmod.DecisionGraph()
    dg.upsert_plan("plan_b0b1e001", SATZ, ok=True, hop_count=1)
    dg.upsert_entscheidung({"trace_id": SPUR, "weg": "plan-executor", "stufe": "planer", "ok": True,
                            "bereich": "ideas", "plan_id": "plan_b0b1e001", "text": SATZ,
                            "faehigkeit": "idea_add", "schritte": 1, "laenge": len(SATZ)})
    # Ein Cluster, dessen Beschriftung und Thema aus Inhalt stammen.
    dg._mem_clusters[THEMA] = {"id": THEMA, "label": "Sponsoren Druckerei", "topic": "Druckerei Stadtfest",
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
    assert knoten[f"satz:{SPUR}"]["label"] == SATZ[:36]
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
    satz = knoten[f"satz:{SPUR}"]
    assert satz == {"id": f"satz:{SPUR}", "type": "satz", "trace_id": SPUR, "reiter": "",
                    "laenge": len(SATZ), "ts": satz["ts"], "label": "Satz"}
    assert knoten["plan_b0b1e001"]["label"] == "Plan" and "intent" not in knoten["plan_b0b1e001"]
    cluster = [k for k in knoten.values() if k["type"] == "cluster"][0]
    assert cluster["label"] == "Cluster" and "topic" not in cluster and cluster["activation"] == 0.9
    assert cluster["id"].startswith("k") and "topic" not in r.text
    assert knoten[f"ent_{SPUR}"]["label"] == "planer: idea_add"
    assert knoten["bereich:ideas"]["label"] == "ideas"
    kanten = {(e["data"]["type"], e["data"]["source"], e["data"]["target"]) for e in d["edges"]}
    assert ("entschieden_durch", f"satz:{SPUR}", f"ent_{SPUR}") in kanten


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
    x1, p1 = (n["data"] for n in sauber["nodes"])
    assert {k: v for k, v in x1.items() if k != "id"} == {"type": "neu", "label": "?"}
    assert {k: v for k, v in p1.items() if k != "id"} == {"type": "plan", "ok": True, "label": "Plan"}


def test_kennung_mit_klartext_wird_undurchsichtig_kanten_folgen():
    """Sicherheitsbefund: Kennungen koennen Inhalt tragen (topic:<Thema>)."""
    out = {"connected": True, "nodes": [
        {"data": {"id": "topic:Sponsoren Druckerei", "type": "cluster", "activation": 0.5}},
        {"data": {"id": "plan_b0b1e001", "type": "plan", "ok": True}},
        {"data": {"id": "plan_b0b1e001:Stadtfest-Schritt", "type": "hop", "capability": "idea_add"}},
        {"data": {"id": "bereich:Bühnenplan Druckerei", "type": "bereich"}},
        {"data": {"id": "satz:Stadtfest", "type": "satz", "trace_id": "Stadtfest"}},
    ], "edges": [
        {"data": {"id": "fired_topic:Sponsoren Druckerei_plan_b0b1e001", "type": "fired",
                  "source": "topic:Sponsoren Druckerei", "target": "plan_b0b1e001"}},
        {"data": {"id": "hashop_x", "type": "has_hop", "source": "plan_b0b1e001",
                  "target": "plan_b0b1e001:Stadtfest-Schritt"}},
    ]}
    sauber = intro._ohne_text(out)
    text = json.dumps(sauber, ensure_ascii=False)
    for wort in ("Sponsoren", "Druckerei", "Stadtfest", "Bühnenplan", "topic"):
        assert wort not in text, wort
    ids = [n["data"]["id"] for n in sauber["nodes"]]
    assert ids[1] == "plan_b0b1e001"                      # inhaltsfreie Form bleibt lesbar
    assert all(i.startswith("k") and len(i) == 17 for i in ids[:1] + ids[2:])
    assert sauber["nodes"][3]["data"]["label"] == "Bereich"
    fired, hashop = (e["data"] for e in sauber["edges"])
    assert (fired["source"], fired["target"]) == (ids[0], ids[1])   # Kanten zeigen weiter richtig
    assert (hashop["source"], hashop["target"]) == (ids[1], ids[2])
    assert fired["id"].startswith("e") and fired["id"] != hashop["id"]
    # Stabil zwischen Abfragen (die Ansicht erkennt Knoten wieder).
    assert [n["data"]["id"] for n in intro._ohne_text(out)["nodes"]] == ids


@pytest.mark.parametrize("kennung", ["plan_b0b1e001", "ideen_1795540008614", "fenster-1795540008614-0007",
                                     "disp_0123456789", "satz:a0000001", "ent_a0000001-0b",
                                     "bereich:ideas"])
def test_inhaltsfreie_kennungen_bleiben(kennung):
    assert intro._undurchsichtig(kennung) == kennung


def test_zustand_bleibt_ohne_text_erhalten():
    """Die Ansicht braucht neo4j_connected und abfrage_fehler fuer ihre Zustandszeile."""
    sauber = intro._ohne_text({"connected": True, "neo4j_connected": True, "abfrage_fehler": True,
                               "error": "Druckerei", "nodes": [], "edges": []})
    assert sauber["neo4j_connected"] is True and sauber["abfrage_fehler"] is True
    assert "error" not in sauber


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
