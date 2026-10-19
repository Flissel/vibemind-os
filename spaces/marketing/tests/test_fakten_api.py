"""Geschaeftsfakten fuer den Marketing-Agenten (Paket D2, 19.10.2026) - ohne echte DB.
User-Entscheid D1: nur Zahlen (keine Namen, keine Texte), Zahlen immer exakt."""
import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import fakten, server
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

AK = "api-k"
A = {"X-API-Key": AK}
ZEILE = {"f": {"kontakte": 558, "firmen": 477, "personen": 79, "stufen": {"new": 433}, "herkunft": {"recherche": 477},
               "bereiche": {"bav": 477}, "einwilligung": {"opt_in": 3}, "newsletter_hoechstens": 3,
               "termine": 10, "vertraege": 0, "empfehlungslisten": 0,
               "gespraeche_je_monat": [{"monat": "2026-10", "ausgehend": 120, "antworten": 80}]}}


@pytest.fixture
def db(monkeypatch):
    f = FalscheDB()
    f.psql = FalschePsql(f)
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", f.psql)
    monkeypatch.setattr(server, "API_KEY", AK)
    monkeypatch.delenv("MARKETING_FAKTEN_SCHEMAS", raising=False)
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


def test_fakten_vibemind_aus_dem_sales_schema(db, c):
    db.antworten = [[ZEILE]]
    r = c.get("/api/fakten?firma=vibemind", headers=A)
    assert r.status_code == 200
    j = r.json()
    assert j["firma"] == "vibemind" and j["kontakte"] == 558 and j["newsletter_hoechstens"] == 3
    assert "Zahlen" in j["hinweis"] and j["stand"]
    sql = db.sql[0]
    assert "sales.leads" in sql and "sales.activities" in sql and "sales.empfehlungslisten" in sql
    assert "'_privat'" in sql                                   # private Kontakte zaehlen nicht mit


def test_keine_namen_keine_texte_im_sql(db, c):
    """D1: nur Zahlen - die Abfrage liest weder Namen, E-Mails, Telefonnummern noch Nachrichtentexte."""
    db.antworten = [[ZEILE]]
    c.get("/api/fakten?firma=vibemind", headers=A)
    sql = db.sql[0].lower()
    for spalte in ("l.name", "l.email", "l.phone", "l.notes", "payload", "body", "vorname", "company"):
        assert spalte not in sql.replace("btrim(l.email)", ""), spalte


def test_newsletter_hoechstens_wie_die_versandregel(db, c):
    db.antworten = [[ZEILE]]
    c.get("/api/fakten?firma=vibemind", headers=A)
    sql = db.sql[0]
    assert "consent_status = 'opt_in'" in sql and "btrim(l.email)" in sql and "'archiviert'" in sql


def test_firma_ohne_laden(db, c):
    r = c.get("/api/fakten?firma=fin2gether", headers=A)
    assert r.status_code == 404 and "keinen Laden" in r.json()["detail"]
    assert db.sql == []


def test_zuordnung_ueber_umgebung(db, c, monkeypatch):
    monkeypatch.setenv("MARKETING_FAKTEN_SCHEMAS", "vibemind:sales, fin2gether:sales_ivan")
    db.antworten = [[ZEILE]]
    assert c.get("/api/fakten?firma=fin2gether", headers=A).status_code == 200
    assert "sales_ivan.leads" in db.sql[0]


@pytest.mark.parametrize("wert", ["vibemind:sales;drop", "vibemind:Sales", "vibemind:sales.x", "kaputt"])
def test_boese_zuordnung_wird_verworfen(db, c, monkeypatch, wert):
    monkeypatch.setenv("MARKETING_FAKTEN_SCHEMAS", wert)
    assert c.get("/api/fakten?firma=vibemind", headers=A).status_code == 404
    assert db.sql == []


def test_firma_geprueft_und_schluessel_noetig(db, c):
    assert c.get("/api/fakten?firma=x';--", headers=A).status_code == 422
    assert c.get("/api/fakten?firma=vibemind").status_code in (401, 403)


def test_zuordnung_rein():
    assert fakten.zuordnung("vibemind:sales,fin2gether:sales_ivan") == {"vibemind": "sales", "fin2gether": "sales_ivan"}
    assert fakten.zuordnung("") == {}


def test_gruppen_nur_aus_festen_listen(db, c):
    """Sicherheitspruefung 19.10.: Herkunft, Stufe, Einwilligung und Bereiche sind Freitext - ein Name dort
    landete sonst als Gruppenschluessel beim Agenten. Nur bekannte Werte, alles andere 'sonstige'."""
    db.antworten = [[ZEILE]]
    c.get("/api/fakten?firma=vibemind", headers=A)
    sql = db.sql[0]
    assert sql.count("'sonstige'") >= 4
    assert "l.source IN ('recherche'" in sql and "l.status IN ('new'" in sql
    assert "l.consent_status IN ('opt_in'" in sql and "b IN ('bav'" in sql
    assert "coalesce(l.source, 'ohne') k" not in sql
