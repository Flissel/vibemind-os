"""Marken-Routen (Pult + Arbeiter) ohne echte DB (FalscheDB wie test_pult_api)."""
import io
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from spaces.marketing.api import server
from spaces.marketing.claw import schriften
from spaces.marketing.tests.test_medien_mandant import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

PK, BK, AK = "pult-k", "bild-k", "api-k"
IID = "11111111-1111-1111-1111-111111111111"
AID = "22222222-2222-2222-2222-222222222222"
VID = "33333333-3333-3333-3333-333333333333"
H = {"X-Pult-Key": PK}
HB = {"X-Bild-Key": BK}
MARKE = Path(__file__).resolve().parents[1]
STUDIO = json.loads((MARKE / "vorlagen" / "newsletter" / "studio.json").read_text(encoding="utf-8"))["bloecke"]
VORSCHLAG = {"akzent": "#b45309", "zweitfarbe": "#3b2f2f", "grund": "#faf7f2", "text": "#2b2724",
             "schrift_anzeige": "playfair", "schrift_text": "manrope", "logo": None, "abschnitte": {},
             "mustertext": {"betreff": "Betreff Muster", "ueberschrift": "Ueberschrift Muster",
                            "absatz": "Absatz Muster"}}
JOB = {"mandant": "radhaus", "art": "chat", "status": "in_arbeit", "gueltig": True}
BASIS = "https://h.ts.net/marketing/bild/t/"


def _sicht(eigene=(), fremde=(), gemeinsam=()):
    return [{"name": "Radhaus", "eigene": list(eigene), "fremde": list(fremde), "gemeinsam": list(gemeinsam)}]


def _bild(fmt="PNG", groesse=(40, 30)):
    puffer = io.BytesIO()
    Image.new("RGB", groesse, "#b45309").save(puffer, fmt)
    return puffer.getvalue()


def _db_fehler(text):
    return RuntimeError(f"ERROR:  {text}")


@pytest.fixture
def umg(monkeypatch, tmp_path):
    from spaces.marketing.sync import _db
    f = FalscheDB()
    f.psql = FalschePsql()
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", f.psql)
    monkeypatch.setenv("MARKETING_PULT_KEY", PK)
    monkeypatch.setenv("MARKETING_BILD_KEY", BK)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.delenv("MARKETING_MEDIEN_ORDNER", raising=False)
    monkeypatch.setattr(server, "API_KEY", AK)
    return f, tmp_path, TestClient(server.app)


# ─── Register-Abgleich ──────────────────────────────────────────────────


def test_sql_register_gleich_python_register():
    sql = (MARKE / "db" / "064_marke.sql").read_text(encoding="utf-8")
    block = re.search(r"marke_schriften\(\) RETURNS text\[\].*?ARRAY\[(.*?)\]::text\[\]", sql, re.S).group(1)
    ids = re.findall(r"'([^']+)'", block)
    assert ids == list(schriften.REGISTER.keys())


# ─── Schluessel (401) fuer jede Route ───────────────────────────────────

PULT_ROUTEN = [
    ("get", "/api/pult/marke?mandant=radhaus"),
    ("post", "/api/pult/marke/chat"),
    ("post", f"/api/pult/marke/vorschlaege/{VID}/uebernehmen"),
    ("post", f"/api/pult/marke/vorschlaege/{VID}/verwerfen"),
    ("get", f"/api/pult/marke/vorschlaege/{VID}/vorschau"),
    ("post", f"/api/pult/inhalte/{IID}/marke_hinweis_aus"),
]
ARBEITER_ROUTEN = [
    ("post", "/api/marke/arbeiter/naechster"),
    ("post", f"/api/marke/arbeiter/{AID}/weiter"),
    ("post", f"/api/marke/arbeiter/{AID}/vorschlag"),
    ("post", f"/api/marke/arbeiter/{AID}/fertig"),
    ("post", f"/api/marke/arbeiter/{AID}/zurueck"),
    ("get", f"/api/marke/arbeiter/{AID}/medien/foto.jpg"),
    ("post", f"/api/marke/arbeiter/{AID}/logo"),
    ("post", "/api/marke/arbeiter/spiegeln"),
    ("post", "/api/marke/arbeiter/markieren"),
    ("get", "/api/marke/arbeiter/firmen"),
]


def _anfrage(c, methode):
    """POST mit leerem JSON-Body: FastAPI prueft Body-Formen vor dem Schluessel."""
    if methode == "get":
        return c.get
    return lambda pfad, **kw: c.post(pfad, json={}, **kw)


@pytest.mark.parametrize("methode,pfad", PULT_ROUTEN)
def test_pult_routen_ohne_schluessel_401(umg, methode, pfad):
    f, _, c = umg
    anfrage = _anfrage(c, methode)
    assert anfrage(pfad).status_code == 401
    assert anfrage(pfad, headers={"X-Pult-Key": "falsch"}).status_code == 401
    assert f.sql == []


@pytest.mark.parametrize("methode,pfad", ARBEITER_ROUTEN)
def test_arbeiter_routen_ohne_schluessel_401(umg, methode, pfad):
    f, _, c = umg
    anfrage = _anfrage(c, methode)
    assert anfrage(pfad).status_code == 401              # Middleware laesst durch, eigener Schluessel greift
    assert anfrage(pfad, headers={"X-Bild-Key": "falsch"}).status_code == 401
    assert anfrage(pfad, headers=H).status_code == 401   # Pult-Schluessel ist kein Bild-Schluessel
    assert f.sql == []


# ─── Pult: Stand ────────────────────────────────────────────────────────


def test_stand_liefert_alles(umg):
    f, _, c = umg
    gestalt = {"akzent": "#b45309", "flaeche": "#3b2f2f", "logo": "data:image/png;base64,AAAA",
               "schriften": {"anzeige": "playfair", "text": "manrope"}, "grund": "#fff", "text": "#000"}
    f.antworten += [[{"ok": True}],
                    [{"name": "Radhaus", "stand": "2026-10-07 10:00 von Anna", "gespiegelt_am": "2026-10-07 10:01:00+00",
                      "fehler": None, "gestalt": gestalt}],
                    [{"id": AID, "nachricht": "Hallo", "antwort": "Hi", "status": "fertig", "hinweise": [],
                      "vorschlag": VID, "erstellt_am": "x", "sortiert_am": "y"}],
                    [{"art": "chat"}],
                    [{"id": VID, "vorschlag": VORSCHLAG, "erstellt_am": "z"}]]
    r = c.get("/api/pult/marke?mandant=radhaus", headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["mandant"] == "radhaus" and j["name"] == "Radhaus"
    assert j["spiegel"] == {"gestalt": {"akzent": "#b45309", "flaeche": "#3b2f2f", "logo": "data:image/png;base64,AAAA",
                                        "schriften": {"anzeige": "playfair", "text": "manrope"}},
                            "stand": "2026-10-07 10:00 von Anna", "gespiegelt_am": "2026-10-07 10:01:00+00",
                            "fehler": None}
    assert j["auftraege"][0]["id"] == AID and "sortiert_am" not in j["auftraege"][0]
    assert j["laeuft"] is True and j["uebernahme"] is None
    assert j["vorschlag"] == {"id": VID, "vorschlag": VORSCHLAG, "erstellt_am": "z"}
    assert "_marke_aufraeumen('radhaus')" in f.sql[0]
    assert "LIMIT 10" in f.sql[2] and "art = 'chat'" in f.sql[2]


def test_stand_ohne_spiegel_uebernahme_laeuft(umg):
    f, _, c = umg
    f.antworten += [[{"ok": True}], [{"name": "Radhaus", "stand": None, "gespiegelt_am": None, "fehler": "kaputt",
                                       "gestalt": None}], [], [{"art": "uebernehmen"}], []]
    j = c.get("/api/pult/marke?mandant=radhaus", headers=H).json()
    assert j["spiegel"] == {"gestalt": {}, "stand": "", "gespiegelt_am": None, "fehler": "kaputt"}
    assert j["laeuft"] is False and j["uebernahme"] == "laeuft" and j["vorschlag"] is None and j["auftraege"] == []


def test_stand_unbekannter_mandant_404_ohne_mandant_422_db_weg_503(umg):
    f, _, c = umg
    f.antworten += [[{"ok": True}], []]
    assert c.get("/api/pult/marke?mandant=gibtsnicht", headers=H).status_code == 404
    assert c.get("/api/pult/marke", headers=H).status_code == 422          # kein Rueckfall auf vibemind
    assert c.get("/api/pult/marke?mandant=GROSS", headers=H).status_code == 422
    f.fehler += [RuntimeError("db weg")]
    assert c.get("/api/pult/marke?mandant=radhaus", headers=H).status_code == 503


# ─── Pult: Chat, Uebernehmen, Verwerfen, Hinweis ────────────────────────


def test_chat_reicht_durch_und_prueft(umg):
    f, _, c = umg
    f.antworten.append([{"id": AID}])
    r = c.post("/api/pult/marke/chat", headers=H, json={"mandant": "radhaus", "nachricht": "Wir sind Radhaus",
                                                          "kontext": {"anhaenge": [{"art": "bild", "name": "a.png"}]}})
    assert r.status_code == 200 and r.json() == {"auftrag": AID}
    assert "marketing.pult_marke_anlegen('radhaus', 'Wir sind Radhaus'" in f.sql[0] and "a.png" in f.sql[0]
    n = len(f.sql)
    for body in ({"nachricht": "x"}, {"mandant": "radhaus"}, {"mandant": "radhaus", "nachricht": "  "},
                 {"mandant": "radhaus", "nachricht": "x" * 2001}, {"mandant": "radhaus", "nachricht": "x", "kontext": "x"}):
        assert c.post("/api/pult/marke/chat", headers=H, json=body).status_code == 422, body
    assert len(f.sql) == n


def test_chat_db_ablehnung_422_ausfall_503(umg):
    f, _, c = umg
    body = {"mandant": "radhaus", "nachricht": "x"}
    f.fehler += [_db_fehler("Der Assistent arbeitet gerade")]
    r = c.post("/api/pult/marke/chat", headers=H, json=body)
    assert r.status_code == 422 and r.json()["detail"] == "Der Assistent arbeitet gerade"
    f.fehler += [RuntimeError("ssh weg")]
    assert c.post("/api/pult/marke/chat", headers=H, json=body).status_code == 503


def test_uebernehmen_erfolg(umg):
    f, _, c = umg
    f.antworten.append([{"id": AID}])
    r = c.post(f"/api/pult/marke/vorschlaege/{VID}/uebernehmen", headers=H, json={"von": "Anna"})
    assert r.status_code == 200 and r.json() == {"auftrag": AID}
    assert f"pult_marke_uebernehmen('{VID}'::uuid, 'Anna')" in f.sql[0]


def test_uebernehmen_konflikt_422_mit_meldung(umg):
    """Review Focus 3: ein inzwischen ersetzter Vorschlag kommt als 422 mit der Meldung der DB an."""
    f, _, c = umg
    f.fehler += [_db_fehler("Inzwischen gibt es ein neueres Profil – bitte neu laden")]
    r = c.post(f"/api/pult/marke/vorschlaege/{VID}/uebernehmen", headers=H, json={"von": "Anna"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Inzwischen gibt es ein neueres Profil – bitte neu laden"


def test_uebernehmen_form_und_ausfall(umg):
    f, _, c = umg
    assert c.post("/api/pult/marke/vorschlaege/kaputt/uebernehmen", headers=H, json={"von": "A"}).status_code == 404
    for body in ({}, {"von": ""}, {"von": 3}):
        assert c.post(f"/api/pult/marke/vorschlaege/{VID}/uebernehmen", headers=H, json=body).status_code == 422
    assert f.sql == []
    f.fehler += [RuntimeError("db weg")]
    assert c.post(f"/api/pult/marke/vorschlaege/{VID}/uebernehmen", headers=H, json={"von": "A"}).status_code == 503


def test_verwerfen(umg):
    f, _, c = umg
    f.antworten.append([{"status": "verworfen"}])
    r = c.post(f"/api/pult/marke/vorschlaege/{VID}/verwerfen", headers=H, json={"von": "Anna"})
    assert r.status_code == 200 and r.json() == {"status": "verworfen"}
    assert f"pult_marke_verwerfen('{VID}'::uuid, 'Anna')" in f.sql[0]
    f.fehler += [_db_fehler("Inzwischen gibt es ein neueres Profil – bitte neu laden")]
    assert c.post(f"/api/pult/marke/vorschlaege/{VID}/verwerfen", headers=H, json={"von": "Anna"}).status_code == 422
    assert c.post(f"/api/pult/marke/vorschlaege/{VID}/verwerfen", headers=H, json={}).status_code == 422
    f.fehler += [RuntimeError("db weg")]
    assert c.post(f"/api/pult/marke/vorschlaege/{VID}/verwerfen", headers=H, json={"von": "A"}).status_code == 503


def test_hinweis_aus(umg):
    f, _, c = umg
    f.antworten.append([{"ok": True}])
    r = c.post(f"/api/pult/inhalte/{IID}/marke_hinweis_aus", headers=H)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert f"pult_marke_hinweis_aus('{IID}'::uuid)" in f.sql[0]
    f.antworten.append([{"ok": False}])
    assert c.post(f"/api/pult/inhalte/{IID}/marke_hinweis_aus", headers=H).status_code == 404
    assert c.post("/api/pult/inhalte/kaputt/marke_hinweis_aus", headers=H).status_code == 404
    f.fehler += [RuntimeError("db weg")]
    assert c.post(f"/api/pult/inhalte/{IID}/marke_hinweis_aus", headers=H).status_code == 503


# ─── Pult: Vorschau ─────────────────────────────────────────────────────


def _vorschau_antworten(f, vorschlag=VORSCHLAG, sicht=None):
    f.antworten += [[{"vorschlag": vorschlag, "mandant": "radhaus", "name": "Radhaus Jena",
                      "pflichtteil": {"impressum": "Impressum X"}}],
                    [{"bloecke": STUDIO}]]
    if sicht is not None:
        f.antworten.append(sicht)


def test_vorschau_rendert_mit_vorschlagsfarben_schriften_mustertext(umg):
    f, _, c = umg
    _vorschau_antworten(f)
    r = c.get(f"/api/pult/marke/vorschlaege/{VID}/vorschau?format=mail&bild_basis={BASIS}", headers=H)
    assert r.status_code == 200, r.text
    assert "#b45309" in r.text                                   # Akzent (Knopf)
    assert "Playfair Display" in r.text and "Manrope" in r.text  # Schriftpaar
    assert "Betreff Muster" in r.text and "Ueberschrift Muster" in r.text and "Absatz Muster" in r.text
    assert "Radhaus Jena" in r.text and "[Laden]" not in r.text  # ohne Logo: Wortmarke
    assert "name = 'studio'" in f.sql[1] and "fuer_alle" in f.sql[1]


def test_vorschau_handy_und_logo_aus_anhang(umg):
    f, ordner, c = umg
    (ordner / "firmenlogo.png").write_bytes(_bild())
    _vorschau_antworten(f, dict(VORSCHLAG, logo="anhang:firmenlogo.png"), _sicht(eigene=["firmenlogo.png"]))
    r = c.get(f"/api/pult/marke/vorschlaege/{VID}/vorschau?format=handy&bild_basis={BASIS}", headers=H)
    assert r.status_code == 200, r.text
    assert "firmenlogo.png" in r.text and "[Laden]" not in r.text


def test_vorschau_logo_eines_web_abgleichs_und_fremdes_logo(umg):
    f, ordner, c = umg
    for n in ("marke-radhaus-logo-0123456789.png", "fremd.png"):
        (ordner / n).write_bytes(_bild())
    _vorschau_antworten(f, dict(VORSCHLAG, logo="marke-radhaus-logo-0123456789.png"),
                        _sicht(gemeinsam=["marke-radhaus-logo-0123456789.png"]))
    r = c.get(f"/api/pult/marke/vorschlaege/{VID}/vorschau?bild_basis={BASIS}", headers=H)
    assert r.status_code == 200 and "marke-radhaus-logo-0123456789.png" in r.text
    # fremdes Bild und noch nicht geladenes web:<n> erscheinen nie
    for logo, sicht in (("fremd.png", _sicht(fremde=["fremd.png"])), ("web:2", None)):
        _vorschau_antworten(f, dict(VORSCHLAG, logo=logo), sicht)
        r = c.get(f"/api/pult/marke/vorschlaege/{VID}/vorschau?bild_basis={BASIS}", headers=H)
        assert r.status_code == 200 and "fremd.png" not in r.text and "web:2" not in r.text and "Radhaus Jena" in r.text


def test_vorschau_unbekannt_vorlage_fehlt_form_db_weg(umg):
    f, _, c = umg
    f.antworten.append([])
    assert c.get(f"/api/pult/marke/vorschlaege/{VID}/vorschau", headers=H).status_code == 404
    assert c.get("/api/pult/marke/vorschlaege/kaputt/vorschau", headers=H).status_code == 404
    f.antworten += [[{"vorschlag": VORSCHLAG, "mandant": "radhaus", "name": "R", "pflichtteil": {}}], []]
    assert c.get(f"/api/pult/marke/vorschlaege/{VID}/vorschau", headers=H).status_code == 422
    assert c.get(f"/api/pult/marke/vorschlaege/{VID}/vorschau?format=pdf", headers=H).status_code == 422
    assert c.get(f"/api/pult/marke/vorschlaege/{VID}/vorschau?bild_basis=http://x/", headers=H).status_code == 422
    f.fehler += [RuntimeError("db weg")]
    assert c.get(f"/api/pult/marke/vorschlaege/{VID}/vorschau", headers=H).status_code == 503


# ─── Arbeiter: Warteschlange ────────────────────────────────────────────


def test_naechster(umg):
    f, _, c = umg
    f.antworten.append([{"a": None}])
    assert c.post("/api/marke/arbeiter/naechster", headers=HB).json() == {"auftrag": None}
    job = {"id": AID, "art": "chat", "mandant": "radhaus", "firma": "Radhaus", "nachricht": "x", "kontext": {},
           "verlauf": [], "vorschlag": None}
    f.antworten.append([{"a": job}])
    assert c.post("/api/marke/arbeiter/naechster", headers=HB).json() == {"auftrag": job}
    assert "pult_marke_naechster('5 minutes'::interval)" in f.sql[-1]
    f.fehler += [_db_fehler("kaputt")]
    assert c.post("/api/marke/arbeiter/naechster", headers=HB).status_code == 422
    f.fehler += [RuntimeError("db weg")]
    assert c.post("/api/marke/arbeiter/naechster", headers=HB).status_code == 503


def test_weiter(umg):
    f, _, c = umg
    f.antworten.append([{"ok": True}])
    assert c.post(f"/api/marke/arbeiter/{AID}/weiter", headers=HB).json() == {"ok": True}
    assert f"pult_marke_verlaengern('{AID}'::uuid, '5 minutes'::interval)" in f.sql[0]
    assert c.post("/api/marke/arbeiter/kaputt/weiter", headers=HB).status_code == 404
    f.fehler += [RuntimeError("db weg")]
    assert c.post(f"/api/marke/arbeiter/{AID}/weiter", headers=HB).status_code == 503


def test_vorschlag_ablegen(umg):
    f, _, c = umg
    f.antworten.append([{"id": VID}])
    r = c.post(f"/api/marke/arbeiter/{AID}/vorschlag", headers=HB,
               json={"vorschlag": VORSCHLAG, "antwort": "Hier", "hinweise": ["Webseite x nicht lesbar: y"]})
    assert r.status_code == 200 and r.json() == {"vorschlag": VID}
    assert f"pult_marke_vorschlag('{AID}'::uuid" in f.sql[0] and "Betreff Muster" in f.sql[0] and "Hier" in f.sql[0]
    n = len(f.sql)
    for body in ({"antwort": "a"}, {"vorschlag": [], "antwort": "a"}, {"vorschlag": {}, "antwort": 1},
                 {"vorschlag": {}, "antwort": "a", "hinweise": "x"}, {"vorschlag": {}, "antwort": "a", "hinweise": [1]}):
        assert c.post(f"/api/marke/arbeiter/{AID}/vorschlag", headers=HB, json=body).status_code == 422, body
    assert len(f.sql) == n
    f.fehler += [_db_fehler("Auftrag ist nicht (mehr) in Arbeit")]
    r = c.post(f"/api/marke/arbeiter/{AID}/vorschlag", headers=HB, json={"vorschlag": {}, "antwort": "a"})
    assert r.status_code == 422 and "nicht (mehr) in Arbeit" in r.json()["detail"]
    f.fehler += [RuntimeError("db weg")]
    assert c.post(f"/api/marke/arbeiter/{AID}/vorschlag", headers=HB,
                  json={"vorschlag": {}, "antwort": "a"}).status_code == 503


def test_fertig(umg):
    f, _, c = umg
    f.antworten.append([{"n": 3}])
    r = c.post(f"/api/marke/arbeiter/{AID}/fertig", headers=HB, json={"antwort": "Fertig", "hinweise": []})
    assert r.status_code == 200 and r.json() == {"markiert": 3}
    assert f"pult_marke_fertig('{AID}'::uuid, 'Fertig'" in f.sql[0]
    assert c.post(f"/api/marke/arbeiter/{AID}/fertig", headers=HB, json={"hinweise": []}).status_code == 422
    f.fehler += [_db_fehler("Auftrag ist nicht (mehr) in Arbeit")]
    assert c.post(f"/api/marke/arbeiter/{AID}/fertig", headers=HB, json={"antwort": "a"}).status_code == 422
    f.fehler += [RuntimeError("db weg")]
    assert c.post(f"/api/marke/arbeiter/{AID}/fertig", headers=HB, json={"antwort": "a"}).status_code == 503


def test_zurueck(umg):
    f, _, c = umg
    f.antworten.append([{"s": "fehler"}])
    r = c.post(f"/api/marke/arbeiter/{AID}/zurueck", headers=HB, json={"antwort": "Logo kaputt"})
    assert r.status_code == 200 and r.json() == {"status": "fehler"}
    assert f"pult_marke_zurueck('{AID}'::uuid, 'Logo kaputt')" in f.sql[0]
    assert c.post(f"/api/marke/arbeiter/{AID}/zurueck", headers=HB, json={}).status_code == 422
    f.fehler += [RuntimeError("db weg")]
    assert c.post(f"/api/marke/arbeiter/{AID}/zurueck", headers=HB, json={"antwort": "a"}).status_code == 503


# ─── Arbeiter: Medien ───────────────────────────────────────────────────


def test_medien_firma_und_gemeinsam_ja_fremd_404(umg):
    f, ordner, c = umg
    for n in ("fremd.jpg", "eigen.jpg", "gemeinsam.jpg", "logo-laura-0123456789.png"):
        (ordner / n).write_bytes(b"BILD")
    sicht = _sicht(eigene=["eigen.jpg"], fremde=["fremd.jpg"], gemeinsam=["gemeinsam.jpg"])
    for name, status in (("fremd.jpg", 404), ("eigen.jpg", 200), ("gemeinsam.jpg", 200),
                         ("logo-laura-0123456789.png", 404)):
        f.antworten += [[JOB], sicht]
        r = c.get(f"/api/marke/arbeiter/{AID}/medien/{name}", headers=HB)
        assert r.status_code == status, name
        if status == 200:
            assert r.content == b"BILD"


def test_medien_nur_in_arbeit_und_nur_schlichte_namen(umg):
    f, ordner, c = umg
    (ordner / "foto.jpg").write_bytes(b"BILD")
    assert c.get(f"/api/marke/arbeiter/{AID}/medien/foto.jpg", headers=HB).status_code == 404     # Auftrag unbekannt
    f.antworten.append([dict(JOB, status="fertig")])
    assert c.get(f"/api/marke/arbeiter/{AID}/medien/foto.jpg", headers=HB).status_code == 404
    f.antworten.append([dict(JOB, gueltig=False)])
    assert c.get(f"/api/marke/arbeiter/{AID}/medien/foto.jpg", headers=HB).status_code == 404
    for boese in ("..%2Fx", ".versteckt.jpg", "x.exe"):
        assert c.get(f"/api/marke/arbeiter/{AID}/medien/{boese}", headers=HB).status_code == 404
    f.antworten += [[JOB], _sicht()]
    assert c.get(f"/api/marke/arbeiter/{AID}/medien/fehlt.jpg", headers=HB).status_code == 404


def test_medien_sicht_ausfall_503(umg):
    f, ordner, c = umg
    (ordner / "foto.jpg").write_bytes(b"BILD")
    f.antworten += [[JOB]]
    f.fehler += [None, RuntimeError("db weg")]
    assert c.get(f"/api/marke/arbeiter/{AID}/medien/foto.jpg", headers=HB).status_code == 503


# ─── Arbeiter: Logo ─────────────────────────────────────────────────────


def _logo(c, roh, hb=HB):
    return c.post(f"/api/marke/arbeiter/{AID}/logo", content=roh, headers=hb)


def test_logo_png_und_jpeg_werden_abgelegt_und_zugeordnet(umg):
    f, ordner, c = umg
    namen = {}
    for fmt, endung in (("PNG", "png"), ("JPEG", "jpg")):
        roh = _bild(fmt)
        f.antworten.append([JOB])
        r = _logo(c, roh)
        assert r.status_code == 200, r.text
        name = namen[fmt] = r.json()["name"]
        assert re.fullmatch(rf"marke-radhaus-logo-[0-9a-f]{{10}}\.{endung}", name)
        assert (ordner / name).read_bytes() == roh
        assert name in f.psql.sql[-1] and "'radhaus'" in f.psql.sql[-1] and "medien_mandant" in f.psql.sql[-1]
    # gleicher Inhalt -> gleicher Name (idempotent), keine zweite Datei
    f.antworten.append([JOB])
    assert _logo(c, _bild("PNG")).json()["name"] == namen["PNG"]
    assert len(list(ordner.iterdir())) == 2


def test_logo_zuordnung_scheitert_datei_wird_entfernt(umg):
    f, ordner, c = umg
    f.antworten.append([JOB])
    f.psql.fehler.append(RuntimeError("psql weg"))
    assert _logo(c, _bild()).status_code == 503
    assert list(ordner.iterdir()) == []


def test_logo_abgelehnt(umg):
    f, ordner, c = umg
    assert _logo(c, b"GIF89a....").status_code == 422                           # keine Signatur
    assert _logo(c, b"\x89PNG\r\n\x1a\nkaputt").status_code == 422              # Signatur, aber kein Bild
    assert _logo(c, _bild("PNG")[:-20]).status_code == 422                      # abgeschnitten
    assert _logo(c, _bild("PNG") + b"\0" * (2 * 1024 * 1024)).status_code == 422  # > 2 MB
    assert _logo(c, b"").status_code == 422
    assert f.sql == [] and list(ordner.iterdir()) == []


def test_logo_nur_im_laufenden_chat_auftrag(umg):
    f, ordner, c = umg
    assert _logo(c, _bild()).status_code == 404                                  # Auftrag unbekannt
    f.antworten.append([dict(JOB, status="fertig")])
    assert _logo(c, _bild()).status_code == 409
    f.antworten.append([dict(JOB, art="uebernehmen")])
    assert _logo(c, _bild()).status_code == 422
    assert list(ordner.iterdir()) == []


def test_logo_ordner_fehlt_503(umg, monkeypatch):
    f, _, c = umg
    monkeypatch.delenv("MARKETING_BILD_ORDNER")
    assert _logo(c, _bild()).status_code == 503


# ─── Arbeiter: Spiegeln und Markieren ───────────────────────────────────

GESTALT = {"akzent": "#b45309", "flaeche": "#3b2f2f", "schriften": {"anzeige": "playfair", "text": "manrope"}}


def test_spiegeln_erfolg(umg):
    f, _, c = umg
    f.antworten.append([{"n": 4}])
    r = c.post("/api/marke/arbeiter/spiegeln", headers=HB,
               json={"mandant": "radhaus", "gestalt": GESTALT, "stand": "2026-10-07 10:00 von Anna"})
    assert r.status_code == 200 and r.json() == {"ok": True, "fassung": 4}
    assert "pult_marke_spiegeln('radhaus'" in f.sql[0] and "'2026-10-07 10:00 von Anna'" in f.sql[0]


def test_spiegeln_ungueltig_ist_ok_false_mit_text_aus_dem_spiegel(umg):
    """R5: die DB gibt NULL zurueck und legt den Grund in marken_spiegel.fehler ab - kein Erfolg."""
    f, _, c = umg
    f.antworten += [[{"n": None}], [{"fehler": "Layout ungueltig: schriften.anzeige ist keine Schrift aus dem Register"}]]
    r = c.post("/api/marke/arbeiter/spiegeln", headers=HB,
               json={"mandant": "radhaus", "gestalt": {"akzent": "#b45309"}, "stand": "s"})
    assert r.status_code == 200
    assert r.json() == {"ok": False,
                        "fehler": "Layout ungueltig: schriften.anzeige ist keine Schrift aus dem Register"}
    assert "FROM marketing.marken_spiegel WHERE mandant = 'radhaus'" in f.sql[1]


def test_spiegeln_ungueltig_ohne_fehlertext_bleibt_ok_false(umg):
    f, _, c = umg
    f.antworten += [[{"n": None}], []]
    r = c.post("/api/marke/arbeiter/spiegeln", headers=HB, json={"mandant": "radhaus", "gestalt": {}, "stand": ""})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["fehler"]


def test_spiegeln_form_ablehnung_ausfall_und_groesse(umg):
    f, _, c = umg
    for body in ({"gestalt": {}, "stand": ""}, {"mandant": "radhaus", "stand": ""},
                 {"mandant": "radhaus", "gestalt": [], "stand": ""}, {"mandant": "radhaus", "gestalt": {}},
                 {"mandant": "radhaus", "gestalt": {}, "stand": "x" * 201}, {"mandant": "BIG", "gestalt": {}, "stand": ""}):
        assert c.post("/api/marke/arbeiter/spiegeln", headers=HB, json=body).status_code == 422, body
    assert c.post("/api/marke/arbeiter/spiegeln", headers=HB, content=b"kein json").status_code == 422
    assert f.sql == []
    gross = {"mandant": "radhaus", "gestalt": {"logo": "x" * (500 * 1024)}, "stand": ""}
    assert c.post("/api/marke/arbeiter/spiegeln", headers=HB, json=gross).status_code == 413
    f.fehler += [_db_fehler("Gestalt muss ein Objekt sein")]
    ok = {"mandant": "radhaus", "gestalt": {}, "stand": ""}
    assert c.post("/api/marke/arbeiter/spiegeln", headers=HB, json=ok).status_code == 422
    f.fehler += [RuntimeError("db weg")]
    assert c.post("/api/marke/arbeiter/spiegeln", headers=HB, json=ok).status_code == 503


def test_markieren(umg):
    f, _, c = umg
    f.antworten.append([{"n": 2}])
    r = c.post("/api/marke/arbeiter/markieren", headers=HB, json={"mandant": "radhaus"})
    assert r.status_code == 200 and r.json() == {"markiert": 2}
    assert "pult_marke_markieren('radhaus')" in f.sql[0]
    assert c.post("/api/marke/arbeiter/markieren", headers=HB, json={}).status_code == 422
    f.fehler += [_db_fehler("Unbekannt")]
    assert c.post("/api/marke/arbeiter/markieren", headers=HB, json={"mandant": "radhaus"}).status_code == 422
    f.fehler += [RuntimeError("db weg")]
    assert c.post("/api/marke/arbeiter/markieren", headers=HB, json={"mandant": "radhaus"}).status_code == 503


# ─── Fix-Runde 1 ────────────────────────────────────────────────────────


def test_vorschau_logo_ohne_datei_wortmarke(umg):
    f, _, c = umg
    _vorschau_antworten(f, dict(VORSCHLAG, logo="anhang:weg.png"), _sicht(eigene=["weg.png"]))
    r = c.get(f"/api/pult/marke/vorschlaege/{VID}/vorschau?bild_basis={BASIS}", headers=H)
    assert r.status_code == 200 and "weg.png" not in r.text and "Radhaus Jena" in r.text


def test_logo_zwei_getrennte_meldungen(umg, monkeypatch):
    f, _, c = umg
    from spaces.marketing.api import marke
    f.antworten.append([JOB])
    assert "Bildpunkte" not in _logo(c, _bild("PNG")).text
    monkeypatch.setattr(marke, "LOGO_PIXEL_MAX", 10)
    f.antworten.append([JOB])
    r = _logo(c, _bild("PNG"))
    assert r.status_code == 422 and "Bildpunkte" in r.json()["detail"]
    f.antworten.append([JOB])
    r = _logo(c, b"\x89PNG\r\n\x1a\nkaputt")
    assert r.status_code == 422 and "Bildpunkte" not in r.json()["detail"]


def test_spiegeln_413_meldung_passt(umg):
    _, _, c = umg
    gross = {"mandant": "radhaus", "gestalt": {"logo": "x" * (500 * 1024)}, "stand": ""}
    r = c.post("/api/marke/arbeiter/spiegeln", headers=HB, json=gross)
    assert r.status_code == 413 and "Zwischenstand" not in r.json()["detail"]


def test_stand_aufraeumen_ablehnung_422(umg):
    f, _, c = umg
    f.fehler += [_db_fehler("Aufraeumen abgelehnt")]
    assert c.get("/api/pult/marke?mandant=radhaus", headers=H).status_code == 422


def test_hinweis_aus_ablehnung_422(umg):
    f, _, c = umg
    f.fehler += [_db_fehler("Hinweis abgelehnt")]
    assert c.post(f"/api/pult/inhalte/{IID}/marke_hinweis_aus", headers=H).status_code == 422


# ─── Task 5: Firmenliste fuer den Abgleich, Urheber beim Uebernehmen ────


def test_firmen_fuer_den_abgleich(umg):
    """Der Arbeiter vergleicht Marke.md mit dem Spiegel: aktive Firmen + Spiegel-Teil des Standard-Layouts."""
    f, _, c = umg
    f.antworten.append([
        {"id": "radhaus", "name": "Radhaus", "stand": "2026-10-07 10:00 von Anna",
         "gestalt": {**GESTALT, "grund": "#ffffff", "logo": "data:image/png;base64,AAAA"}},
        {"id": "fin2gether", "name": "fin2gether", "stand": None, "gestalt": None}])
    r = c.get("/api/marke/arbeiter/firmen", headers=HB)
    assert r.status_code == 200
    assert r.json() == {"firmen": [
        {"id": "radhaus", "name": "Radhaus", "stand": "2026-10-07 10:00 von Anna",
         "gestalt": {**GESTALT, "logo": "data:image/png;base64,AAAA"}},
        {"id": "fin2gether", "name": "fin2gether", "stand": "", "gestalt": {}}]}
    assert "m.aktiv" in f.sql[0] and "marken_spiegel" in f.sql[0] and "l.standard" in f.sql[0]
    f.fehler += [RuntimeError("db weg")]
    assert c.get("/api/marke/arbeiter/firmen", headers=HB).status_code == 503


def test_naechster_uebernehmen_traegt_den_urheber(umg):
    f, _, c = umg
    job = {"id": AID, "art": "uebernehmen", "mandant": "radhaus", "firma": "Radhaus", "nachricht": "",
           "kontext": {}, "verlauf": [], "vorschlag": {"id": VID, "vorschlag": VORSCHLAG}}
    f.antworten += [[{"a": job}], [{"von": "Anna"}]]
    assert c.post("/api/marke/arbeiter/naechster", headers=HB).json() == {"auftrag": {**job, "von": "Anna"}}
    assert "entschieden_von" in f.sql[1] and VID in f.sql[1]


def test_naechster_urheber_nicht_lesbar_liefert_auftrag_trotzdem(umg):
    """Der Auftrag ist schon vergeben: ein Lesefehler beim Urheber darf ihn nicht verlieren."""
    f, _, c = umg
    job = {"id": AID, "art": "uebernehmen", "mandant": "radhaus", "firma": "Radhaus", "nachricht": "",
           "kontext": {}, "verlauf": [], "vorschlag": {"id": VID, "vorschlag": VORSCHLAG}}
    f.antworten.append([{"a": job}])
    f.fehler += [None, RuntimeError("db weg")]
    assert c.post("/api/marke/arbeiter/naechster", headers=HB).json() == {"auftrag": job}
