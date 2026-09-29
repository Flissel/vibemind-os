"""Pult-Router der Marketing-API (Spec §3.4). Ohne echte Datenbank: _db wird
gefaelscht; geprueft werden Schluesselpflicht, Formen und Weitergabe an die
DB-Funktionen aus 050/051.

Abweichung vom Brief (task-3-brief.md): 051 aenderte marketing.pult_entscheiden
auf die 5-arg-Form (p_inhalt, p_fassung, p_urteil, p_von, p_grund) -- die
4-arg-Form gibt es nicht mehr. test_entscheiden schickt deshalb "fassung" im
Body und prueft dessen Weitergabe als 2. SQL-Argument; zusaetzlich prueft
test_entscheiden_ohne_gueltige_fassung_422, dass eine fehlende/ungueltige
fassung mit 422 ohne DB-Zugriff abgewiesen wird."""
import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import pult, server
from spaces.marketing.sync import _db

KEY = "test-pult-key"
GESTALT = {"grund": "#0f2422", "text": "#cfe3df", "akzent": "#5eead4",
           "flaeche": "#1d3b39", "text_hell": "#e9fbf6", "text_leise": "#8aa3a0",
           "gold": "#fbbf24", "handlung_text": "#0f2422"}
FELDER = {"betreff": "B", "vorschautext": "", "abschnitte": [{"titel": "", "text": "T"}],
          "knopf_text": "", "knopf_link": ""}


class FalscheDB:
    def __init__(self):
        self.sql = []
        self.antworten = []          # Liste von Listen (Zeilen) in Aufrufreihenfolge

    def query(self, sql, params=None, container=None, streng=False):
        self.sql.append(sql)
        return self.antworten.pop(0) if self.antworten else []

    def one(self, sql, params=None, container=None, streng=False):
        zeilen = self.query(sql, params, container, streng)
        return zeilen[0] if zeilen else None


@pytest.fixture
def db(monkeypatch):
    f = FalscheDB()
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setenv("MARKETING_PULT_KEY", KEY)
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


H = {"X-Pult-Key": KEY}


def test_ohne_schluessel_401(db, c):
    assert c.get("/api/pult/inhalte").status_code == 401
    assert c.get("/api/pult/inhalte", headers={"X-Pult-Key": "falsch"}).status_code == 401
    assert db.sql == []                          # nichts erreicht die DB


def test_ohne_konfiguration_503(monkeypatch, c):
    monkeypatch.delenv("MARKETING_PULT_KEY", raising=False)
    assert c.get("/api/pult/inhalte", headers=H).status_code == 503


def test_inhalte_liste(db, c):
    db.antworten = [[{"id": "a1", "art": "newsletter", "titel": "X", "status": "entwurf",
                      "erstellt_am": "2026-09-04", "fassungen": 1, "layout": "dunkel"}]]
    r = c.get("/api/pult/inhalte?mandant=vibemind&status=entwurf", headers=H)
    assert r.status_code == 200 and r.json()["inhalte"][0]["id"] == "a1"
    assert "mandant = 'vibemind'" in db.sql[0] and "status = 'entwurf'" in db.sql[0]


def test_filter_wird_nicht_blind_eingesetzt(db, c):
    r = c.get("/api/pult/inhalte?art=newsletter';drop table x;--", headers=H)
    assert r.status_code == 422 and db.sql == []


def test_fassung_speichern_ruft_db_funktion(db, c):
    db.antworten = [[{"fassung": 5}]]
    r = c.post("/api/pult/inhalte/11111111-1111-1111-1111-111111111111/fassungen",
               headers=H, json={"felder": FELDER, "layout": "dunkel", "von": "felix"})
    assert r.status_code == 200 and r.json() == {"fassung": 5}
    assert "marketing.pult_fassung_speichern(" in db.sql[0] and "'betreiber'" in db.sql[0]


def test_ungueltige_id_404_ohne_db(db, c):
    assert c.get("/api/pult/inhalte/keine-uuid", headers=H).status_code == 404
    assert db.sql == []


def test_db_ablehnung_wird_422_mit_grund(db, c, monkeypatch):
    def wirft(*a, **k):
        raise RuntimeError("ERROR:  Nur Entwuerfe lassen sich bearbeiten")
    monkeypatch.setattr(_db, "query_one", wirft)
    r = c.post("/api/pult/inhalte/11111111-1111-1111-1111-111111111111/fassungen",
               headers=H, json={"felder": FELDER, "layout": "dunkel", "von": "felix"})
    assert r.status_code == 422 and "Nur Entwuerfe" in r.json()["detail"]


def test_vorschau_html_und_pdf(db, c):
    iid = "11111111-1111-1111-1111-111111111111"
    zeile = {"felder": FELDER, "gestalt": GESTALT,
             "pflichtteil": {"impressum": "I", "abmelde_hinweis": "A"}}
    db.antworten = [[zeile]]
    r = c.get(f"/api/pult/inhalte/{iid}/vorschau?fassung=1&format=mail", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    db.antworten = [[zeile]]
    r = c.get(f"/api/pult/inhalte/{iid}/vorschau?fassung=1&format=pdf", headers=H)
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")


def test_layout_vorschau_prueft_gestalt(db, c):
    db.antworten = [[{"fehler": "rundung muss eine Zahl von 0 bis 24 sein"}]]
    r = c.post("/api/pult/layouts/vorschau", headers=H,
               json={"gestalt": dict(GESTALT, rundung=999), "mandant": "vibemind", "format": "mail"})
    assert r.status_code == 422 and "rundung" in r.json()["detail"]


def test_layout_vorschau_nutzt_beispiel(db, c):
    db.antworten = [[{"fehler": None}], [{"pflichtteil": {"impressum": "I", "abmelde_hinweis": ""}}]]
    r = c.post("/api/pult/layouts/vorschau", headers=H,
               json={"gestalt": GESTALT, "mandant": "vibemind", "format": "handy"})
    assert r.status_code == 200 and "Neuigkeiten aus der Werkstatt" in r.text
    assert 'width="380"' in r.text


def test_entscheiden(db, c):
    db.antworten = [[{"status": "freigegeben"}]]
    r = c.post("/api/pult/inhalte/11111111-1111-1111-1111-111111111111/entscheiden",
               headers=H, json={"fassung": 2, "urteil": "freigeben", "von": "felix", "grund": ""})
    assert r.json() == {"status": "freigegeben"}
    assert "marketing.pult_entscheiden(" in db.sql[0] and ", 2, " in db.sql[0]


def test_entscheiden_ohne_gueltige_fassung_422(db, c):
    r = c.post("/api/pult/inhalte/11111111-1111-1111-1111-111111111111/entscheiden",
               headers=H, json={"urteil": "freigeben", "von": "felix", "grund": ""})
    assert r.status_code == 422 and db.sql == []
    r = c.post("/api/pult/inhalte/11111111-1111-1111-1111-111111111111/entscheiden",
               headers=H, json={"fassung": 0, "urteil": "freigeben", "von": "felix", "grund": ""})
    assert r.status_code == 422 and db.sql == []
    r = c.post("/api/pult/inhalte/11111111-1111-1111-1111-111111111111/entscheiden",
               headers=H, json={"fassung": "zwei", "urteil": "freigeben", "von": "felix", "grund": ""})
    assert r.status_code == 422 and db.sql == []
