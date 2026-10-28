"""Kampagnen (28.10.2026, Migration 095) ohne echte DB - FalscheDB wie test_plan_api."""
import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import server
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

KID = "0123abcd-3333-3333-3333-333333333333"
PK, AK = "pult-k", "api-k"
P, A = {"X-Pult-Key": PK}, {"X-API-Key": AK}


@pytest.fixture
def db(monkeypatch):
    f = FalscheDB()
    f.psql = FalschePsql(f)
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", f.psql)
    monkeypatch.setenv("MARKETING_PULT_KEY", PK)
    monkeypatch.setattr(server, "API_KEY", AK)
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


def test_pult_lesen_mit_beitraegen(db, c):
    db.antworten = [[{"id": KID, "name": "Herbst", "zielgruppe": "sales", "status": "geplant"}],
                    [{"id": "p1", "kampagne": KID, "titel": "Auftakt", "status": "vorschlag"}]]
    r = c.get("/api/pult/kampagnen?mandant=vibemind", headers=P)
    assert r.status_code == 200
    k = r.json()["kampagnen"][0]
    assert k["name"] == "Herbst" and k["beitraege"][0]["titel"] == "Auftakt"
    assert "marketing.kampagnen" in db.sql[0] and "'vibemind'" in db.sql[0]


def test_pult_anlegen(db, c):
    db.antworten = [[{"id": KID}]]
    r = c.post("/api/pult/kampagnen", headers=P, json={
        "mandant": "vibemind", "name": "Herbst", "ziel": "bAV-Termine", "zielgruppe": "verteiler",
        "von": "2026-11-01", "bis": "2026-11-30", "von_wer": "Felix"})
    assert r.status_code == 200 and r.json() == {"id": KID, "status": "in_planung"}
    assert "marketing.kampagne_anlegen(" in db.sql[0] and "'verteiler'" in db.sql[0]


@pytest.mark.parametrize("koerper, grund", [
    ({"mandant": "vibemind", "name": "H", "zielgruppe": "alle", "von": "2026-11-01", "bis": "2026-11-30"}, "zielgruppe"),
    ({"mandant": "vibemind", "name": "", "zielgruppe": "sales", "von": "2026-11-01", "bis": "2026-11-30"}, "name"),
    ({"mandant": "vibemind", "name": "H", "zielgruppe": "sales", "von": "1.11.", "bis": "2026-11-30"}, "von"),
])
def test_pult_anlegen_formen(db, c, koerper, grund):
    r = c.post("/api/pult/kampagnen", headers=P, json=koerper)
    assert r.status_code == 422 and grund in r.json()["detail"].lower()
    assert not db.sql


def test_agent_offen_beitrag_geplant(db, c):
    db.antworten = [[{"id": KID, "status": "in_planung"}], []]
    r = c.get("/api/plan/kampagnen", headers=A)
    assert r.status_code == 200 and r.json()["kampagnen"][0]["id"] == KID
    db.antworten = [[{"id": "p2"}]]
    r = c.post(f"/api/plan/kampagnen/{KID}/beitrag", headers=A,
               json={"datum": "2026-11-05", "art": "post", "titel": "Teaser"})
    assert r.status_code == 200 and r.json() == {"id": "p2", "status": "vorschlag"}
    assert "marketing.kampagne_beitrag(" in db.sql[-1] and "'agent'" in db.sql[-1]
    db.antworten = [[{"s": "geplant"}]]
    r = c.post(f"/api/plan/kampagnen/{KID}/geplant", headers=A)
    assert r.json() == {"status": "geplant"}


def test_ohne_schluessel(db, c):
    assert c.get("/api/pult/kampagnen?mandant=vibemind").status_code == 401
    assert c.get("/api/plan/kampagnen").status_code == 401
