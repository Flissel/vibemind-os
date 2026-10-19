"""Ton-Endpunkte (Podcast, 17.10.2026) ohne echte DB - FalscheDB wie test_bilder_api."""
import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import server
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

AID = "0123abcd-1111-1111-1111-111111111111"
BK, AK = "bild-k", "api-k"
SKRIPT = [{"sprecher": "Anna", "text": "Willkommen zur ersten Folge."},
          {"sprecher": "Anna", "text": "Heute geht es um den Herbst im Laden."}]
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x64" + b"\x00" * 2000


@pytest.fixture
def db(monkeypatch, tmp_path):
    f = FalscheDB()
    f.psql = FalschePsql(f)
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", f.psql)
    monkeypatch.setenv("MARKETING_BILD_KEY", BK)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", AK)
    f.ordner = tmp_path
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


A = {"X-API-Key": AK}
B = {"X-Bild-Key": BK}


def test_auftrag_anlegen(db, c):
    # ueber die DB-Funktion (Migration 070): ein INSERT geht in query_via_docker nicht (Unterabfrage)
    db.antworten = [[{"id": AID}]]
    r = c.post("/api/ton/auftraege", headers=A, json={"firma": "vibemind", "titel": "Herbst im Laden",
                                                       "skript": SKRIPT})
    assert r.status_code == 200 and r.json() == {"auftrag": AID}
    assert len(db.sql) == 1 and "marketing.ton_auftrag_anlegen(" in db.sql[0] and "'Herbst im Laden'" in db.sql[0]
    assert "INSERT" not in db.sql[0]


@pytest.mark.parametrize("koerper, grund", [
    ({"firma": "vibemind", "titel": "", "skript": SKRIPT}, "titel"),
    ({"firma": "vibemind", "titel": "T", "skript": []}, "skript"),
    ({"firma": "vibemind", "titel": "T", "skript": [{"sprecher": "A", "text": ""}]}, "text"),
    ({"firma": "vibemind", "titel": "T", "skript": [{"sprecher": "A", "text": "x" * 1001}]}, "text"),
    ({"firma": "vibemind", "titel": "T", "skript": [{"sprecher": "A<b>", "text": "x"}]}, "sprecher"),
    ({"firma": "vibemind", "titel": "T", "skript": SKRIPT, "sprache": "deutsch"}, "sprache"),
    ({"titel": "T", "skript": SKRIPT}, "firma"),
])
def test_auftrag_formen(db, c, koerper, grund):
    r = c.post("/api/ton/auftraege", headers=A, json=koerper)
    assert r.status_code == 422 and grund in r.json()["detail"].lower()
    assert not db.sql


def test_auftrag_unbekannte_firma(db, c):
    db.fehler = [RuntimeError("psql failed: ERROR:  Unbekannte firma\nCONTEXT: PL/pgSQL")]
    r = c.post("/api/ton/auftraege", headers=A, json={"firma": "gibtsnicht", "titel": "T", "skript": SKRIPT})
    assert r.status_code == 422 and r.json()["detail"] == "Unbekannte firma"


def test_auftraege_lesen(db, c):
    db.antworten = [[{"id": AID, "titel": "T", "status": "fertig", "datei": "pod-0123abcd-t.mp3"}]]
    r = c.get("/api/ton/auftraege?limit=5", headers=A)
    assert r.status_code == 200 and r.json()["auftraege"][0]["status"] == "fertig"


def test_ohne_api_key_401(db, c):
    assert c.post("/api/ton/auftraege", json={}).status_code == 401


def test_arbeiter_braucht_bild_key(db, c):
    assert c.post("/api/ton/arbeiter/naechster").status_code == 401
    assert c.post("/api/ton/arbeiter/naechster", headers={"X-Bild-Key": "falsch"}).status_code == 401


def test_arbeiter_naechster(db, c):
    db.antworten = [[{"a": {"id": AID, "titel": "T", "sprache": "de", "skript": SKRIPT, "mandant": "vibemind"}}]]
    r = c.post("/api/ton/arbeiter/naechster", headers=B)
    assert r.status_code == 200 and r.json()["auftrag"]["id"] == AID
    assert "marketing.ton_naechster(" in db.sql[0]


def test_arbeiter_datei_und_fertig(db, c):
    db.antworten = [[{"titel": "Herbst im Laden!", "mandant": "vibemind", "status": "in_arbeit"}]]
    r = c.post(f"/api/ton/arbeiter/{AID}/datei", headers=B, content=MP3)
    assert r.status_code == 200
    name = r.json()["name"]
    assert name == "pod-0123abcd-herbst-im-laden.mp3" and (db.ordner / name).read_bytes() == MP3
    assert any("medien_mandant" in s and "'vibemind'" in s for s in db.psql.sql)
    db.antworten = [[{"v": "fertig"}]]
    r = c.post(f"/api/ton/arbeiter/{AID}/fertig", headers=B, json={"datei": name, "dauer_s": 42.5})
    assert r.status_code == 200 and r.json() == {"status": "fertig"}
    assert "marketing.ton_abschliessen(" in db.sql[-1] and name in db.sql[-1]


def test_arbeiter_datei_lehnt_kein_mp3_ab(db, c):
    r = c.post(f"/api/ton/arbeiter/{AID}/datei", headers=B, content=b"<html>kein ton</html>")
    assert r.status_code == 422


def test_arbeiter_fertig_mit_fremder_datei(db, c):
    r = c.post(f"/api/ton/arbeiter/{AID}/fertig", headers=B, json={"datei": "pod-ffffffff-x.mp3", "dauer_s": 1})
    assert r.status_code == 422


def test_arbeiter_fehler_melden(db, c):
    db.antworten = [[{"v": "fehler"}]]
    r = c.post(f"/api/ton/arbeiter/{AID}/fertig", headers=B, json={"befund": "Sprech-Dienst nicht erreichbar"})
    assert r.status_code == 200 and r.json() == {"status": "fehler"}
    assert "NULL" in db.sql[-1]


# Eigener Agent-Schluessel (17.10.2026, User-Entscheid): am PC hat die API keinen MARKETING_API_KEY; die
# Agent-Routen (Newsletter-Bilder, Podcast) verlangen dann X-Agent-Key = MARKETING_AGENT_KEY. Ohne beide
# Schluessel bleiben sie gesperrt (fail-closed) - kein Loopback-Durchlass (Sicherheitspruefung 17.10.).

def test_agent_key_oeffnet_ohne_globalen_schluessel(db, c, monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "")
    monkeypatch.setenv("MARKETING_AGENT_KEY", "agent-k")
    db.antworten = [[{"id": "x"}]]
    assert c.get("/api/ton/auftraege", headers={"X-Agent-Key": "agent-k"}).status_code == 200


@pytest.mark.parametrize("kopf", [{}, {"X-Agent-Key": "falsch"}])
def test_falscher_oder_fehlender_agent_key_401(db, c, monkeypatch, kopf):
    monkeypatch.setattr(server, "API_KEY", "")
    monkeypatch.setenv("MARKETING_AGENT_KEY", "agent-k")
    assert c.get("/api/ton/auftraege", headers=kopf).status_code == 401
    assert not db.sql


def test_ohne_jeden_schluessel_503(db, c, monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "")
    monkeypatch.delenv("MARKETING_AGENT_KEY", raising=False)
    r = c.get("/api/ton/auftraege", headers={"X-Agent-Key": "irgendwas"})
    assert r.status_code == 503 and "MARKETING_AGENT_KEY" in r.json()["detail"]


# --- Pult (Betreiber, Migration 074) ------------------------------------------

P = {"X-Pult-Key": "pult-k"}


@pytest.fixture
def pult(db, monkeypatch):
    monkeypatch.setenv("MARKETING_PULT_KEY", "pult-k")
    return db


def test_pult_liste_filtert_mandant_und_liefert_skript(pult, c):
    pult.antworten = [[{"id": AID, "titel": "T", "status": "offen", "angelegt_von": "betreiber"}]]
    r = c.get("/api/pult/ton?mandant=vibemind&limit=10", headers=P)
    assert r.status_code == 200 and r.json()["auftraege"][0]["angelegt_von"] == "betreiber"
    assert "WHERE mandant = 'vibemind'" in pult.sql[-1] and "skript" in pult.sql[-1]
    assert "ORDER BY erstellt_am DESC" in pult.sql[-1]


def test_pult_liste_ohne_schluessel_abgewiesen(pult, c):
    assert c.get("/api/pult/ton?mandant=vibemind").status_code == 401
    assert c.post("/api/pult/ton", json={}).status_code == 401
    assert c.post(f"/api/pult/ton/{AID}/abbrechen").status_code == 401
    assert not pult.sql


@pytest.mark.parametrize("mandant", ["../x", "Vibemind"])
def test_pult_liste_mandant_ungueltig_422(pult, c, mandant):
    r = c.get("/api/pult/ton", params={"mandant": mandant}, headers=P)
    assert r.status_code == 422 and not pult.sql


def test_pult_anlegen_nutzt_betreiber_und_prueft_skript(pult, c):
    pult.antworten = [[{"id": AID}]]
    r = c.post("/api/pult/ton", headers=P, json={"mandant": "vibemind", "titel": "Folge", "skript": SKRIPT})
    assert r.status_code == 200 and r.json() == {"auftrag": AID}
    assert "ton_auftrag_anlegen_von(" in pult.sql[-1] and "'betreiber'" in pult.sql[-1]
    assert "'vibemind'" in pult.sql[-1]
    n = len(pult.sql)
    r = c.post("/api/pult/ton", headers=P, json={"mandant": "vibemind", "titel": "Folge",
                                                  "skript": [{"sprecher": "A", "text": "x" * 1001}]})
    assert r.status_code == 422 and len(pult.sql) == n


def test_pult_abbrechen_ruft_funktion(pult, c):
    pult.antworten = [[{"status": "offen"}], [{"s": "abgebrochen"}]]
    r = c.post(f"/api/pult/ton/{AID}/abbrechen", headers=P)
    assert r.status_code == 200 and r.json() == {"status": "abgebrochen"}
    assert "ton_abbrechen(" in pult.sql[-1]


def test_pult_abbrechen_ungueltige_id_404(pult, c):
    assert c.post("/api/pult/ton/gibtsnicht/abbrechen", headers=P).status_code == 404
    assert not pult.sql


def test_pult_abbrechen_unbekannte_folge_404(pult, c):
    pult.antworten = [[]]
    r = c.post(f"/api/pult/ton/{AID}/abbrechen", headers=P)
    assert r.status_code == 404 and len(pult.sql) == 1


@pytest.mark.parametrize("status", ["fertig", "fehler", "abgebrochen"])
def test_pult_abbrechen_nicht_mehr_offen_409(pult, c, status):
    pult.antworten = [[{"status": status}]]
    r = c.post(f"/api/pult/ton/{AID}/abbrechen", headers=P)
    assert r.status_code == 409 and r.json()["detail"] == "Folge ist nicht mehr offen"
    assert len(pult.sql) == 1 and "ton_abbrechen(" not in pult.sql[0]


def test_pult_abbrechen_rennen_wird_409(pult, c):
    pult.antworten = [[{"status": "offen"}]]
    pult.fehler = [None, RuntimeError("psql failed: ERROR:  Auftrag ist nicht mehr offen\nCONTEXT: PL/pgSQL")]
    r = c.post(f"/api/pult/ton/{AID}/abbrechen", headers=P)
    assert r.status_code == 409