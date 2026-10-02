"""Gestaltungs-Route ohne echte DB (FalscheDB wie test_pult_api)."""
import os

import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import server
from spaces.marketing.claw import bildplaetze
from spaces.marketing.tests.test_pult_api import FalscheDB

PK, AK = "pult-k", "api-k"
IID = "11111111-1111-1111-1111-111111111111"
H = {"X-Pult-Key": PK, "X-API-Key": AK}
G = {"version": 1, "format": "quer", "hintergrund": "#FFFFFF", "ebenen": []}


@pytest.fixture
def umg(monkeypatch, tmp_path):
    from spaces.marketing.api import gestaltung as ga
    from spaces.marketing.sync import _db
    f = FalscheDB()
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setenv("MARKETING_PULT_KEY", PK)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.delenv("MARKETING_MEDIEN_ORDNER", raising=False)
    monkeypatch.setattr(server, "API_KEY", AK)
    monkeypatch.setattr(ga, "_zuletzt", 0.0)
    return f, tmp_path


def test_rechnet_und_liefert_url(umg):
    f, ordner = umg
    f.antworten.append([{"id": IID}])
    r = TestClient(server.app).post(f"/api/pult/inhalte/{IID}/gestaltung", json={"gestaltung": G}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["url"].startswith("medien:gs-") and d["height"] == 400
    assert (ordner / d["url"][7:]).exists()
    assert f.sql and all(s.lstrip().upper().startswith("SELECT") for s in f.sql)
    assert "marketing.inhalte" in f.sql[0]


def test_unbekannter_inhalt_404(umg):
    r = TestClient(server.app).post(f"/api/pult/inhalte/{IID}/gestaltung", json={"gestaltung": G}, headers=H)
    assert r.status_code == 404


def test_fehlende_quelle_422(umg):
    f, _ = umg
    f.antworten.append([{"id": IID}])
    g = dict(G, ebenen=[{"id": "b", "art": "bild", "quelle": "medien:weg.png", "x": 1, "y": 1, "breite": 10, "drehung": 0}])
    r = TestClient(server.app).post(f"/api/pult/inhalte/{IID}/gestaltung", json={"gestaltung": g}, headers=H)
    assert r.status_code == 422 and "weg.png fehlt in den Medien" in r.json()["detail"]


def test_ohne_schluessel_401(umg):
    r = TestClient(server.app).post(f"/api/pult/inhalte/{IID}/gestaltung", json={"gestaltung": G}, headers={"X-API-Key": AK})
    assert r.status_code == 401


def _dok(g, url=None):
    return {"root": {"type": "EmailLayout", "data": {"childrenIds": ["f"]}},
            "f": {"type": "Image", "data": {"style": {}, "props": {"url": url, "alt": "x", "width": 600, "height": 1, "gestaltung": g}}}}


def test_gestaltungen_rechnen_setzt_url(umg):
    from spaces.marketing.api import gestaltung as ga
    dok = _dok(G)
    neu, hinweise = ga.gestaltungen_rechnen(dok)
    assert neu["f"]["data"]["props"]["url"].startswith("medien:gs-") and neu["f"]["data"]["props"]["height"] == 400
    assert dok["f"]["data"]["props"]["url"] is None  # Original unveraendert
    assert hinweise == []


def test_gestaltungen_rechnen_ueberspringt_aktuelle_url(umg):
    from spaces.marketing.api import gestaltung as ga
    from spaces.marketing.claw import gestaltung as gc
    g = dict(G, ebenen=[{"id": "b", "art": "bild", "quelle": "medien:weg.png", "x": 1, "y": 1, "breite": 10, "drehung": 0}])
    dok = _dok(g, "medien:" + gc.name_fuer(g))
    neu, _ = ga.gestaltungen_rechnen(dok)   # wuerde sonst an der fehlenden Quelle scheitern
    assert neu == dok


def test_gestaltungen_rechnen_fehler_mit_blockid(umg):
    from spaces.marketing.api import gestaltung as ga
    from spaces.marketing.claw.gestaltung import GestaltungFehler
    g = dict(G, ebenen=[{"id": "b", "art": "bild", "quelle": "medien:weg.png", "x": 1, "y": 1, "breite": 10, "drehung": 0}])
    with pytest.raises(GestaltungFehler, match="^f: Bild weg.png fehlt"):
        ga.gestaltungen_rechnen(_dok(g))


def test_verwiesene_gs_und_aufraeumen_stuendlich(umg):
    from spaces.marketing.api import gestaltung as ga
    f, ordner = umg
    alt, bleibt = "gs-" + "a" * 12 + ".jpg", "gs-" + "b" * 12 + ".jpg"
    for n in (alt, bleibt):
        (ordner / n).write_bytes(b"x")
        os.utime(ordner / n, (1, 1))
    f.antworten.append([{"m": bleibt}])
    ga.aufraeumen_falls_faellig(jetzt=1_000_000_000.0)
    assert not (ordner / alt).exists() and (ordner / bleibt).exists()
    assert "regexp_matches" in f.sql[0] and f.sql[0].lstrip().upper().startswith("SELECT")
    for tabelle in ("inhalt_fassungen", "newsletter_vorlagen_fassungen", "newsletter_vorlagen "):
        assert tabelle in f.sql[0] + " "
    assert len(f.sql) == 1   # eine einzige Abfrage
    n = len(f.sql)
    ga.aufraeumen_falls_faellig(jetzt=1_000_000_000.0 + 60)   # zu frueh
    assert len(f.sql) == n


def test_aufraeumen_schluckt_fehler_behaelt_dateien_und_versucht_erneut(umg):
    from spaces.marketing.api import gestaltung as ga
    f, ordner = umg
    alt = "gs-" + "a" * 12 + ".jpg"
    (ordner / alt).write_bytes(b"x")
    os.utime(ordner / alt, (1, 1))
    f.fehler.append(RuntimeError("db weg"))
    ga.aufraeumen_falls_faellig(jetzt=1_000_000_000.0)   # darf nicht werfen
    assert (ordner / alt).exists()
    ga.aufraeumen_falls_faellig(jetzt=1_000_000_000.0 + 60)   # kein Stundensperre nach Fehler
    assert not (ordner / alt).exists()


def test_gestaltungen_rechnen_ueberspringt_kaputte_bloecke(umg):
    from spaces.marketing.api import gestaltung as ga
    dok = {"a": {"type": "Image", "data": None}, "b": {"type": "Image", "data": {"props": "x"}}, "c": "x"}
    neu, hinweise = ga.gestaltungen_rechnen(dok)
    assert neu == dok and hinweise == []


def test_bildplaetze_ueberspringen_flaechen():
    dok = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["f"]}},
           "f": {"type": "Image", "data": {"style": {}, "props": {"url": "medien:gs-aaaaaaaaaaaa.jpg", "alt": "x",
                 "width": 600, "height": 400, "gestaltung": G}}}}
    assert bildplaetze.finde(dok) == []
