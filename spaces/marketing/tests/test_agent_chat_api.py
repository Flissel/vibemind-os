"""Agent-Routen fuer den Newsletter-Chat (20.10.2026): ein Agent (marketing-claw) legt denselben
marketing.chat_auftraege-Auftrag an wie der Editor - mit X-Agent-Key statt X-Pult-Key. Ohne echte DB."""
import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import server
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

IID = "0123abcd-3333-3333-3333-333333333333"
AID = "0123abcd-4444-4444-4444-444444444444"
AGENT = {"X-Agent-Key": "agent-k"}


@pytest.fixture
def db(monkeypatch):
    f = FalscheDB()
    f.psql = FalschePsql(f)
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", f.psql)
    monkeypatch.setenv("MARKETING_PULT_KEY", "pult-k")
    monkeypatch.setenv("MARKETING_AGENT_KEY", "agent-k")
    monkeypatch.setattr(server, "API_KEY", "")   # PC: kein globaler Schluessel, nur der Agent-Schluessel
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


def test_chat_anlegen_geht_ueber_dieselbe_db_funktion_wie_der_editor(db, c):
    db.antworten = [[{"s": {"id": AID, "status": "offen"}}]]
    r = c.post(f"/api/agent/inhalte/{IID}/chat", headers=AGENT, json={"nachricht": "Mach die Ueberschrift kuerzer"})
    assert r.status_code == 200 and r.json() == {"auftrag": AID, "status": "offen"}
    sql = db.sql[0]
    assert "marketing.pult_chat_senden(" in sql and f"'{IID}'" in sql and "INSERT" not in sql
    assert '"von": "agent"' in sql                     # Herkunft steht im Auftrag, nicht im Text


def test_wartet_wenn_schon_runden_laufen(db, c):
    db.antworten = [[{"s": {"id": AID, "status": "wartet"}}]]
    r = c.post(f"/api/agent/inhalte/{IID}/chat", headers=AGENT, json={"nachricht": "Noch ein Absatz"})
    assert r.json()["status"] == "wartet"


def test_agent_kann_keine_editor_auswahl_oder_anhaenge_unterschieben(db, c):
    db.antworten = [[{"s": {"id": AID, "status": "offen"}}]]
    c.post(f"/api/agent/inhalte/{IID}/chat", headers=AGENT,
           json={"nachricht": "x", "kontext": {"anhaenge": [{"art": "bild", "name": "a.png"}], "fenster": "f"}})
    assert "anhaenge" not in db.sql[0] and "fenster" not in db.sql[0]


@pytest.mark.parametrize("koerper", [{}, {"nachricht": "  "}, {"nachricht": "x" * 2001}])
def test_ungueltige_nachricht_ohne_sql(db, c, koerper):
    r = c.post(f"/api/agent/inhalte/{IID}/chat", headers=AGENT, json=koerper)
    assert r.status_code == 422 and db.sql == []


def test_ohne_oder_mit_falschem_schluessel(db, c):
    assert c.post(f"/api/agent/inhalte/{IID}/chat", json={"nachricht": "x"}).status_code == 401
    assert c.post(f"/api/agent/inhalte/{IID}/chat", headers={"X-Agent-Key": "falsch"},
                  json={"nachricht": "x"}).status_code == 401
    # der Pult-Schluessel oeffnet die Agent-Route nicht
    assert c.post(f"/api/agent/inhalte/{IID}/chat", headers={"X-Pult-Key": "pult-k"},
                  json={"nachricht": "x"}).status_code == 401
    assert db.sql == []


def test_newsletter_liste_nur_eigener_mandant_und_art(db, c):
    db.antworten = [[{"id": IID, "art": "newsletter", "titel": "Herbst", "status": "entwurf"}]]
    r = c.get("/api/agent/inhalte?firma=fin2gether&status=entwurf", headers=AGENT)
    assert r.status_code == 200 and r.json()["inhalte"][0]["titel"] == "Herbst"
    sql = db.sql[0]
    assert "'fin2gether'" in sql and "'newsletter'" in sql and "'entwurf'" in sql


def test_liste_ungueltiger_status(db, c):
    assert c.get("/api/agent/inhalte?status=quatsch", headers=AGENT).status_code == 422 and db.sql == []


def test_chat_stand_knapp(db, c):
    db.antworten = [[{"id": AID, "status": "fertig", "nachricht": "x", "antwort": "Erledigt", "fassung_nachher": 7}]]
    r = c.get(f"/api/agent/inhalte/{IID}/chat", headers=AGENT)
    assert r.status_code == 200 and r.json()["auftraege"][0]["antwort"] == "Erledigt"
    assert "marketing.chat_auftraege" in db.sql[0] and f"'{IID}'" in db.sql[0]
