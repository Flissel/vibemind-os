"""Marketing-Oberflaeche, Stufe B Tag 1: Vorlagen, Entwurf aus Vorlage, signierte Vorschau im Rahmen.
Ohne Datenbank: daten.* wird durch Attrappen ersetzt, der Datenzugang gegen gefaelschte Pult-Funktionen
geprueft. Alle Firmen, Vorlagen und Inhalte sind erfunden. Versendet wird in keinem Test etwas."""
from __future__ import annotations

import html
import logging
import re
import urllib.parse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.params import Param
from fastapi.responses import HTMLResponse, Response
from fastapi.testclient import TestClient

from spaces.marketing.api import oberflaeche
from spaces.marketing.api.oberflaeche import daten, huelle, vorlagen

KEY = "test-ui-schluessel"
H = {"Authorization": f"Bearer {KEY}"}
IID = "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
FIRMEN = [{"id": "alpha", "name": "Alpha GmbH", "aktiv": True}]
VORLAGEN = [{"name": "fruehling", "beschreibung": "Frühlingsgruß mit Angebot", "format": "bloecke",
             "fuer_alle": False},
            {"name": "collage-gross", "beschreibung": "Große Collage", "format": "seite", "fuer_alle": True}]
MAIL = "<!doctype html><html><body><table><tr><td style=\"color:#123\">Hallo Muster</td></tr></table></body></html>"


class Attrappe:
    def __init__(self):
        self.aufrufe: list[tuple] = []
        self.vorlagen = list(VORLAGEN)
        self.entwurf = {"inhalt": {"id": IID, "mandant": "alpha", "titel": "Newsletter Probe", "status": "entwurf"},
                        "fassungen": [{"fassung": 1, "format": "bloecke"}]}
        self.fehler: dict[str, daten.DatenFehler] = {}

    def einsetzen(self, monkeypatch):
        def mach(name, holen):
            def f(*werte):
                self.aufrufe.append((name, *werte))
                if name in self.fehler:
                    raise self.fehler[name]
                return holen(*werte)
            monkeypatch.setattr(daten, name, f)
        mach("firmen", lambda: FIRMEN)
        mach("vorlagen", lambda m: self.vorlagen)
        mach("vorlage_vorschau", lambda m, n, fmt: MAIL)
        mach("aus_vorlage", lambda m, n, t: IID)
        mach("entwurf", lambda i: self.entwurf)
        mach("entwurf_vorschau", lambda i, n, fmt: MAIL)

    def namen(self) -> list[str]:
        return [x[0] for x in self.aufrufe]


@pytest.fixture
def a(monkeypatch):
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, KEY)
    attrappe = Attrappe()
    attrappe.einsetzen(monkeypatch)
    return attrappe


@pytest.fixture
def c():
    app = FastAPI()
    app.include_router(oberflaeche.router)
    return TestClient(app)


def rahmen_src(text: str) -> str:
    treffer = re.search(r'<iframe[^>]* src="([^"]+)"', text)
    assert treffer, "kein Rahmen"
    return html.unescape(treffer.group(1))


def anlegen(c, **werte):
    daten_ = {"csrf": huelle.csrf_marke(), "vorlage": "fruehling", "titel": "Mein Entwurf", **werte}
    return c.post("/oberflaeche/vorlagen/anlegen", headers=H, data=daten_, follow_redirects=False)


# --- Seite Vorlagen -----------------------------------------------------------------------------

def test_vorlagen_seite_listet_und_bleibt_streng(c, a):
    r = c.get("/oberflaeche/vorlagen", headers=H)
    assert r.status_code == 200
    csp = r.headers["Content-Security-Policy"]
    assert csp == huelle.CSP_RAHMEN and "frame-src 'self'" in csp
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert "script-src" not in csp and "unsafe-inline" not in csp
    assert "<script" not in r.text.lower() and " style=" not in r.text and "<style" not in r.text
    assert "<title>Marketing – Vorlagen</title>" in r.text
    assert "fruehling" in r.text and "collage-gross" in r.text and "für alle Firmen" in r.text
    assert "<iframe" not in r.text                         # erst nach der Wahl
    assert '<a href="/oberflaeche/vorlagen" class="aktiv" aria-current="page">' in r.text
    assert "Versendet wird nichts" in r.text


def test_andere_seiten_erlauben_keinen_rahmen(c, a, monkeypatch):
    monkeypatch.setattr(daten, "newsletter", lambda m: [])
    r = c.get("/oberflaeche/newsletter", headers=H)
    assert r.headers["Content-Security-Policy"] == huelle.CSP and "frame-src" not in huelle.CSP
    assert 'href="/oberflaeche/vorlagen"' in r.text        # im Menue


def test_wahl_zeigt_rahmen_und_formular(c, a):
    r = c.get("/oberflaeche/vorlagen?wahl=fruehling", headers=H)
    assert r.status_code == 200
    assert re.search(r'<iframe[^>]* sandbox=""', r.text)
    src = rahmen_src(r.text)
    pfad, _, q = src.partition("?")
    assert pfad == "/oberflaeche/vorschau/vorlage/fruehling"
    werte = dict(urllib.parse.parse_qsl(q))
    assert werte["firma"] == "alpha" and werte["format"] == "mail" and werte["fassung"] == "0"
    assert len(werte["sig"]) == 64 and KEY not in src
    assert 'action="/oberflaeche/vorlagen/anlegen"' in r.text
    assert f'name="csrf" value="{huelle.csrf_marke()}"' in r.text
    assert 'name="vorlage" value="fruehling"' in r.text
    assert "[Bild: …]" in r.text


def test_wahl_handy(c, a):
    r = c.get("/oberflaeche/vorlagen?wahl=fruehling&format=handy", headers=H)
    assert '<iframe class="handy"' in r.text
    assert dict(urllib.parse.parse_qsl(rahmen_src(r.text).partition("?")[2]))["format"] == "handy"


def test_collage_nur_vorschau_ohne_anlegen(c, a):
    r = c.get("/oberflaeche/vorlagen?wahl=collage-gross", headers=H)
    assert "<iframe" in r.text
    assert 'action="/oberflaeche/vorlagen/anlegen"' not in r.text
    assert "Bilder beauftragt" in r.text


def test_unbekannte_wahl(c, a):
    r = c.get("/oberflaeche/vorlagen?wahl=gibt-es-nicht", headers=H)
    assert r.status_code == 200 and "Diese Vorlage gibt es hier nicht." in r.text and "<iframe" not in r.text


def test_keine_vorlagen(c, a):
    a.vorlagen = []
    r = c.get("/oberflaeche/vorlagen", headers=H)
    assert "noch keine freigegebene Vorlage" in r.text


def test_englisch(c, a):
    r = c.get("/oberflaeche/vorlagen?wahl=fruehling", headers={**H, "Accept-Language": "en"})
    assert "<title>Marketing – Templates</title>" in r.text and "Create draft" in r.text


def test_vorlagen_ohne_schluessel(c, a):
    assert c.get("/oberflaeche/vorlagen").status_code == 401
    assert a.aufrufe == []


# --- Entwurf anlegen ----------------------------------------------------------------------------

def test_anlegen_legt_entwurf_an_und_leitet_weiter(c, a):
    r = anlegen(c, titel="  Mein Entwurf  ")
    assert r.status_code == 303
    assert r.headers["location"] == f"/oberflaeche/vorlagen/entwurf/{IID}"
    assert ("aus_vorlage", "alpha", "fruehling", "Mein Entwurf") in a.aufrufe


@pytest.mark.parametrize("werte, text", [
    ({"titel": ""}, "Titel fehlt"),
    ({"titel": "   "}, "Titel fehlt"),
    ({"titel": "x" * 201}, "Titel fehlt"),
    ({"vorlage": "Boese/../x"}, "gibt es hier nicht"),
    ({"vorlage": "gibt-es-nicht"}, "gibt es hier nicht"),
    ({"vorlage": "collage-gross"}, "Collage-Vorlagen")])
def test_anlegen_abgewiesen(c, a, werte, text):
    r = anlegen(c, **werte)
    assert r.status_code == 422 and text in r.text
    assert "aus_vorlage" not in a.namen()


def test_anlegen_ohne_csrf_oder_fremd(c, a):
    r1 = c.post("/oberflaeche/vorlagen/anlegen", headers=H, data={"vorlage": "fruehling", "titel": "x"})
    r2 = c.post("/oberflaeche/vorlagen/anlegen", headers={**H, "Sec-Fetch-Site": "cross-site"},
                data={"csrf": huelle.csrf_marke(), "vorlage": "fruehling", "titel": "x"})
    assert r1.status_code == 403 and r2.status_code == 403
    assert "aus_vorlage" not in a.namen()


def test_anlegen_ohne_schluessel(c, a):
    r = c.post("/oberflaeche/vorlagen/anlegen",
               data={"csrf": huelle.csrf_marke(), "vorlage": "fruehling", "titel": "x"})
    assert r.status_code == 401 and a.aufrufe == []


def test_anlegen_fehler_der_api(c, a):
    a.fehler["aus_vorlage"] = daten.DatenFehler("abgelehnt", "Es gibt nur noch Collagen")
    r = anlegen(c)
    assert r.status_code == 422 and "Es gibt nur noch Collagen" in r.text


def test_anlegen_kaputte_id(c, a, monkeypatch):
    monkeypatch.setattr(daten, "aus_vorlage", lambda m, n, t: "keine-uuid")
    assert anlegen(c).status_code == 503


# --- Seite Entwurf ------------------------------------------------------------------------------

def test_entwurf_zeigt_vorschau_der_neuesten_fassung(c, a):
    r = c.get(f"/oberflaeche/vorlagen/entwurf/{IID}", headers=H)
    assert r.status_code == 200
    assert r.headers["Content-Security-Policy"] == huelle.CSP_RAHMEN
    assert "Entwurf angelegt: Newsletter Probe" in r.text and "Fassung 1" in r.text
    src = rahmen_src(r.text)
    assert src.startswith(f"/oberflaeche/vorschau/entwurf/{IID}?")
    assert dict(urllib.parse.parse_qsl(src.partition("?")[2]))["fassung"] == "1"
    assert "<script" not in r.text.lower() and " style=" not in r.text


def test_entwurf_fremder_firma(c, a):
    a.entwurf["inhalt"]["mandant"] = "beta"
    r = c.get(f"/oberflaeche/vorlagen/entwurf/{IID}", headers=H)
    assert r.status_code == 422 and "anderen Firma" in r.text and "<iframe" not in r.text


def test_entwurf_kaputte_id(c, a):
    r = c.get("/oberflaeche/vorlagen/entwurf/nicht-uuid", headers=H)
    assert r.status_code == 422 and "entwurf" not in a.namen()


def test_entwurf_collage_ohne_rahmen(c, a):
    a.entwurf["fassungen"] = [{"fassung": 2, "format": "seite"}]
    r = c.get(f"/oberflaeche/vorlagen/entwurf/{IID}", headers=H)
    assert r.status_code == 200 and "<iframe" not in r.text


def test_entwurf_ohne_fassung(c, a):
    a.entwurf["fassungen"] = []
    r = c.get(f"/oberflaeche/vorlagen/entwurf/{IID}", headers=H)
    assert "noch keine Fassung" in r.text and "<iframe" not in r.text


# --- Vorschau im Rahmen -------------------------------------------------------------------------

def test_vorschau_mit_signatur_ohne_bearer(c, a):
    src = vorlagen.vorschau_adresse("vorlage", "alpha", "fruehling", 0, "mail")
    r = c.get(src)                                          # der Rahmen bekommt den Schluessel nicht
    assert r.status_code == 200 and r.text == MAIL
    csp = r.headers["Content-Security-Policy"]
    assert "sandbox" in csp and "script-src" not in csp and "frame-ancestors 'self'" in csp
    assert "form-action 'none'" in csp and "img-src data:" in csp
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "no-store" in r.headers["Cache-Control"]
    assert ("vorlage_vorschau", "alpha", "fruehling", "mail") in a.aufrufe


def test_vorschau_entwurf(c, a):
    r = c.get(vorlagen.vorschau_adresse("entwurf", "alpha", IID, 1, "handy"))
    assert r.status_code == 200
    assert ("entwurf_vorschau", IID, 1, "handy") in a.aufrufe


def _mit(src: str, **neu) -> str:
    pfad, _, q = src.partition("?")
    werte = dict(urllib.parse.parse_qsl(q))
    pfad = neu.pop("pfad", pfad)
    werte.update(neu)
    return pfad + "?" + urllib.parse.urlencode(werte)


@pytest.mark.parametrize("aenderung", [
    {"firma": "beta"}, {"format": "handy"}, {"fassung": "1"}, {"sig": "0" * 64}, {"sig": ""},
    {"pfad": "/oberflaeche/vorschau/vorlage/andere"}, {"pfad": "/oberflaeche/vorschau/entwurf/fruehling"},
    {"bis": "1"}, {"bis": "x"}, {"bis": "99999999999999"}])
def test_vorschau_veraendert_wird_abgewiesen(c, a, aenderung):
    src = vorlagen.vorschau_adresse("vorlage", "alpha", "fruehling", 0, "mail")
    r = c.get(_mit(src, **aenderung))
    assert r.status_code == 403
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert a.namen() == []


def test_vorschau_abgelaufen(c, a):
    src = vorlagen.vorschau_adresse("vorlage", "alpha", "fruehling", 0, "mail",
                                    jetzt=1_000_000.0)
    assert c.get(src).status_code == 403


def test_signatur_haengt_am_schluessel(monkeypatch):
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, "eins")
    src = vorlagen.vorschau_adresse("vorlage", "alpha", "fruehling", 0, "mail")
    werte = dict(urllib.parse.parse_qsl(src.partition("?")[2]))
    args = ("vorlage", "alpha", "fruehling", "0", "mail", werte["bis"], werte["sig"])
    assert vorlagen.signatur_ok(*args)
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, "zwei")
    assert not vorlagen.signatur_ok(*args)


def test_vorschau_ohne_schluessel_in_der_umgebung(c, a, monkeypatch):
    src = vorlagen.vorschau_adresse("vorlage", "alpha", "fruehling", 0, "mail")
    monkeypatch.delenv(huelle.SCHLUESSEL_ENV)
    assert c.get(src).status_code == 503 and a.namen() == []
    assert vorlagen._signatur(("a",)) == ""


def test_vorschau_datenfehler_zeigt_keinen_rohtext(c, a, caplog):
    caplog.set_level(logging.DEBUG)
    a.fehler["vorlage_vorschau"] = daten.DatenFehler("abgelehnt", "geheimer Rohtext")
    r = c.get(vorlagen.vorschau_adresse("vorlage", "alpha", "fruehling", 0, "mail"))
    assert r.status_code == 404 and "geheimer Rohtext" not in r.text and "nicht verfügbar" in r.text


def test_vorschau_entwurf_kaputte_id(c, a):
    r = c.get(vorlagen.vorschau_adresse("entwurf", "alpha", "nicht-uuid", 1, "mail"))
    assert r.status_code == 404 and a.namen() == []


def test_schluessel_nie_in_seite_oder_log(c, a, caplog):
    caplog.set_level(logging.DEBUG)
    r1 = c.get("/oberflaeche/vorlagen?wahl=fruehling", headers=H)
    r2 = c.get(rahmen_src(r1.text))
    for text in (r1.text, r2.text, caplog.text):
        assert KEY not in text


# --- Datenzugang --------------------------------------------------------------------------------

class FalscheRoute:
    def __init__(self, antwort=None, fehler=None):
        self.antwort, self.fehler, self.werte = antwort, fehler, None

    def __call__(self, **werte):
        self.werte = werte
        if self.fehler:
            raise self.fehler
        return self.antwort


def _pult_key(monkeypatch):
    monkeypatch.setenv(daten.PULT_ENV, "pult-test")


def test_daten_vorlagen(monkeypatch):
    from spaces.marketing.api import pult
    route = FalscheRoute({"vorlagen": [{"name": "a1"}, {"name": 3}, "kein dict"]})
    monkeypatch.setattr(pult, "vorlagen", route)
    _pult_key(monkeypatch)
    assert daten.vorlagen("alpha") == [{"name": "a1"}]
    assert route.werte == {"mandant": "alpha", "status": "freigegeben", "x_pult_key": "pult-test"}


def test_daten_vorlage_vorschau(monkeypatch):
    from spaces.marketing.api import pult
    route = FalscheRoute(HTMLResponse(MAIL))
    monkeypatch.setattr(pult, "vorlage_vorschau", route)
    _pult_key(monkeypatch)
    assert daten.vorlage_vorschau("alpha", "fruehling", "handy") == MAIL
    assert route.werte["bild_basis"] == "" and route.werte["format"] == "handy"
    assert not any(isinstance(w, Param) for w in route.werte.values())


def test_daten_vorschau_kein_html(monkeypatch):
    from spaces.marketing.api import pult
    monkeypatch.setattr(pult, "vorschau", FalscheRoute(Response(b"%PDF", media_type="application/pdf")))
    with pytest.raises(daten.DatenFehler) as f:
        daten.entwurf_vorschau(IID, 1, "mail")
    assert f.value.art == "nicht_erreichbar"


def test_daten_aus_vorlage(monkeypatch):
    from spaces.marketing.api import pult
    route = FalscheRoute({"id": IID})
    monkeypatch.setattr(pult, "inhalt_aus_vorlage", route)
    _pult_key(monkeypatch)
    assert daten.aus_vorlage("alpha", "fruehling", "Titel") == IID
    assert route.werte == {"payload": {"vorlage": "fruehling", "titel": "Titel", "mandant": "alpha"},
                           "x_pult_key": "pult-test"}
    monkeypatch.setattr(pult, "inhalt_aus_vorlage", FalscheRoute({"warnungen": []}))
    with pytest.raises(daten.DatenFehler):
        daten.aus_vorlage("alpha", "fruehling", "Titel")


def test_daten_aus_vorlage_nur_collagen(monkeypatch):
    from spaces.marketing.api import pult
    monkeypatch.setattr(pult, "inhalt_aus_vorlage", FalscheRoute(fehler=HTTPException(409, "nur noch Collagen")))
    with pytest.raises(daten.DatenFehler) as f:
        daten.aus_vorlage("alpha", "fruehling", "Titel")
    assert f.value.art == "abgelehnt" and "Collagen" in f.value.grund


def test_daten_entwurf_und_vorschau(monkeypatch):
    from spaces.marketing.api import pult
    kopf, vs = FalscheRoute({"inhalt": {}}), FalscheRoute(HTMLResponse(MAIL))
    monkeypatch.setattr(pult, "inhalt", kopf)
    monkeypatch.setattr(pult, "vorschau", vs)
    assert daten.entwurf(IID) == {"inhalt": {}}
    assert daten.entwurf_vorschau(IID, 2, "mail") == MAIL
    assert vs.werte["fassung"] == 2 and vs.werte["bild_basis"] == "" and kopf.werte["iid"] == IID


def test_server_haengt_die_neuen_wege_ein():
    from spaces.marketing.api import server
    pfade = {getattr(r, "path", "") for r in server.app.routes}
    for pfad in ("/oberflaeche/vorlagen", "/oberflaeche/vorlagen/anlegen", "/oberflaeche/vorlagen/entwurf/{iid}",
                 "/oberflaeche/vorschau/vorlage/{ref}", "/oberflaeche/vorschau/entwurf/{ref}"):
        assert pfad in pfade
