"""Eigene Marketing-Oberflaeche, Stufe A: Huelle, Schluessel, CSRF, Sprachwahl, Woerterbuecher,
Datenzugang und jede lesende Seite. Ohne Datenbank: daten.* wird durch Attrappen ersetzt; der
Datenzugang selbst wird gegen gefaelschte Pult-Funktionen geprueft. Alle Firmen und Inhalte sind
erfunden."""
from __future__ import annotations

import logging
import string
from datetime import date

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.params import Param
from fastapi.testclient import TestClient

from spaces.marketing.api import oberflaeche
from spaces.marketing.api.oberflaeche import daten, huelle, sprache

KEY = "test-ui-schluessel"
H = {"Authorization": f"Bearer {KEY}"}
FIRMEN = [{"id": "alpha", "name": "Alpha GmbH", "aktiv": True},
          {"id": "beta", "name": "Beta AG", "aktiv": True},
          {"id": "ruhend", "name": "Ruhend KG", "aktiv": False}]
SEITEN = ("/oberflaeche/newsletter", "/oberflaeche/plan", "/oberflaeche/marke", "/oberflaeche/reels",
          "/oberflaeche/podcast")
LOGO = "data:image/png;base64,iVBORw0KGgo="


class Attrappe:
    """Ersetzt daten.*: liefert vorbereitete Antworten und merkt sich jeden Aufruf."""
    def __init__(self):
        self.aufrufe: list[tuple] = []
        self.firmen = list(FIRMEN)
        self.newsletter: list[dict] = []
        self.plan: list[dict] = []
        self.kampagnen: list[dict] = []
        self.marke: dict = {}
        self.reels: dict[bool, list[dict]] = {False: [], True: []}
        self.podcast: list[dict] = []
        self.fehler: dict[str, daten.DatenFehler] = {}

    def _antwort(self, name, *werte):
        self.aufrufe.append((name, *werte))
        if name in self.fehler:
            raise self.fehler[name]

    def einsetzen(self, monkeypatch):
        def mach(name, holen):
            def f(*werte):
                self._antwort(name, *werte)
                return holen(*werte)
            monkeypatch.setattr(daten, name, f)
        mach("firmen", lambda: self.firmen)
        mach("newsletter", lambda m: self.newsletter)
        mach("plan", lambda m, von, bis: self.plan)
        mach("kampagnen", lambda m: self.kampagnen)
        mach("marke", lambda m: self.marke)
        mach("reels", lambda m, archiv: self.reels[archiv])
        mach("podcast", lambda m: self.podcast)


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


def namen(attrappe: Attrappe) -> list[str]:
    return [x[0] for x in attrappe.aufrufe]


# --- Schluessel -----------------------------------------------------------------------------------

@pytest.mark.parametrize("pfad", SEITEN)
def test_ohne_schluessel_in_der_umgebung_ist_alles_zu(c, a, monkeypatch, pfad):
    monkeypatch.delenv(huelle.SCHLUESSEL_ENV)
    r = c.get(pfad, headers=H)
    assert r.status_code == 503
    assert "nicht eingerichtet" in r.text
    assert a.aufrufe == []


def test_leerer_schluessel_in_der_umgebung_ist_zu(c, a, monkeypatch):
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, "   ")
    assert c.get("/oberflaeche/newsletter", headers={"Authorization": "Bearer    "}).status_code == 503


@pytest.mark.parametrize("kopf", [None, "", "Bearer", "Bearer ", "Basic " + KEY, "Bearer falsch", KEY,
                                  f"Bearer {KEY}x"])
def test_fehlender_oder_falscher_schluessel_401(c, a, kopf):
    headers = {} if kopf is None else {"Authorization": kopf}
    r = c.get("/oberflaeche/newsletter", headers=headers)
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"].startswith("Bearer")
    assert a.aufrufe == []


def test_schluessel_steht_nie_im_log_oder_auf_der_seite(c, a, caplog):
    caplog.set_level(logging.DEBUG)
    r1 = c.get("/oberflaeche/newsletter", headers={"Authorization": "Bearer geraten-123"})
    r2 = c.get("/oberflaeche/newsletter", headers=H)
    assert r1.status_code == 401 and r2.status_code == 200
    for text in (caplog.text, r1.text, r2.text):
        assert KEY not in text
        assert "geraten-123" not in text


def test_schluessel_mit_kleinem_bearer_wort(c, a):
    assert c.get("/oberflaeche/newsletter", headers={"Authorization": f"bearer {KEY}"}).status_code == 200


def test_post_ohne_schluessel_401(c, a):
    r = c.post("/oberflaeche/sprache", data={"csrf": huelle.csrf_marke(), "sprache": "en"})
    assert r.status_code == 401
    assert "set-cookie" not in r.headers


# --- Rahmen und Koepfe ----------------------------------------------------------------------------

@pytest.mark.parametrize("pfad", SEITEN)
def test_jede_seite_hat_strenge_koepfe_und_kein_skript(c, a, pfad):
    r = c.get(pfad, headers=H)
    assert r.status_code == 200
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script-src" not in csp and "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in r.headers["Cache-Control"]
    assert "<script" not in r.text.lower()
    assert " style=" not in r.text and "<style" not in r.text
    assert "<title>Marketing – " in r.text       # die App erkennt den Reiter am Titel (/Marketing/i)


def test_fehlerseiten_haben_dieselben_koepfe(c, a, monkeypatch):
    for r in (c.get("/oberflaeche/newsletter"),
              c.post("/oberflaeche/sprache", headers=H, data={"sprache": "en"})):
        assert r.headers["Content-Security-Policy"] == huelle.CSP
        assert r.headers["X-Frame-Options"] == "DENY"


def test_start_leitet_auf_newsletter(c):
    for pfad in ("/oberflaeche", "/oberflaeche/"):
        r = c.get(pfad, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/oberflaeche/newsletter"


def test_stylesheet_ohne_schluessel(c):
    r = c.get("/oberflaeche/stil.css")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/css")


def test_menue_markiert_die_aktive_seite(c, a):
    r = c.get("/oberflaeche/plan", headers=H)
    assert '<a href="/oberflaeche/plan" class="aktiv" aria-current="page">' in r.text
    for pfad in SEITEN:
        assert f'href="{pfad}"' in r.text


def test_formulare_tragen_die_csrf_marke(c, a):
    r = c.get("/oberflaeche/newsletter", headers=H)
    marke = huelle.csrf_marke()
    assert len(marke) == 64
    assert r.text.count(f'name="csrf" value="{marke}"') == 2   # Sprache und Firma


def test_csrf_marke_haengt_am_schluessel(monkeypatch):
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, "eins")
    erste = huelle.csrf_marke()
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, "zwei")
    assert huelle.csrf_marke() != erste
    monkeypatch.delenv(huelle.SCHLUESSEL_ENV)
    assert huelle.csrf_marke() == ""


# --- Formulare: Sprache und Firma ----------------------------------------------------------------

def test_sprache_setzen(c, a):
    r = c.post("/oberflaeche/sprache", headers=H, follow_redirects=False,
               data={"csrf": huelle.csrf_marke(), "sprache": "en", "zurueck": "/oberflaeche/plan?monat=2026-12"})
    assert r.status_code == 303
    assert r.headers["location"] == "/oberflaeche/plan?monat=2026-12"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("sprache=en") and "HttpOnly" in cookie and "Path=/oberflaeche" in cookie
    assert "samesite=lax" in cookie.lower()


@pytest.mark.parametrize("csrf", [None, "", "falsch", "0" * 64])
def test_sprache_ohne_gueltige_csrf_marke_403(c, a, csrf):
    data = {"sprache": "en"} if csrf is None else {"sprache": "en", "csrf": csrf}
    r = c.post("/oberflaeche/sprache", headers=H, data=data, follow_redirects=False)
    assert r.status_code == 403
    assert "set-cookie" not in r.headers


def test_fremde_herkunft_wird_abgewiesen(c, a):
    r = c.post("/oberflaeche/sprache", headers={**H, "Sec-Fetch-Site": "cross-site"},
               data={"csrf": huelle.csrf_marke(), "sprache": "en"}, follow_redirects=False)
    assert r.status_code == 403


def test_unbekannte_sprache_422(c, a):
    r = c.post("/oberflaeche/sprache", headers=H, data={"csrf": huelle.csrf_marke(), "sprache": "fr"})
    assert r.status_code == 422
    assert "set-cookie" not in r.headers


@pytest.mark.parametrize("ziel", ["https://fremd.example/", "//fremd.example/x", "/oberflaechex", "/api/pult/inhalte",
                                  "/oberflaeche/\\x", "/oberflaeche//x", "/oberflaeche/\nx", ""])
def test_kein_offener_redirect(c, a, ziel):
    r = c.post("/oberflaeche/sprache", headers=H, follow_redirects=False,
               data={"csrf": huelle.csrf_marke(), "sprache": "de", "zurueck": ziel})
    assert r.status_code == 303
    assert r.headers["location"] == "/oberflaeche/newsletter"


def test_zu_grosses_formular_wird_abgewiesen(c, a):
    r = c.post("/oberflaeche/sprache", headers=H,
               data={"csrf": huelle.csrf_marke(), "sprache": "en", "fuell": "x" * 5000})
    assert r.status_code == 403


def test_firma_waehlen_und_benutzen(c, a):
    r = c.post("/oberflaeche/firma", headers=H, follow_redirects=False,
               data={"csrf": huelle.csrf_marke(), "firma": "beta", "zurueck": "/oberflaeche/podcast"})
    assert r.status_code == 303 and r.headers["location"] == "/oberflaeche/podcast"
    assert r.headers["set-cookie"].startswith("mk_firma=beta")
    c.cookies.set("mk_firma", "beta")
    c.get("/oberflaeche/podcast", headers=H)
    assert ("podcast", "beta") in a.aufrufe


@pytest.mark.parametrize("firma", ["ruhend", "gibtsnicht", ""])
def test_firma_nur_aktive(c, a, firma):
    r = c.post("/oberflaeche/firma", headers=H, data={"csrf": huelle.csrf_marke(), "firma": firma})
    assert r.status_code == 422
    assert "set-cookie" not in r.headers


def test_firmen_cookie_ist_eingabe_und_wird_geprueft(c, a):
    c.cookies.set("mk_firma", "ruhend")
    c.get("/oberflaeche/podcast", headers=H)
    assert ("podcast", "alpha") in a.aufrufe


def test_firma_waehlen_regeln():
    assert huelle.firma_waehlen("beta", FIRMEN) == "beta"
    assert huelle.firma_waehlen("ruhend", FIRMEN) == "alpha"
    assert huelle.firma_waehlen(None, FIRMEN + [{"id": huelle.VORGABE_FIRMA, "aktiv": True}]) == huelle.VORGABE_FIRMA
    assert huelle.firma_waehlen("x", [{"id": "ruhend", "aktiv": False}]) is None


def test_ohne_aktive_firma_eine_klare_seite(c, a):
    a.firmen = [{"id": "ruhend", "name": "Ruhend KG", "aktiv": False}]
    r = c.get("/oberflaeche/newsletter", headers=H)
    assert r.status_code == 200
    assert "keine aktive Firma" in r.text
    assert namen(a) == ["firmen"]


def test_eine_firma_braucht_keinen_umschalter(c, a):
    a.firmen = [FIRMEN[0]]
    r = c.get("/oberflaeche/newsletter", headers=H)
    assert 'action="/oberflaeche/firma"' not in r.text


def test_umschalter_zeigt_nur_aktive_firmen(c, a):
    r = c.get("/oberflaeche/newsletter", headers=H)
    assert "Alpha GmbH" in r.text and "Beta AG" in r.text and "Ruhend KG" not in r.text
    assert 'value="alpha" aria-pressed="true"' in r.text


# --- Datenfehler auf der Seite ---------------------------------------------------------------------

@pytest.mark.parametrize("art, status, satz", [
    ("nicht_verbunden", 503, "nicht verbunden"),
    ("nicht_erreichbar", 503, "nicht erreichbar"),
    ("abgelehnt", 422, "Grund: &lt;b&gt;kaputt&lt;/b&gt;")])
def test_datenfehler_wird_zur_fehlerseite(c, a, art, status, satz):
    a.fehler["firmen"] = daten.DatenFehler(art, "<b>kaputt</b>")
    r = c.get("/oberflaeche/newsletter", headers=H)
    assert r.status_code == status
    assert satz in r.text
    assert "<b>kaputt</b>" not in r.text


def test_datenfehler_auf_englisch(c, a):
    a.fehler["newsletter"] = daten.DatenFehler("nicht_erreichbar")
    r = c.get("/oberflaeche/newsletter", headers={**H, "Accept-Language": "en"})
    assert r.status_code == 503
    assert '<html lang="en">' in r.text and "currently unavailable" in r.text


def test_firma_waehlen_bei_datenfehler(c, a):
    a.fehler["firmen"] = daten.DatenFehler("nicht_erreichbar")
    r = c.post("/oberflaeche/firma", headers=H, data={"csrf": huelle.csrf_marke(), "firma": "beta"})
    assert r.status_code == 503


# --- Sprachwahl ----------------------------------------------------------------------------------

@pytest.mark.parametrize("kopf, erwartet", [
    ("en-US,en;q=0.9,de;q=0.8", "en"), ("de-DE,de;q=0.9,en;q=0.8", "de"), ("fr-FR, de;q=0.5", "de"),
    ("fr", None), ("", None), (None, None), ("de;q=0, en;q=0.1", "en"), ("en;q=0.4, de;q=0.6", "de"),
    ("*", None), ("en;q=abc, de", "de"), ("EN", "en"), (";;,,", None), ("en;q=0.5, de;q=0.5", "en")])
def test_accept_language(kopf, erwartet):
    assert sprache.aus_accept_language(kopf) == erwartet


def test_cookie_vor_kopf_vor_deutsch():
    assert sprache.waehlen("en", "de") == "en"
    assert sprache.waehlen("de", "en") == "de"
    assert sprache.waehlen("fr", "en") == "en"
    assert sprache.waehlen(None, None) == "de"
    assert sprache.waehlen("", "fr") == "de"


def test_seite_folgt_accept_language(c, a):
    r = c.get("/oberflaeche/plan", headers={**H, "Accept-Language": "en-GB,en;q=0.9"})
    assert '<html lang="en">' in r.text
    assert r.headers["Content-Language"] == "en"
    assert "Campaign plan" in r.text and "Kampagnenplan" not in r.text


def test_cookie_schlaegt_accept_language(c, a):
    c.cookies.set("sprache", "de")
    r = c.get("/oberflaeche/plan", headers={**H, "Accept-Language": "en"})
    assert '<html lang="de">' in r.text and "Kampagnenplan" in r.text


def test_ohne_angabe_deutsch(c, a):
    r = c.get("/oberflaeche/reels", headers=H)
    assert '<html lang="de">' in r.text
    assert 'value="de" aria-pressed="true"' in r.text


def test_fehlerseite_ohne_anmeldung_folgt_der_sprache(c, a):
    r = c.get("/oberflaeche/marke", headers={"Accept-Language": "en"})
    assert r.status_code == 401
    assert '<html lang="en">' in r.text and "Not signed in" in r.text


def _platzhalter(vorlage: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(vorlage) if name}


@pytest.mark.parametrize("modul", oberflaeche.MODULE_MIT_TEXTEN, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_jeder_schluessel_in_beiden_sprachen(modul):
    texte = modul.TEXTE
    assert set(texte) == set(sprache.SPRACHEN)
    de, en = texte["de"], texte["en"]
    assert set(de) == set(en), f"fehlt in en: {set(de) - set(en)}, fehlt in de: {set(en) - set(de)}"
    for schluessel in de:
        for s in sprache.SPRACHEN:
            assert isinstance(texte[s][schluessel], str) and texte[s][schluessel].strip(), (s, schluessel)
        if schluessel != "datum":        # das Datum ordnet die Teile je Sprache anders an
            assert _platzhalter(de[schluessel]) == _platzhalter(en[schluessel]), schluessel


@pytest.mark.parametrize("s, erwartet", [("de", "07.10.2026"), ("en", "7 Oct 2026")])
def test_datum_je_sprache(s, erwartet):
    assert huelle.datum(s, "2026-10-07T14:32:00+00:00") == erwartet
    assert huelle.datum(s, "kaputt") == "kaputt"


# --- Datenzugang ---------------------------------------------------------------------------------

class FalscheRoute:
    def __init__(self, antwort=None, fehler=None):
        self.antwort, self.fehler, self.werte = antwort, fehler, None

    def __call__(self, **werte):
        self.werte = werte
        if self.fehler:
            raise self.fehler
        return self.antwort


@pytest.mark.parametrize("aufruf, modul, name, feld", [
    (lambda: daten.firmen(), "medien_mandant", "mandanten_liste", "mandanten"),
    (lambda: daten.newsletter("alpha"), "pult", "inhalte", "inhalte"),
    (lambda: daten.plan("alpha", "2026-12-01", "2026-12-31"), "plan", "pult_lesen", "eintraege"),
    (lambda: daten.kampagnen("alpha"), "kampagnen", "pult_lesen", "kampagnen"),
    (lambda: daten.reels("alpha", True), "video", "pult_liste", "auftraege"),
    (lambda: daten.podcast("alpha"), "ton", "pult_liste", "auftraege")])
def test_daten_rufen_die_pult_funktionen_mit_allen_argumenten(monkeypatch, aufruf, modul, name, feld):
    import importlib
    m = importlib.import_module(f"spaces.marketing.api.{modul}")
    route = FalscheRoute({feld: [{"id": "x1"}, "kein dict", {"id": "x2"}]})
    monkeypatch.setattr(m, name, route)
    monkeypatch.setenv(daten.PULT_ENV, "pult-test")
    assert [z["id"] for z in aufruf()] == ["x1", "x2"]
    assert route.werte["x_pult_key"] == "pult-test"
    assert not any(isinstance(w, Param) for w in route.werte.values())   # keine FastAPI-Vorgabe durchgerutscht


def test_daten_newsletter_und_reels_argumente(monkeypatch):
    from spaces.marketing.api import pult, video
    nl, rl = FalscheRoute({"inhalte": []}), FalscheRoute({"auftraege": []})
    monkeypatch.setattr(pult, "inhalte", nl)
    monkeypatch.setattr(video, "pult_liste", rl)
    daten.newsletter("alpha")
    daten.reels("alpha", False)
    assert nl.werte["art"] == "newsletter" and nl.werte["status"] is None and nl.werte["mandant"] == "alpha"
    assert rl.werte["archiv"] is False and rl.werte["limit"] == daten.REELS_MAX


def test_daten_marke(monkeypatch):
    from spaces.marketing.api import marke
    route = FalscheRoute({"name": "Alpha GmbH"})
    monkeypatch.setattr(marke, "marke_stand", route)
    assert daten.marke("alpha") == {"name": "Alpha GmbH"}
    assert route.werte["mandant"] == "alpha" and route.werte["alle"] is False


@pytest.mark.parametrize("fehler, art", [
    (HTTPException(503, "misconfigured: MARKETING_PULT_KEY fehlt"), "nicht_verbunden"),
    (HTTPException(401, "Pult-Schluessel fehlt oder falsch"), "nicht_verbunden"),
    (HTTPException(503, "Marketing-Datenbank nicht erreichbar"), "nicht_erreichbar"),
    (HTTPException(422, "Unbekannter Mandant"), "abgelehnt"),
    (HTTPException(404, "Unbekannter Mandant"), "abgelehnt"),
    (RuntimeError("ssh: verbindung zu geheimer-rechner abgebrochen"), "nicht_erreichbar")])
def test_daten_fehler_arten(monkeypatch, caplog, fehler, art):
    from spaces.marketing.api import ton
    monkeypatch.setattr(ton, "pult_liste", FalscheRoute(fehler=fehler))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(daten.DatenFehler) as f:
        daten.podcast("alpha")
    assert f.value.art == art
    if art == "abgelehnt":
        assert f.value.grund == "Unbekannter Mandant"
    assert "geheimer-rechner" not in caplog.text


def test_daten_ohne_pult_schluessel_ist_nicht_verbunden(monkeypatch):
    """Echte Pult-Funktion: ohne MARKETING_PULT_KEY antwortet sie 503 misconfigured, ohne DB-Zugriff."""
    monkeypatch.delenv(daten.PULT_ENV, raising=False)
    with pytest.raises(daten.DatenFehler) as f:
        daten.newsletter("alpha")
    assert f.value.art == "nicht_verbunden"


# --- Seiten --------------------------------------------------------------------------------------

def _nl(titel, status, offen=0, fassungen=1, fmt="seite", erstellt="2026-11-02T09:00:00+00:00"):
    return {"id": titel, "art": "newsletter", "titel": titel, "status": status, "offene_rueckmeldungen": offen,
            "fassungen": fassungen, "format": fmt, "erstellt_am": erstellt}


def test_newsletter_reiter_und_karten(c, a):
    a.newsletter = [_nl("Herbstgruss", "entwurf"), _nl("Rueckfrage", "entwurf", offen=2),
                    _nl("Zur Freigabe", "eingereicht"), _nl("Draussen", "freigegeben", fassungen=0)]
    r = c.get("/oberflaeche/newsletter", headers=H)
    assert ("newsletter", "alpha") in a.aufrufe
    assert "in Arbeit (1)" in r.text and "zurückgegeben (1)" in r.text and "zur Freigabe (1)" in r.text
    assert "Herbstgruss" in r.text and "Rueckfrage" not in r.text      # Vorgabe-Reiter: in Arbeit
    assert "Fassung 1 · erstellt 02.11.2026 · Collage" in r.text
    r = c.get("/oberflaeche/newsletter?status=zurueckgegeben", headers=H)
    assert "Rueckfrage" in r.text and "Offene Rückmeldungen: 2" in r.text
    r = c.get("/oberflaeche/newsletter?status=freigegeben", headers=H)
    assert "Noch keine Fassung" in r.text


def test_newsletter_englisch(c, a):
    a.newsletter = [_nl("Herbstgruss", "entwurf")]
    r = c.get("/oberflaeche/newsletter", headers={**H, "Accept-Language": "en"})
    assert "in progress (1)" in r.text and "Version 1 · created 2 Nov 2026 · Collage" in r.text
    assert "<h1>Newsletters</h1>" in r.text


def test_newsletter_unbekannter_status_und_leer(c, a):
    r = c.get("/oberflaeche/newsletter?status=<script>", headers=H)
    assert r.status_code == 200 and "<script>" not in r.text
    assert "Keine Newsletter mit diesem Status." in r.text


def test_newsletter_titel_wird_maskiert(c, a):
    a.newsletter = [_nl('<img src=x onerror="alert(1)">', "entwurf")]
    r = c.get("/oberflaeche/newsletter", headers=H)
    assert "<img src=x" not in r.text and "&lt;img src=x" in r.text


def test_newsletter_blaettern(c, a):
    a.newsletter = [_nl(f"Nummer {i:02d}", "entwurf") for i in range(30)]
    r = c.get("/oberflaeche/newsletter", headers=H)
    assert "Nummer 23" in r.text and "Nummer 24" not in r.text and "Seite 1 von 2" in r.text
    r = c.get("/oberflaeche/newsletter?seite=2", headers=H)
    assert "Nummer 24" in r.text and "Nummer 29" in r.text and "Nummer 00" not in r.text
    r = c.get("/oberflaeche/newsletter?seite=abc", headers=H)
    assert "Seite 1 von 2" in r.text


def test_plan_monat_kampagnen_und_kalender(c, a):
    a.plan = [{"id": "p1", "datum": "2026-12-03", "art": "newsletter", "titel": "Adventsmail", "status": "vorschlag",
               "notiz": "Mit Gutschein"},
              {"id": "p2", "datum": "2026-12-03", "art": "post", "titel": "Teamfoto", "status": "geplant"},
              {"id": "p3", "datum": "2026-12-10", "art": "video", "titel": "Abgesagtes Video", "status": "abgesagt"}]
    a.kampagnen = [{"id": "k1", "name": "Winteraktion", "ziel": "Mehr Anfragen", "zielgruppe": "verteiler",
                    "von": "2026-12-01", "bis": "2026-12-24", "status": "laeuft",
                    "beitraege": [{"datum": "2026-12-03", "art": "newsletter", "titel": "Adventsmail",
                                   "status": "geplant"}]}]
    r = c.get("/oberflaeche/plan?monat=2026-12", headers=H)
    assert ("plan", "alpha", "2026-12-01", "2026-12-31") in a.aufrufe
    assert "Winteraktion" in r.text and "läuft" in r.text and "Zielgruppe: Verteiler" in r.text
    assert "01.12.2026 bis 24.12.2026" in r.text and "Beiträge: 1" in r.text
    assert "Do 03.12.2026" in r.text and "Adventsmail" in r.text and "Mit Gutschein" in r.text
    assert "Vorschlag vom Agenten" in r.text and "Vorschläge vom Agenten in diesem Monat: 1" in r.text
    assert "Abgesagtes Video" not in r.text
    assert "Dezember 2026" in r.text
    assert "monat=2026-11" in r.text and "monat=2027-01" in r.text
    r = c.get("/oberflaeche/plan?monat=2026-12&abgesagt=1", headers=H)
    assert "Abgesagtes Video" in r.text
    r = c.get("/oberflaeche/plan?monat=2026-12&art=post", headers=H)
    assert "Teamfoto" in r.text and "Adventsmail</p>" not in r.text


def test_plan_englisch(c, a):
    a.plan = [{"id": "p1", "datum": "2026-12-03", "art": "aktion", "titel": "Messe", "status": "geplant"}]
    r = c.get("/oberflaeche/plan?monat=2026-12", headers={**H, "Accept-Language": "en"})
    assert "December 2026" in r.text and "Thu 3 Dec 2026" in r.text and "Promotion" in r.text
    assert "No campaign yet." in r.text


def test_plan_ohne_kampagnen_bleibt_sichtbar(c, a):
    a.fehler["kampagnen"] = daten.DatenFehler("nicht_erreichbar")
    r = c.get("/oberflaeche/plan?monat=2026-12", headers=H)
    assert r.status_code == 200 and "In diesem Monat ist noch nichts geplant." in r.text


@pytest.mark.parametrize("roh, erwartet", [("2026-02", date(2026, 2, 1)), ("2026-13", None), ("kaputt", None),
                                           ("1999-01", None), (None, None)])
def test_plan_monat_lesen(roh, erwartet):
    heute = date(2026, 11, 24)
    assert oberflaeche.plan.monat_lesen(roh, heute) == (erwartet or date(2026, 11, 1))


def test_plan_monatsgrenzen():
    assert oberflaeche.plan.monat_ende(date(2028, 2, 1)) == date(2028, 2, 29)
    assert oberflaeche.plan.nachbar(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert oberflaeche.plan.nachbar(date(2026, 1, 1), -1) == date(2025, 12, 1)


def _marke():
    return {"mandant": "alpha", "name": "Alpha GmbH",
            "spiegel": {"gestalt": {"akzent": "#c2185b", "flaeche": "#fce4ec", "logo": LOGO,
                                    "logo_dunkel": "javascript:alert(1)",
                                    "schriften": {"anzeige": "inter", "text": "<b>"}},
                        "stand": "2026-11-20", "gespiegelt_am": "2026-11-20T08:00:00", "fehler": None},
            "profil": {"werte": {"grund": "#ffffff", "text": "red", "stimme": "warm"},
                       "abschnitte": {"Kurz gesagt": "Wir bauen Dinge.\nGut.", "Eigenes": "Extra"}},
            "abschnitt_namen": ["Kurz gesagt", "Ton"],
            "pflichtteil": {"impressum": "Alpha GmbH, Musterweg 1", "vermittlerangaben": "  "},
            "profil_hinweise": ["Marke.md: text ungültig"]}


def test_marke_profil(c, a):
    a.marke = _marke()
    r = c.get("/oberflaeche/marke", headers=H)
    assert ("marke", "alpha") in a.aufrufe
    assert "<h1>Marke · Alpha GmbH</h1>" in r.text
    assert 'fill="#c2185b"' in r.text and "Akzent #c2185b" in r.text and "Fläche #fce4ec" in r.text
    assert "Text red" not in r.text                         # keine gueltige Farbe, kein Feld
    assert "Überschrift: inter" in r.text and "&lt;b&gt;" not in r.text
    assert f'<img src="{LOGO}"' in r.text and "javascript:" not in r.text
    assert "Stand: 2026-11-20" in r.text and "Marke.md: text ungültig" in r.text
    assert "warm" in r.text and "Alpha GmbH, Musterweg 1" in r.text
    assert r.text.count('<dd class="leer">fehlt</dd>') == 2
    assert "Wir bauen Dinge.<br>Gut." in r.text
    assert r.text.index("Kurz gesagt") < r.text.index(">Ton<") < r.text.index("Eigenes")
    assert "Noch leer" in r.text


def test_marke_englisch_und_spiegelfehler(c, a):
    d = _marke()
    d["spiegel"]["fehler"] = "Logo zu gross"
    a.marke = d
    r = c.get("/oberflaeche/marke", headers={**H, "Accept-Language": "en"})
    assert "Accent #c2185b" in r.text and "Legal information" in r.text and "Imprint" in r.text
    assert "Mirror out of date since 20 Nov 2026: Logo zu gross" in r.text


def test_marke_leer(c, a):
    a.marke = {"name": "Beta AG"}
    r = c.get("/oberflaeche/marke", headers=H)
    assert r.status_code == 200 and "Noch kein Branding" in r.text and "Noch keine festgelegt" in r.text


def _reel(titel, status, **mehr):
    return {"id": titel, "titel": titel, "status": status, "art": "bilder", "briefing": {"format": "quadrat"},
            "dauer_s": 21.5, "erstellt_am": "2026-11-21T10:00:00", **mehr}


def test_reels_reiter_und_karten(c, a):
    a.reels[False] = [_reel("Fertiges Reel", "fertig"), _reel("Im Bau", "in_arbeit"),
                      _reel("Kaputt", "fehler", befund_text="Der Bau hat zu lange gedauert."),
                      _reel("KI-Reel", "bilder_pruefen", briefing={"ki": {"anzahl": 4}})]
    a.reels[True] = [_reel("Altes Reel", "fertig")]
    r = c.get("/oberflaeche/reels", headers=H)
    assert ("reels", "alpha", False) in a.aufrufe and ("reels", "alpha", True) in a.aufrufe
    assert "Fertig (1)" in r.text and "In Arbeit (2)" in r.text and "Fehlgeschlagen (1)" in r.text
    assert "Archiv (1)" in r.text
    assert "Fertiges Reel" in r.text and "Aus Bildern · Quadrat 1:1 · 21,5 s · 21.11.2026" in r.text
    r = c.get("/oberflaeche/reels?reiter=arbeit", headers=H)
    assert "Bilder prüfen" in r.text and "Aus KI-Bildern" in r.text and "Hochformat 9:16" in r.text
    r = c.get("/oberflaeche/reels?reiter=fehler", headers=H)
    assert "Der Bau hat zu lange gedauert." in r.text
    r = c.get("/oberflaeche/reels?reiter=archiv", headers=H)
    assert "Altes Reel" in r.text


def test_reels_vorgabe_ohne_fertige_und_englisch(c, a):
    a.reels[False] = [_reel("Im Bau", "offen")]
    r = c.get("/oberflaeche/reels?reiter=unbekannt", headers={**H, "Accept-Language": "en"})
    assert "Im Bau" in r.text and "waiting" in r.text and "21.5 s" in r.text and "Square 1:1" in r.text
    assert 'href="/oberflaeche/reels?reiter=arbeit" class="aktiv"' in r.text


def test_reels_leer(c, a):
    r = c.get("/oberflaeche/reels", headers=H)
    assert "Noch keine Reels in diesem Bereich." in r.text


def test_podcast_liste(c, a):
    a.podcast = [{"id": "t1", "titel": "Folge eins", "status": "fertig", "sprache": "de", "dauer_s": 754,
                  "angelegt_von": "agent", "erstellt_am": "2026-11-19T12:00:00",
                  "skript": [{"sprecher": "Anna", "text": "Hallo <alle>"}, {"sprecher": "Ben", "text": "Hi"},
                             {"sprecher": "Anna", "text": "Los"}]},
                 {"id": "t2", "titel": "Folge zwei", "status": "fehler", "befund": "Stimme fehlt", "skript": "kaputt"}]
    r = c.get("/oberflaeche/podcast", headers=H)
    assert ("podcast", "alpha") in a.aufrufe
    assert "Folge eins" in r.text and "fertig" in r.text and "12:34 min" in r.text
    assert "19.11.2026 · vom Agenten · Sprache der Folge: de · Sprecher: Anna, Ben" in r.text
    assert "Anna: Hallo &lt;alle&gt;" in r.text and "<details>" in r.text
    assert "Stimme fehlt" in r.text and "von dir" in r.text


def test_podcast_englisch_und_leer(c, a):
    r = c.get("/oberflaeche/podcast", headers={**H, "Accept-Language": "en"})
    assert "No episodes yet." in r.text and "<h2>Episodes</h2>" in r.text


# --- Einbau in die API --------------------------------------------------------------------------

def test_server_haengt_die_oberflaeche_ein(monkeypatch, a):
    from spaces.marketing.api import server
    pfade = {getattr(r, "path", "") for r in server.app.routes}
    for pfad in SEITEN + ("/oberflaeche/sprache", "/oberflaeche/firma", "/oberflaeche/stil.css"):
        assert pfad in pfade
    # Der X-API-Key-Schutz gilt nur fuer /api/*; die Oberflaeche prueft ihren eigenen Schluessel
    monkeypatch.setattr(server, "API_KEY", "api-schluessel")
    sc = TestClient(server.app)
    assert sc.get("/oberflaeche/newsletter", headers=H).status_code == 200
    assert sc.get("/oberflaeche/newsletter").status_code == 401
