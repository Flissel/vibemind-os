"""Bild-Endpunkte ohne echte DB (FalscheDB wie test_pult_api)."""
import io
import json
import os

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


def test_auftrag_mit_staerke_und_modus(db, c):
    db.antworten = [[{"id": "a9"}]]
    r = c.post(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK},
               json={"platz": "kopf", "hinweis": "waermer", "staerke": 30, "modus": "ueberarbeiten"})
    assert r.status_code == 200
    assert ", 30, 'ueberarbeiten') AS id" in db.sql[0]


def test_auftrag_standard_55_ueberarbeiten(db, c):
    db.antworten = [[{"id": "a9"}]]
    c.post(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK}, json={"platz": "kopf"})
    assert ", 55, 'ueberarbeiten') AS id" in db.sql[0]


@pytest.mark.parametrize("body", [{"staerke": 101}, {"staerke": -1}, {"staerke": True}, {"staerke": "55"},
                                  {"staerke": 5.5}, {"modus": "malen"}, {"modus": 1}])
def test_auftrag_formen_staerke_modus(db, c, body):
    assert c.post(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK}, json=body).status_code == 422
    assert db.sql == []


def test_stand_liefert_staerke_modus_messung(db, c):
    db.antworten = [[{"id": "a1"}]]
    c.get(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK})
    assert "staerke, modus, messung" in db.sql[0]


def test_naechster_ergaenzt_staerke_modus(db, c):
    db.antworten = [[{"a": {"id": AID, "platz": None}}], [{"staerke": 40, "modus": "ueberarbeiten"}]]
    a = c.post("/api/bilder/arbeiter/naechster", headers={"X-Bild-Key": BK}).json()["auftrag"]
    assert a["staerke"] == 40 and a["modus"] == "ueberarbeiten"


def test_quelle_menschen_ordner_zuerst(db, c, monkeypatch, tmp_path):
    mensch = tmp_path / "media"
    mensch.mkdir()
    (mensch / "eigen.png").write_bytes(b"\x89PNG\r\n\x1a\nMENSCH")
    (db.ordner / "eigen.png").write_bytes(b"\x89PNG\r\n\x1a\nSYSTEM")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(mensch))
    db.antworten = [[{"f": None}], [{"url": "medien:eigen.png"}]]
    r = c.get(f"/api/bilder/arbeiter/{AID}/quelle?platz=kopf", headers={"X-Bild-Key": BK})
    assert r.status_code == 200 and r.content.endswith(b"MENSCH") and r.headers["content-type"] == "image/png"


def test_quelle_aus_media_erzeugt(db, c):
    (db.ordner / "nl-0123abcd-kopf.jpg").write_bytes(jpeg())
    db.antworten = [[{"f": None}], [{"url": "medien:nl-0123abcd-kopf.jpg"}]]
    r = c.get(f"/api/bilder/arbeiter/{AID}/quelle?platz=kopf", headers={"X-Bild-Key": BK})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("url,code", [("medien:platzhalter-2x1.png", 404), ("", 404),
                                      ("medien:../../etc/passwd", 404), ("medien:weg.jpg", 404),
                                      ("https://x.de/a.jpg", 404)])
def test_quelle_ablehnen(db, c, url, code):
    db.antworten = [[{"f": None}], [{"url": url}]]
    assert c.get(f"/api/bilder/arbeiter/{AID}/quelle?platz=kopf", headers={"X-Bild-Key": BK}).status_code == code


def test_quelle_schluessel_und_auftrag(db, c):
    assert c.get(f"/api/bilder/arbeiter/{AID}/quelle?platz=kopf").status_code == 401
    db.antworten = [[{"f": "Platz gehoert nicht zu diesem Auftrag"}]]
    r = c.get(f"/api/bilder/arbeiter/{AID}/quelle?platz=kopf", headers={"X-Bild-Key": BK})
    assert r.status_code == 422 and "gehoert nicht" in r.json()["detail"]
    assert c.get(f"/api/bilder/arbeiter/{AID}/quelle?platz=kopf%0A", headers={"X-Bild-Key": BK}).status_code == 422


def test_fertig_mit_messung(db, c):
    (db.ordner / "nl-0123abcd-kopf.jpg").write_bytes(jpeg())
    db.antworten = [[{"e": {"fassung": 5}}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/fertig", headers={"X-Bild-Key": BK},
               json={"ergebnis": {"kopf": "nl-0123abcd-kopf.jpg"}, "befund": "",
                     "messung": {"kopf": {"aehnlich_original": 0.82, "naeher_am_hinweis": None}}})
    assert r.status_code == 200 and '"aehnlich_original": 0.82' in db.sql[0] and "::jsonb, '', " in db.sql[0]


@pytest.mark.parametrize("messung", [{"kopf": {"aehnlich_original": 2}}, {"kopf": {"x": 0.1}},
                                     {"fremd": {"aehnlich_original": 0.5}}, {"kopf": {"aehnlich_original": True}},
                                     [1], {"kopf": 0.5}])
def test_fertig_messung_ablehnen(db, c, messung):
    (db.ordner / "nl-0123abcd-kopf.jpg").write_bytes(jpeg())
    r = c.post(f"/api/bilder/arbeiter/{AID}/fertig", headers={"X-Bild-Key": BK},
               json={"ergebnis": {"kopf": "nl-0123abcd-kopf.jpg"}, "befund": "", "messung": messung})
    assert r.status_code == 422 and db.sql == []


@pytest.mark.parametrize("name,typ", [("Team Foto (1).jpg", "image/jpeg"), ("Grüße.png", "image/png"),
                                      ("Bild.WEBP", "image/webp")])
def test_quelle_menschen_namen_wie_sales_ui(db, c, monkeypatch, tmp_path, name, typ):
    mensch = tmp_path / "media"
    mensch.mkdir()
    (mensch / name).write_bytes(b"MENSCH")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(mensch))
    db.antworten = [[{"f": None}], [{"url": "medien:" + name}]]
    r = c.get(f"/api/bilder/arbeiter/{AID}/quelle?platz=kopf", headers={"X-Bild-Key": BK})
    assert r.status_code == 200 and r.content == b"MENSCH" and r.headers["content-type"] == typ


@pytest.mark.parametrize("name", [".versteckt.png", "a/b.png", "a\\b.png", "c:b.png", "a\x00.png", "a..b.png",
                                  "bild.svg", "bild", ".."])
def test_quelle_menschen_namen_ablehnen(db, c, monkeypatch, tmp_path, name):
    mensch = tmp_path / "media"
    (mensch / "a").mkdir(parents=True)
    (mensch / ".versteckt.png").write_bytes(b"X")
    (mensch / "a" / "b.png").write_bytes(b"X")
    (mensch / "bild.svg").write_bytes(b"X")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(mensch))
    db.antworten = [[{"f": None}], [{"url": "medien:" + name}]]
    assert c.get(f"/api/bilder/arbeiter/{AID}/quelle?platz=kopf", headers={"X-Bild-Key": BK}).status_code == 404


def test_quelle_symlink_nach_draussen_404(db, c, monkeypatch, tmp_path):
    mensch = tmp_path / "media"
    mensch.mkdir()
    draussen = tmp_path / "geheim.png"
    draussen.write_bytes(b"GEHEIM")
    try:
        os.symlink(draussen, mensch / "link.png")
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("os.symlink hier nicht verfuegbar (Windows ohne Symlink-Recht)")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(mensch))
    db.antworten = [[{"f": None}], [{"url": "medien:link.png"}]]
    assert c.get(f"/api/bilder/arbeiter/{AID}/quelle?platz=kopf", headers={"X-Bild-Key": BK}).status_code == 404


# ---- Freistellen (Task 2) -------------------------------------------------

def png(alpha=True, groesse=(64, 64)) -> bytes:
    b = io.BytesIO()
    Image.new("RGBA" if alpha else "RGB", groesse, (10, 20, 30, 0) if alpha else (10, 20, 30)).save(b, "PNG")
    return b.getvalue()


def _png_hoch(roh, platz="kopf_bild", extra=""):
    return roh, f"/api/bilder/arbeiter/{AID}/bild?platz={platz}&format=png{extra}"


def test_png_upload_frei(db, c):
    db.antworten = [[{"f": None}]]
    daten, url = _png_hoch(png())
    r = c.post(url, headers={"X-Bild-Key": BK}, content=daten)
    assert r.status_code == 200 and r.json() == {"name": "nl-0123abcd-kopf_bild-frei.png"}
    assert (db.ordner / "nl-0123abcd-kopf_bild-frei.png").read_bytes()[:4] == b"\x89PNG"
    assert not (db.ordner / "nl-0123abcd-kopf_bild-frei.png.teil").exists()


@pytest.mark.parametrize("roh", [b"\xff\xd8\xff" + b"x" * 50, b"\x89PNGkaputt", "ohne_alpha"],
                         ids=["jpeg-als-png", "kaputt", "ohne-alpha"])
def test_png_upload_abgelehnt(db, c, roh):
    db.antworten = [[{"f": None}]]
    daten = png(alpha=False) if roh == "ohne_alpha" else roh
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf_bild&format=png",
               headers={"X-Bild-Key": BK}, content=daten)
    assert r.status_code == 422
    assert list(db.ordner.iterdir()) == []


def test_png_zu_gross(db, c):
    db.antworten = [[{"f": None}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf_bild&format=png", headers={"X-Bild-Key": BK},
               content=b"\x89PNG" + b"0" * (4 * 1024 * 1024 + 1))
    assert r.status_code == 413 and "4 MB" in r.json()["detail"]
    assert list(db.ordner.iterdir()) == []


def test_png_ueber_1mb_ok_wenn_unter_4mb(db, c):
    # JPEG-Grenze (1 MB) gilt nicht fuer PNG: inkompressibles RGBA-PNG ~1,5 MB
    b = io.BytesIO()
    Image.frombytes("RGBA", (600, 600), os.urandom(600 * 600 * 4)).save(b, "PNG")
    roh = b.getvalue()
    assert 1024 * 1024 < len(roh) < 4 * 1024 * 1024
    db.antworten = [[{"f": None}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf_bild&format=png",
               headers={"X-Bild-Key": BK}, content=roh)
    assert r.status_code == 200


def test_jpeg_weg_unveraendert_format_jpg_explizit(db, c):
    db.antworten = [[{"f": None}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf&format=jpg", headers={"X-Bild-Key": BK}, content=jpeg())
    assert r.status_code == 200 and r.json() == {"name": "nl-0123abcd-kopf.jpg"}
    assert (db.ordner / "nl-0123abcd-kopf.jpg").read_bytes()[:3] == b"\xff\xd8\xff"


def test_jpeg_zu_gross_bleibt_1mb(db, c):
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf&format=jpg", headers={"X-Bild-Key": BK},
               content=b"\xff\xd8\xff" + b"0" * (1024 * 1024))
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]


def test_png_in_jpeg_weg_bleibt_abgelehnt(db, c):
    db.antworten = [[{"f": None}]]
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf", headers={"X-Bild-Key": BK}, content=png())
    assert r.status_code == 422 and list(db.ordner.iterdir()) == []


def test_unbekanntes_format_422(db, c):
    r = c.post(f"/api/bilder/arbeiter/{AID}/bild?platz=kopf&format=gif", headers={"X-Bild-Key": BK}, content=jpeg())
    assert r.status_code == 422 and db.sql == []


def _doc(url="medien:nl-1234abcd-kopf_bild.jpg", grafik=False, bid="t1_bild"):
    props = {"url": url, "width": 600, "height": 300}
    if grafik:
        props["grafik"] = True
    return {"root": {"type": "EmailLayout", "data": {"childrenIds": [bid]}},
            bid: {"type": "Image", "data": {"style": {"padding": {"left": 0, "right": 0}}, "props": props}}}


def test_freistellen_echtes_bild_legt_auftrag_an(db, c):
    db.antworten = [[{"bloecke": _doc()}], [{"id": "a-frei"}]]
    r = c.post(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK},
               json={"platz": "t1_bild", "modus": "freistellen", "staerke": 80, "nur_leere": True})
    assert r.status_code == 200 and r.json() == {"auftrag": "a-frei"}
    assert "marketing.inhalt_fassungen" in db.sql[0] and "ORDER BY fassung DESC LIMIT 1" in db.sql[0]
    assert "marketing.pult_bild_auftrag(" in db.sql[1]
    assert "'t1_bild'" in db.sql[1] and ", false, " in db.sql[1]
    assert ", 0, 'freistellen') AS id" in db.sql[1]


@pytest.mark.parametrize("doc", [
    _doc(url="medien:platzhalter-2x1.png"),
    _doc(url=""),
    _doc(grafik=True),
    _doc(bid="anderer"),
], ids=["platzhalter", "leer", "grafik", "platz-fehlt"])
def test_freistellen_ohne_echtes_bild_422(db, c, doc):
    db.antworten = [[{"bloecke": doc}], [{"id": "darf-nicht"}]]
    r = c.post(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK},
               json={"platz": "t1_bild", "modus": "freistellen"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Freistellen geht nur bei einem Bildplatz mit echtem Bild"
    assert not any("pult_bild_auftrag" in s for s in db.sql)


def test_freistellen_ohne_platz_422(db, c):
    r = c.post(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK}, json={"modus": "freistellen"})
    assert r.status_code == 422 and db.sql == []


def test_freistellen_unbekannter_inhalt_404(db, c):
    db.antworten = [[]]
    r = c.post(f"/api/pult/inhalte/{IID}/bilder", headers={"X-Pult-Key": PK},
               json={"platz": "t1_bild", "modus": "freistellen"})
    assert r.status_code == 404
