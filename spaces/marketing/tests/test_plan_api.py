"""Kampagnenkalender (17.10.2026, Migration 071) ohne echte DB - FalscheDB wie test_ton_api."""
import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import server
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

PID = "0123abcd-2222-2222-2222-222222222222"
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


def test_pult_lesen_monat(db, c):
    db.antworten = [[{"id": PID, "datum": "2026-10-20", "art": "newsletter", "titel": "Herbst", "status": "geplant"}]]
    r = c.get("/api/pult/plan?mandant=vibemind&von=2026-10-01&bis=2026-10-31", headers=P)
    assert r.status_code == 200 and r.json()["eintraege"][0]["titel"] == "Herbst"
    assert "marketing.kampagnen_plan" in db.sql[0] and "'vibemind'" in db.sql[0] and "'2026-10-31'" in db.sql[0]


def test_pult_anlegen_als_betreiber(db, c):
    db.antworten = [[{"id": PID}]]
    r = c.post("/api/pult/plan", headers=P, json={"mandant": "vibemind", "datum": "2026-10-20", "art": "podcast",
                                                   "titel": "Folge 1", "notiz": "zwei Stimmen"})
    assert r.status_code == 200 and r.json() == {"id": PID}
    assert "marketing.plan_anlegen(" in db.sql[0] and "'betreiber'" in db.sql[0] and "INSERT" not in db.sql[0]


def test_pult_aendern(db, c):
    db.antworten = [[{"s": "verschoben"}]]
    r = c.post(f"/api/pult/plan/{PID}", headers=P, json={"datum": "2026-10-25"})
    assert r.status_code == 200 and r.json() == {"status": "verschoben"}
    assert "marketing.plan_aendern(" in db.sql[0] and "'2026-10-25'" in db.sql[0]


@pytest.mark.parametrize("koerper, grund", [
    ({"mandant": "vibemind", "datum": "20.10.2026", "art": "post", "titel": "T"}, "datum"),
    ({"mandant": "vibemind", "datum": "2026-10-20", "art": "fax", "titel": "T"}, "art"),
    ({"mandant": "vibemind", "datum": "2026-10-20", "art": "post", "titel": ""}, "titel"),
    ({"mandant": "vibemind", "datum": "2026-10-20", "art": "post", "titel": "T", "notiz": "x" * 1001}, "notiz"),
    ({"datum": "2026-10-20", "art": "post", "titel": "T"}, "mandant"),
])
def test_pult_anlegen_formen(db, c, koerper, grund):
    r = c.post("/api/pult/plan", headers=P, json=koerper)
    assert r.status_code == 422 and grund in r.json()["detail"].lower()
    assert not db.sql


def test_pult_braucht_schluessel(db, c):
    assert c.get("/api/pult/plan?mandant=vibemind").status_code == 401


def test_agent_vorschlag_ist_immer_vorschlag(db, c):
    db.antworten = [[{"id": PID}]]
    r = c.post("/api/plan/vorschlag", headers=A, json={"firma": "vibemind", "datum": "2026-10-22", "art": "newsletter",
                                                        "titel": "Herbst-Newsletter", "status": "geplant"})
    assert r.status_code == 200 and r.json() == {"id": PID, "status": "vorschlag"}
    assert "'agent'" in db.sql[0] and "'betreiber'" not in db.sql[0]


def test_agent_lesen(db, c):
    db.antworten = [[{"id": PID, "datum": "2026-10-22", "status": "vorschlag"}]]
    r = c.get("/api/plan?firma=vibemind&tage=14", headers=A)
    assert r.status_code == 200 and r.json()["eintraege"][0]["status"] == "vorschlag"


def test_agent_ohne_schluessel(db, c):
    assert c.get("/api/plan?firma=vibemind").status_code == 401
