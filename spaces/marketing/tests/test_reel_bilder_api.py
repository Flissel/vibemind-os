"""Reels Stufe 3 - KI-Bilder (23.10.2026, Migration 090): Arbeiter- und Pult-Routen ohne echte DB (FalscheDB)."""
import io

import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import medien_mandant, reel_bilder, server
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

AID = "0123abcd-3333-3333-3333-333333333333"
BID = "89abcdef-5555-5555-5555-555555555555"
NEU = "fedcba98-6666-6666-6666-666666666666"
BK, AK, PK = "bild-k", "api-k", "pult-k"
A, B, P = {"X-API-Key": AK}, {"X-Bild-Key": BK}, {"X-Pult-Key": PK}


def _jpeg(w=96, h=160) -> bytes:
    from PIL import Image
    puffer = io.BytesIO()
    Image.new("RGB", (w, h), (20, 80, 60)).save(puffer, "JPEG", quality=80)
    return puffer.getvalue()


@pytest.fixture
def db(monkeypatch, tmp_path):
    f = FalscheDB()
    f.psql = FalschePsql(f)
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", f.psql)
    monkeypatch.setenv("MARKETING_BILD_KEY", BK)
    monkeypatch.setenv("MARKETING_PULT_KEY", PK)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", AK)
    f.ordner = tmp_path
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


# --- Schluessel ---------------------------------------------------------------------------------------

def test_schluessel_pflicht(db, c):
    jpeg = _jpeg()
    for pfad, koerper in (("/api/video/arbeiter/ideen/naechster", None),
                          (f"/api/video/arbeiter/{AID}/ideen", {"ideen": ["a", "b", "c"]}),
                          (f"/api/video/arbeiter/{AID}/ideen-fehler", {"befund": "x"}),
                          ("/api/video/arbeiter/reel-bild/naechster", None),
                          (f"/api/video/arbeiter/reel-bild/{BID}/weiter", None),
                          (f"/api/video/arbeiter/reel-bild/{BID}/fehler", {"befund": "x"})):
        assert c.post(pfad, json=koerper).status_code == 401, pfad
        assert c.post(pfad, headers=P, json=koerper).status_code == 401, pfad      # Pult-Key zaehlt nicht
    assert c.post(f"/api/video/arbeiter/reel-bild/{BID}", content=jpeg).status_code == 401
    assert c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=A, content=jpeg).status_code == 401
    for pfad, koerper in ((f"/api/pult/video/{AID}/ki/neu", {"bild": BID, "mandant": "vibemind"}),
                          (f"/api/pult/video/{AID}/ki/freigeben", {"mandant": "vibemind"})):
        assert c.post(pfad, json=koerper).status_code == 401, pfad
        assert c.post(pfad, headers=B, json=koerper).status_code == 401, pfad
    assert not db.sql and not db.psql.sql


def test_arbeiter_ohne_globalen_api_schluessel(db, c):
    """Der Bild-Arbeiter kennt den X-API-Key nicht: /api/video/arbeiter/... ist von der Middleware ausgenommen."""
    db.antworten = [[{"a": None}], [{"b": None}]]
    assert c.post("/api/video/arbeiter/ideen/naechster", headers=B).status_code == 200
    assert c.post("/api/video/arbeiter/reel-bild/naechster", headers=B).status_code == 200


# --- Ideen ---------------------------------------------------------------------------------------------

def test_ideen_naechster_und_setzen(db, c):
    auftrag = {"id": AID, "mandant": "vibemind", "titel": "T", "briefing": {"ki": {"anzahl": 3}}}
    db.antworten = [[{"a": auftrag}]]
    r = c.post("/api/video/arbeiter/ideen/naechster", headers=B)
    assert r.status_code == 200 and r.json() == {"auftrag": auftrag}
    assert "marketing.video_ideen_naechster(" in db.sql[-1] and "'10 minutes'::interval" in db.sql[-1]
    db.antworten = [[{"a": None}]]
    assert c.post("/api/video/arbeiter/ideen/naechster", headers=B).json() == {"auftrag": None}

    db.antworten = [[{"n": 3}]]
    r = c.post(f"/api/video/arbeiter/{AID}/ideen", headers=B,
               json={"ideen": [" Ein Team am Tisch ", "Hände mit Vertrag", "Sonnenaufgang über Büros"]})
    assert r.status_code == 200 and r.json() == {"anzahl": 3}
    assert f"marketing.video_ideen_setzen('{AID}'::uuid, " in db.sql[-1]
    assert '"Ein Team am Tisch"' in db.sql[-1] and "::jsonb" in db.sql[-1]

    # DB-Ablehnung (falsche Anzahl gegen briefing.ki.anzahl) -> 422 mit der Meldung
    db.fehler = [RuntimeError("ERROR:  Es braucht genau 4 Bildideen")]
    r = c.post(f"/api/video/arbeiter/{AID}/ideen", headers=B, json={"ideen": ["a", "b", "c"]})
    assert r.status_code == 422 and r.json()["detail"] == "Es braucht genau 4 Bildideen"


@pytest.mark.parametrize("koerper", [{}, {"ideen": "a"}, {"ideen": ["a", "b"]}, {"ideen": ["a"] * 6},
                                     {"ideen": ["a", "b", 3]}, {"ideen": ["a", "b", "   "]},
                                     {"ideen": ["a", "b", "x" * 601]}])
def test_ideen_formen(db, c, koerper):
    r = c.post(f"/api/video/arbeiter/{AID}/ideen", headers=B, json=koerper)
    assert r.status_code == 422 and not db.sql


def test_ideen_unbekannter_auftrag(db, c):
    assert c.post("/api/video/arbeiter/kein-uuid/ideen", headers=B, json={"ideen": ["a", "b", "c"]}).status_code == 404
    assert not db.sql


def test_ideen_fehler(db, c):
    db.antworten = [[{"s": "ideen"}], [{"s": "fehler"}], [{"s": None}]]
    r = c.post(f"/api/video/arbeiter/{AID}/ideen-fehler", headers=B, json={"befund": "Shim 429"})
    assert r.json() == {"status": "ideen"} and "marketing.video_ideen_fehler(" in db.sql[-1]
    assert "'Shim 429'" in db.sql[-1]
    assert c.post(f"/api/video/arbeiter/{AID}/ideen-fehler", headers=B, json={}).json() == {"status": "fehler"}
    assert c.post(f"/api/video/arbeiter/{AID}/ideen-fehler", headers=B, json={"befund": "x"}).json() == {"status": None}
    assert c.post(f"/api/video/arbeiter/{AID}/ideen-fehler", headers=B, json={"befund": 5}).status_code == 422


# --- Reel-Bilder: abholen, verlaengern, Fehler ----------------------------------------------------------

def test_reel_bild_naechster_weiter_fehler(db, c):
    bild = {"id": BID, "video_auftrag": AID, "nr": 2, "idee": "x", "versuch": 1, "mandant": "vibemind",
            "format": "reel", "titel": "T"}
    db.antworten = [[{"b": bild}], [{"ok": True}], [{"ok": False}], [{"ok": True}]]
    assert c.post("/api/video/arbeiter/reel-bild/naechster", headers=B).json() == {"bild": bild}
    assert "marketing.reel_bild_naechster('10 minutes'::interval)" in db.sql[-1]
    assert c.post(f"/api/video/arbeiter/reel-bild/{BID}/weiter", headers=B).json() == {"ok": True}
    assert f"marketing.reel_bild_verlaengern('{BID}'::uuid, '10 minutes'::interval)" in db.sql[-1]
    assert c.post(f"/api/video/arbeiter/reel-bild/{BID}/weiter", headers=B).json() == {"ok": False}
    r = c.post(f"/api/video/arbeiter/reel-bild/{BID}/fehler", headers=B, json={"befund": "ComfyUI weg"})
    assert r.json() == {"ok": True} and f"marketing.reel_bild_fehler('{BID}'::uuid, 'ComfyUI weg')" in db.sql[-1]
    assert c.post("/api/video/arbeiter/reel-bild/kein-uuid/weiter", headers=B).status_code == 404


# --- Reel-Bilder: hochladen ------------------------------------------------------------------------------

def _bildzeile(status="in_arbeit", auftrag_status="bilder_laufen", nr=2, versuch=3):
    return [{"status": status, "nr": nr, "versuch": versuch, "video_auftrag": AID, "mandant": "vibemind",
             "auftrag_status": auftrag_status}]


def test_reel_bild_upload_prueft_jpeg_und_name(db, c):
    jpeg = _jpeg()
    db.antworten = [_bildzeile(), [{"ok": True}]]
    r = c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=B, content=jpeg)
    assert r.status_code == 200, r.text
    name = "ki-0123abcd-2-3.jpg"
    assert r.json() == {"name": name} and (db.ordner / name).read_bytes() == jpeg
    assert f"marketing.reel_bild_fertig('{BID}'::uuid, '{name}')" in db.sql[-1]
    # erst der Firma zuordnen, dann ablegen und fertig melden
    assert db.psql.sql and name in db.psql.sql[-1] and "'vibemind'" in db.psql.sql[-1]
    assert db.psql.vorher[-1] == 1                   # nach dem Nachschlagen, vor reel_bild_fertig


@pytest.mark.parametrize("roh, code", [
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, 422),         # kein JPEG
    (b"\xff\xd8\xff\xe0" + b"\x00" * 2000, 422),         # JPEG-Kopf, aber kaputt
    (b"", 422),
])
def test_reel_bild_upload_formen(db, c, roh, code):
    assert c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=B, content=roh).status_code == code
    assert not db.sql and not list(db.ordner.iterdir())


def test_reel_bild_upload_zu_gross(db, c):
    gross = _jpeg() + b"\x00" * reel_bilder.BILD_MAX
    assert c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=B, content=gross).status_code == 413
    assert not db.sql and not list(db.ordner.iterdir())


@pytest.mark.parametrize("zeile", [_bildzeile(status="fertig"), _bildzeile(status="verworfen"),
                                   _bildzeile(auftrag_status="abgebrochen")])
def test_reel_bild_upload_nur_in_arbeit(db, c, zeile):
    """Nie eine fertige Datei ueberschreiben: erst nachschlagen, nur bei in_arbeit (Auftrag bilder_laufen) ablegen."""
    vorhanden = db.ordner / "ki-0123abcd-2-3.jpg"
    vorhanden.write_bytes(b"alt")
    db.antworten = [zeile]
    assert c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=B, content=_jpeg()).status_code == 409
    assert vorhanden.read_bytes() == b"alt" and not db.psql.sql
    assert not any("reel_bild_fertig" in s for s in db.sql)


def test_reel_bild_upload_unbekannt(db, c):
    db.antworten = [[]]
    assert c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=B, content=_jpeg()).status_code == 404
    assert c.post("/api/video/arbeiter/reel-bild/kein-uuid", headers=B, content=_jpeg()).status_code == 404
    assert not list(db.ordner.iterdir())


def test_reel_bild_upload_db_lehnt_ab_loescht_datei(db, c):
    db.antworten = [_bildzeile(), [{"ok": False}], [{"status": "verworfen", "datei": None}]]
    assert c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=B, content=_jpeg()).status_code == 409
    assert not (db.ordner / "ki-0123abcd-2-3.jpg").exists()
    db.antworten = [_bildzeile()]
    db.fehler = [None, RuntimeError("ERROR:  Dateiname passt nicht zu diesem Bild")]
    r = c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=B, content=_jpeg())
    assert r.status_code == 422 and not (db.ordner / "ki-0123abcd-2-3.jpg").exists()


def test_reel_bild_upload_zuordnung_scheitert_keine_datei(db, c):
    db.antworten = [_bildzeile()]
    db.psql.fehler = [RuntimeError("ERROR:  kaputt")]
    r = c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=B, content=_jpeg())
    assert r.status_code == 422 and not list(db.ordner.iterdir())
    assert not any("reel_bild_fertig" in s for s in db.sql)


# --- Pult: neu erzeugen, freigeben -----------------------------------------------------------------------

def test_ki_neu_und_freigeben(db, c):
    db.antworten = [[{"mandant": "vibemind"}], [{"video_auftrag": AID}], [{"id": NEU}]]
    r = c.post(f"/api/pult/video/{AID}/ki/neu", headers=P,
               json={"bild": BID, "mandant": "vibemind", "idee": " Ein Hafen bei Nacht "})
    assert r.status_code == 200 and r.json() == {"bild": NEU}
    assert f"marketing.reel_bild_neu('{BID}'::uuid, 'vibemind', 'Ein Hafen bei Nacht')" in db.sql[-1]
    # ohne Idee: NULL -> dieselbe Idee
    db.antworten = [[{"mandant": "vibemind"}], [{"video_auftrag": AID}], [{"id": NEU}]]
    r = c.post(f"/api/pult/video/{AID}/ki/neu", headers=P, json={"bild": BID, "mandant": "vibemind", "idee": ""})
    assert r.status_code == 200 and f"marketing.reel_bild_neu('{BID}'::uuid, 'vibemind', NULL)" in db.sql[-1]

    db.antworten = [[{"mandant": "vibemind"}], [{"ok": True}]]
    r = c.post(f"/api/pult/video/{AID}/ki/freigeben", headers=P, json={"mandant": "vibemind"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert f"marketing.video_ki_freigeben('{AID}'::uuid, 'vibemind')" in db.sql[-1]


def test_ki_neu_und_freigeben_mandant_404(db, c):
    # fremde Firma: die API prueft zuerst, kein Aufruf der SQL-Funktion
    db.antworten = [[{"mandant": "andere"}]]
    r = c.post(f"/api/pult/video/{AID}/ki/neu", headers=P, json={"bild": BID, "mandant": "vibemind"})
    assert r.status_code == 404
    db.antworten = [[{"mandant": "andere"}]]
    r = c.post(f"/api/pult/video/{AID}/ki/freigeben", headers=P, json={"mandant": "vibemind"})
    assert r.status_code == 404
    # Bild eines anderen Auftrags (oder unbekannt) -> 404
    db.antworten = [[{"mandant": "vibemind"}], [{"video_auftrag": NEU}]]
    r = c.post(f"/api/pult/video/{AID}/ki/neu", headers=P, json={"bild": BID, "mandant": "vibemind"})
    assert r.status_code == 404
    db.antworten = [[{"mandant": "vibemind"}], []]
    r = c.post(f"/api/pult/video/{AID}/ki/neu", headers=P, json={"bild": BID, "mandant": "vibemind"})
    assert r.status_code == 404
    r = c.post(f"/api/pult/video/{AID}/ki/neu", headers=P, json={"bild": "kein-uuid", "mandant": "vibemind"})
    assert r.status_code == 404
    assert not any("reel_bild_neu" in s or "video_ki_freigeben" in s for s in db.sql)
    # Rueckhalt in der DB: "nicht gefunden" -> 404
    db.antworten = [[{"mandant": "vibemind"}], [{"video_auftrag": AID}]]
    db.fehler = [None, None, RuntimeError("ERROR:  Bild nicht gefunden")]
    r = c.post(f"/api/pult/video/{AID}/ki/neu", headers=P, json={"bild": BID, "mandant": "vibemind"})
    assert r.status_code == 404
    db.antworten = [[{"mandant": "vibemind"}]]
    db.fehler = [None, RuntimeError("ERROR:  Auftrag nicht gefunden")]
    r = c.post(f"/api/pult/video/{AID}/ki/freigeben", headers=P, json={"mandant": "vibemind"})
    assert r.status_code == 404


def test_ki_neu_db_fehler_422(db, c):
    db.antworten = [[{"mandant": "vibemind"}], [{"video_auftrag": AID}]]
    db.fehler = [None, None, RuntimeError("ERROR:  Bilder lassen sich nur beim Prüfen neu erzeugen")]
    r = c.post(f"/api/pult/video/{AID}/ki/neu", headers=P, json={"bild": BID, "mandant": "vibemind"})
    assert r.status_code == 422 and r.json()["detail"] == "Bilder lassen sich nur beim Prüfen neu erzeugen"
    db.antworten = [[{"mandant": "vibemind"}]]
    db.fehler = [None, RuntimeError("ERROR:  Es gibt noch kein fertiges Bild")]
    r = c.post(f"/api/pult/video/{AID}/ki/freigeben", headers=P, json={"mandant": "vibemind"})
    assert r.status_code == 422 and r.json()["detail"] == "Es gibt noch kein fertiges Bild"


@pytest.mark.parametrize("koerper", [{"bild": BID, "mandant": "vibemind", "idee": 5},
                                     {"bild": BID, "mandant": "vibemind", "idee": "x" * 601},
                                     {"bild": BID, "mandant": "../x"}, {"bild": BID}])
def test_ki_neu_formen(db, c, koerper):
    db.antworten = [[{"mandant": "vibemind"}], [{"video_auftrag": AID}]]
    r = c.post(f"/api/pult/video/{AID}/ki/neu", headers=P, json=koerper)
    assert r.status_code == 422 and not any("reel_bild_neu" in s for s in db.sql)


def test_reel_bild_upload_fertig_false_aber_schon_fertig_behaelt_datei(db, c):
    """Race: ein paralleler Upload desselben Versuchs hat das Bild schon fertig gemeldet - reel_bild_fertig
    liefert false, die Zeile zeigt aber fertig mit genau diesem Namen. Die Datei bleibt, Antwort 409."""
    name = "ki-0123abcd-2-3.jpg"
    db.antworten = [_bildzeile(), [{"ok": False}], [{"status": "fertig", "datei": name}]]
    r = c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=B, content=_jpeg())
    assert r.status_code == 409 and (db.ordner / name).exists()
    assert f"'{BID}'::uuid" in db.sql[-1] and "datei" in db.sql[-1]
    # fertig mit anderem Namen -> diese Datei gehoert zu nichts und geht weg
    db.antworten = [_bildzeile(), [{"ok": False}], [{"status": "fertig", "datei": "ki-0123abcd-2-2.jpg"}]]
    (db.ordner / name).unlink()
    assert c.post(f"/api/video/arbeiter/reel-bild/{BID}", headers=B, content=_jpeg()).status_code == 409
    assert not (db.ordner / name).exists()


def test_ki_neu_fremde_firma_vor_formpruefung_404(db, c):
    for idee in ("x" * 601, 5):
        db.antworten = [[{"mandant": "andere"}]]
        r = c.post(f"/api/pult/video/{AID}/ki/neu", headers=P, json={"bild": BID, "mandant": "vibemind", "idee": idee})
        assert r.status_code == 404
    assert not any("reel_bild_neu" in s for s in db.sql)
