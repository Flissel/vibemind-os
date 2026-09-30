"""Bild-Endpunkte ohne echte DB (FalscheDB wie test_pult_api)."""
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from spaces.marketing.api import server
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_pult_api import FalscheDB

IID = "11111111-1111-1111-1111-111111111111"
AID = "0123abcd-1111-1111-1111-111111111111"
PK, BK, AK = "pult-k", "bild-k", "api-k"


def jpeg(w=320, h=160) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (15, 36, 34)).save(b, "JPEG", quality=80)
    return b.getvalue()


@pytest.fixture
def db(monkeypatch, tmp_path):
    f = FalscheDB()
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setenv("MARKETING_PULT_KEY", PK)
    monkeypatch.setenv("MARKETING_BILD_KEY", BK)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", AK)
    f.ordner = tmp_path
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


def test_pult_auftrag_anlegen(db, c):
    db.antworten = [[{"id": "a-neu"}]]
    r = c.post(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK},
               json={"platz": "kopf", "hinweis": "waermer"})
    assert r.status_code == 200 and r.json() == {"auftrag": "a-neu"}
    assert "marketing.pult_bild_auftrag(" in db.sql[0]
    assert "'kopf'" in db.sql[0] and "'waermer'" in db.sql[0] and "'mensch'" in db.sql[0] and "false" in db.sql[0]


def test_pult_auftrag_formen(db, c):
    h = {"X-Pult-Key": PK}
    assert c.post(f"/api/pult/inhalte/{IID}/bilder", headers=h, json={"platz": "a b"}).status_code == 422
    assert c.post(f"/api/pult/inhalte/{IID}/bilder", headers=h, json={"hinweis": 5}).status_code == 422
    assert c.post(f"/api/pult/inhalte/{IID}/bilder", headers=h, json={"hinweis": "x" * 501}).status_code == 422
    assert c.post(f"/api/pult/inhalte/{IID}/bilder", headers=h, json={"nur_leere": "ja"}).status_code == 422
    assert c.post(f"/api/pult/inhalte/{IID}/bilder", json={}).status_code == 401
    assert db.sql == []


def test_pult_auftrag_db_grund_wird_422(db, c):
    db.fehler = [RuntimeError("psql: ERROR:  Bildplatz kopf gibt es in der gespeicherten Fassung nicht - erst speichern")]
    r = c.post(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK}, json={"platz": "kopf"})
    assert r.status_code == 422 and "erst speichern" in r.json()["detail"]


def test_pult_stand(db, c):
    db.antworten = [[{"id": "a1", "platz": None, "status": "offen"}]]
    r = c.get(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK})
    assert r.status_code == 200 and r.json()["auftraege"][0]["id"] == "a1"
    assert "ORDER BY erstellt_am DESC LIMIT 30" in db.sql[0]


def test_agent_braucht_api_key_und_nennt_plaetze(db, c):
    assert c.get(f"/api/bilder/agent/{IID}/plaetze").status_code == 401
    doc = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["k"]}},
           "k": {"type": "Image", "data": {"style": {"padding": {"left": 0, "right": 0}},
                                          "props": {"url": "medien:platzhalter-2x1.png", "width": 600, "height": 300}}}}
    db.antworten = [[{"fassung": 3, "bloecke": doc}], []]
    r = c.get(f"/api/bilder/agent/{IID}/plaetze", headers={"X-API-Key": AK})
    assert r.status_code == 200
    j = r.json()
    assert j["fassung"] == 3 and j["plaetze"][0]["id"] == "k" and j["plaetze"][0]["leer"] is True


def test_agent_auftrag_urheber_agent(db, c):
    db.antworten = [[{"id": "a2"}]]
    r = c.post(f"/api/bilder/agent/{IID}/auftrag", headers={"X-API-Key": AK}, json={"nur_leere": True})
    assert r.status_code == 200 and "'agent'" in db.sql[0] and "NULL" in db.sql[0]


def test_arbeiter_schluessel(db, c, monkeypatch):
    assert c.post("/api/bilder/arbeiter/naechster").status_code == 401
    assert c.post("/api/bilder/arbeiter/naechster", headers={"X-Bild-Key": "falsch"}).status_code == 401
    # X-API-Key-Middleware darf den Arbeiter nicht zusaetzlich sperren
    db.antworten = [[{"a": None}]]
    assert c.post("/api/bilder/arbeiter/naechster", headers={"X-Bild-Key": BK}).json() == {"auftrag": None}
    monkeypatch.delenv("MARKETING_BILD_KEY")
    assert c.post("/api/bilder/arbeiter/naechster", headers={"X-Bild-Key": BK}).status_code == 503


def test_bild_ablegen(db, c):
    db.antworten = [[{"f": None}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf", headers={"X-Bild-Key": BK}, content=jpeg())
    assert r.status_code == 200 and r.json() == {"name": "nl-0123abcd-kopf.jpg"}
    datei = db.ordner / "nl-0123abcd-kopf.jpg"
    assert datei.read_bytes()[:3] == b"\xff\xd8\xff"
    assert "marketing.pult_bild_datei_fehler(" in db.sql[0]


@pytest.mark.parametrize("rumpf,platz,code", [
    (b"\x89PNG\r\n\x1a\n" + b"0" * 100, "kopf", 422),           # kein JPEG
    (b"\xff\xd8\xff" + b"0" * (1024 * 1024), "kopf", 413),      # zu gross
    (jpeg(40, 40), "kopf", 422),                                # zu klein
    (jpeg(), "../x", 422),                                      # Pfadtrick
    (jpeg(), "", 422),
], ids=["kein-jpeg", "zu-gross", "zu-klein", "pfadtrick", "platz-leer"])
def test_bild_ablehnen(db, c, rumpf, platz, code):
    db.antworten = [[{"f": None}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz={platz}", headers={"X-Bild-Key": BK}, content=rumpf)
    assert r.status_code == code
    assert list(db.ordner.iterdir()) == []


def test_bild_db_verweigert(db, c):
    db.antworten = [[{"f": "Auftrag ist nicht (mehr) in Arbeit"}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf", headers={"X-Bild-Key": BK}, content=jpeg())
    assert r.status_code == 422 and "nicht (mehr) in Arbeit" in r.json()["detail"]
    assert list(db.ordner.iterdir()) == []


def test_ohne_ordner_503(db, c, monkeypatch):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(db.ordner / "gibtsnicht"))
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf", headers={"X-Bild-Key": BK}, content=jpeg())
    assert r.status_code == 503


def test_fertig_prueft_namen_und_datei(db, c):
    (db.ordner / "nl-0123abcd-kopf.jpg").write_bytes(jpeg())
    db.antworten = [[{"e": {"fassung": 4, "eingesetzt": ["kopf"], "uebersprungen": []}}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/fertig", headers={"X-Bild-Key": BK},
               json={"ergebnis": {"kopf": "nl-0123abcd-kopf.jpg"}, "befund": ""})
    assert r.status_code == 200 and r.json()["fassung"] == 4
    assert "medien:nl-0123abcd-kopf.jpg" in db.sql[0]
    # Datei fehlt / fremder Name -> 422 ohne DB
    db.sql.clear()
    assert c.post(f"/api/bilder/arbeiter/{AID}/fertig", headers={"X-Bild-Key": BK},
                  json={"ergebnis": {"kopf": "nl-0123abcd-weg.jpg"}}).status_code == 422
    assert c.post(f"/api/bilder/arbeiter/{AID}/fertig", headers={"X-Bild-Key": BK},
                  json={"ergebnis": {"kopf": "../../etc/passwd"}}).status_code == 422
    assert db.sql == []


def test_zurueck_und_weiter(db, c):
    db.antworten = [[{"s": "offen"}], [{"ok": True}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/zurueck", headers={"X-Bild-Key": BK},
               json={"befund": "ComfyUI laeuft nicht", "endgueltig": False})
    assert r.json() == {"status": "offen"} and "false" in db.sql[0]
    assert c.post(f"/api/bilder/arbeiter/{AID}/weiter", headers={"X-Bild-Key": BK}).json() == {"ok": True}
    assert "'10 minutes'" in db.sql[1]


def test_agent_routen_ohne_api_key_503(db, c, monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "")
    r = c.get(f"/api/bilder/agent/{IID}/plaetze")
    assert r.status_code == 503 and "MARKETING_API_KEY" in r.json()["detail"]
    r = c.post(f"/api/bilder/agent/{IID}/auftrag", json={"nur_leere": True})
    assert r.status_code == 503 and "MARKETING_API_KEY" in r.json()["detail"]
    assert db.sql == []


def test_platz_mit_zeilenumbruch_422(db, c):
    db.antworten = [[{"f": None}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf%0A", headers={"X-Bild-Key": BK}, content=jpeg())
    assert r.status_code == 422
    r = c.post(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK}, json={"platz": "kopf\n"})
    assert r.status_code == 422
    assert list(db.ordner.iterdir()) == [] and db.sql == []


def test_fertig_name_mit_zeilenumbruch_422(db, c):
    (db.ordner / "nl-0123abcd-kopf.jpg").write_bytes(jpeg())
    r = c.post(f"/api/bilder/arbeiter/{AID}/fertig", headers={"X-Bild-Key": BK},
               json={"ergebnis": {"kopf": "nl-0123abcd-kopf.jpg\n"}, "befund": ""})
    assert r.status_code == 422 and db.sql == []


def test_abgeschnittenes_jpeg_422(db, c):
    ganz = jpeg(640, 320)
    db.antworten = [[{"f": None}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf", headers={"X-Bild-Key": BK},
               content=ganz[: len(ganz) // 2])
    assert r.status_code == 422
    assert list(db.ordner.iterdir()) == []


def test_ordner_nicht_beschreibbar_503(db, c, monkeypatch):
    from spaces.marketing.api import bilder
    monkeypatch.setattr(bilder.os, "access", lambda pfad, modus: False)
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf", headers={"X-Bild-Key": BK}, content=jpeg())
    assert r.status_code == 503 and "misconfigured" in r.json()["detail"]


def test_schreibfehler_raeumt_teil_datei_weg_503(db, c, monkeypatch):
    from spaces.marketing.api import bilder

    def kaputt(*_a, **_k):
        raise OSError("Datentraeger voll")
    monkeypatch.setattr(bilder.os, "replace", kaputt)
    db.antworten = [[{"f": None}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf", headers={"X-Bild-Key": BK}, content=jpeg())
    assert r.status_code == 503 and r.json()["detail"] == "Bild konnte nicht abgelegt werden"
    assert list(db.ordner.iterdir()) == []
