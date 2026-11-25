"""Marketing-Oberflaeche, Stufe B Punkt 2: Reels und Podcast-Folgen bestellen hinter dem Kosten-Schalter
(MARKETING_BESTELLEN, Vorgabe aus), Reel neu bauen, offene Auftraege abbrechen. Ohne Datenbank: daten.* sind
Attrappen, die Datenwege werden gegen gefaelschte Pult-Funktionen geprueft. Alle Firmen, Bilder und Texte
sind erfunden; bestellt oder versendet wird in keinem Test etwas."""
from __future__ import annotations

import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from spaces.marketing.api import oberflaeche, ton, video
from spaces.marketing.api.oberflaeche import bestellen, daten, huelle

KEY = "test-ui-schluessel"
H = {"Authorization": f"Bearer {KEY}"}
FIRMEN = [{"id": "alpha", "name": "Alpha GmbH", "aktiv": True}]
BILDER = ["ast.jpg", "fruehling.jpg", "laden-innen.png"]
R_OFFEN, R_FERTIG, R_FEHLER = ("aaaaaaaa-1111-4111-8111-111111111111", "bbbbbbbb-2222-4222-8222-222222222222",
                               "cccccccc-3333-4333-8333-333333333333")
F_OFFEN, F_FERTIG = "dddddddd-4444-4444-8444-444444444444", "eeeeeeee-5555-4555-8555-555555555555"
FREMD = "ffffffff-6666-4666-8666-666666666666"
NEU = "12345678-1234-4234-8234-123456789abc"


class Attrappe:
    def __init__(self):
        self.aufrufe: list[tuple] = []
        self.fehler: dict[str, daten.DatenFehler] = {}
        self.reels = [
            {"id": R_OFFEN, "titel": "Wochenmarkt", "status": "offen", "art": "bilder", "briefing": {}},
            {"id": R_FERTIG, "titel": "Frühling", "status": "fertig", "art": "bilder", "briefing": {},
             "datei": "vid-0123abcd-fruehling.mp4"},
            {"id": R_FEHLER, "titel": "Sommer", "status": "fehler", "art": "bilder", "briefing": {},
             "befund_text": "Der Bau hat zu lange gedauert."}]
        self.folgen = [
            {"id": F_OFFEN, "titel": "Folge 2", "status": "offen", "skript": [{"sprecher": "Anna", "text": "Hallo"}]},
            {"id": F_FERTIG, "titel": "Folge 1", "status": "fertig", "datei": "pod-0123abcd-folge-1.mp3"}]

    def einsetzen(self, monkeypatch):
        def mach(name, holen):
            def f(*werte):
                self.aufrufe.append((name, *werte))
                if name in self.fehler:
                    raise self.fehler[name]
                return holen(*werte)
            monkeypatch.setattr(daten, name, f)
        mach("firmen", lambda: FIRMEN)
        mach("bilder", lambda m: list(BILDER))
        mach("reels", lambda m, archiv: [] if archiv else self.reels)
        mach("podcast", lambda m: self.folgen)
        mach("reel_bestellen", lambda m, t, b: NEU)
        mach("reel_nochmal", lambda m, aid, w: NEU)
        mach("reel_abbrechen", lambda m, aid: None)
        mach("folge_bestellen", lambda m, t, s, sk: NEU)
        mach("folge_abbrechen", lambda aid: None)

    def namen(self) -> list[str]:
        return [x[0] for x in self.aufrufe]

    def von(self, name: str) -> list[tuple]:
        return [x[1:] for x in self.aufrufe if x[0] == name]


@pytest.fixture
def a(monkeypatch):
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, KEY)
    monkeypatch.delenv(bestellen.SCHALTER_ENV, raising=False)
    attrappe = Attrappe()
    attrappe.einsetzen(monkeypatch)
    return attrappe


@pytest.fixture
def an(monkeypatch, a):
    monkeypatch.setenv(bestellen.SCHALTER_ENV, "an")
    return a


@pytest.fixture
def c():
    app = FastAPI()
    app.include_router(oberflaeche.router)
    return TestClient(app)


def _csrf() -> str:
    return huelle.csrf_marke()


def _post(c, pfad, werte, **kopf):
    return c.post(pfad, headers={**H, **kopf}, data={"csrf": _csrf(), **werte}, follow_redirects=False)


REEL = {"titel": "Neue Kuchen", "thema": "Frühlingskuchen", "sprechertext": "Ab Montag gibt es neue Kuchen.",
        "hook": "", "dauer_s": "30", "format": "reel", "bild:ast.jpg": "1", "bild:fruehling.jpg": "1"}
FOLGE = {"titel": "Folge 3", "sprache": "de",
         "skript": "Anna: Willkommen zur dritten Folge.\nheute geht es um Kuchen.\n\nBen: Und um Blumen."}


# --- Kosten-Schalter ------------------------------------------------------------------------------

@pytest.mark.parametrize("wert", [None, "", "aus", "ja", "1", "true", "AN-ja"])
def test_schalter_nur_mit_an(monkeypatch, wert):
    if wert is None:
        monkeypatch.delenv(bestellen.SCHALTER_ENV, raising=False)
    else:
        monkeypatch.setenv(bestellen.SCHALTER_ENV, wert)
    assert bestellen.an() is False


def test_schalter_an_auch_gross_und_mit_rand(monkeypatch):
    monkeypatch.setenv(bestellen.SCHALTER_ENV, " An ")
    assert bestellen.an() is True


def test_aus_reels_seite_zeigt_gesperrtes_formular(c, a):
    r = c.get("/oberflaeche/reels", headers=H, cookies={"mk_firma": "alpha"})
    assert r.status_code == 200
    assert "Neues Reel bestellen" in r.text and "Kosten-Schalter" in r.text
    assert re.search(r'action="/oberflaeche/reels/bestellen"[^>]*>.*?<fieldset disabled>', r.text, re.S)
    assert "bilder" not in a.namen()       # aus: die Bilder werden gar nicht erst gelesen


def test_aus_podcast_seite_zeigt_gesperrtes_formular(c, a):
    r = c.get("/oberflaeche/podcast", headers=H, cookies={"mk_firma": "alpha"})
    assert r.status_code == 200 and "Neue Folge bestellen" in r.text
    assert re.search(r'action="/oberflaeche/podcast/bestellen"[^>]*>.*?<fieldset disabled>', r.text, re.S)


@pytest.mark.parametrize("pfad, werte", [("/oberflaeche/reels/bestellen", REEL),
                                         (f"/oberflaeche/reels/{R_FERTIG}/nochmal", {"wunsch": "heller"}),
                                         ("/oberflaeche/podcast/bestellen", FOLGE)])
def test_aus_lehnt_jede_bestellung_ab(c, a, pfad, werte):
    r = _post(c, pfad, werte, cookie="mk_firma=alpha")
    assert r.status_code == 422 and "Kosten-Schalter" in r.text
    assert not {"reel_bestellen", "reel_nochmal", "folge_bestellen", "bilder", "reels"} & set(a.namen())


def test_an_reels_seite_mit_bildwahl(c, an):
    r = c.get("/oberflaeche/reels", headers=H, cookies={"mk_firma": "alpha"})
    assert "<fieldset disabled>" not in r.text and "Kosten-Schalter" not in r.text
    for n in BILDER:
        assert f'name="bild:{n}"' in r.text
    assert 'src="/oberflaeche/bild/ast.jpg?' in r.text


def test_an_bilder_nicht_lesbar_sagt_es(c, an):
    an.fehler["bilder"] = daten.DatenFehler("nicht_erreichbar")
    r = c.get("/oberflaeche/reels", headers=H, cookies={"mk_firma": "alpha"})
    assert r.status_code == 200 and "gerade nicht lesbar" in r.text


# --- Reel bestellen ---------------------------------------------------------------------------------

def test_reel_bestellen(c, an):
    r = _post(c, "/oberflaeche/reels/bestellen", {**REEL, "bild:laden-innen.png": "1"}, cookie="mk_firma=alpha")
    assert r.status_code == 303 and r.headers["location"] == "/oberflaeche/reels?reiter=arbeit&bestellt=1"
    ((firma, titel, briefing),) = an.von("reel_bestellen")
    assert firma == "alpha" and titel == "Neue Kuchen"
    assert briefing == {"thema": "Frühlingskuchen", "sprechertext": "Ab Montag gibt es neue Kuchen.", "hook": "",
                        "dauer_s": 30, "bilder": ["ast.jpg", "fruehling.jpg", "laden-innen.png"]}


def test_reel_format_quer_steht_im_briefing(c, an):
    _post(c, "/oberflaeche/reels/bestellen", {**REEL, "format": "quer"}, cookie="mk_firma=alpha")
    assert an.von("reel_bestellen")[0][2]["format"] == "quer"


@pytest.mark.parametrize("aenderung", [
    {"bild:ast.jpg": "0", "bild:fruehling.jpg": "0"},                     # kein Bild
    {"bild:fremd.jpg": "1"},                                              # nicht unter den Bildern der Firma
    {"dauer_s": "90"}, {"dauer_s": "zehn"}, {"format": "kino"},
    {"thema": "   "}, {"sprechertext": ""}, {"titel": ""}, {"titel": "x" * 121}, {"hook": "x" * 121},
])
def test_reel_falsche_eingaben(c, an, aenderung):
    r = _post(c, "/oberflaeche/reels/bestellen", {**REEL, **aenderung}, cookie="mk_firma=alpha")
    assert r.status_code == 422 and "reel_bestellen" not in an.namen()


def test_reel_hoechstens_acht_bilder(c, an, monkeypatch):
    viele = [f"bild{i}.jpg" for i in range(9)]
    monkeypatch.setattr(daten, "bilder", lambda m: viele)
    r = _post(c, "/oberflaeche/reels/bestellen", {**REEL, **{f"bild:{n}": "1" for n in viele}},
              cookie="mk_firma=alpha")
    assert r.status_code == 422 and "reel_bestellen" not in an.namen()


def test_reel_ohne_schluessel_oder_marke(c, an):
    assert c.post("/oberflaeche/reels/bestellen", data={"csrf": _csrf(), **REEL}, follow_redirects=False
                  ).status_code == 401
    assert c.post("/oberflaeche/reels/bestellen", headers=H, data=REEL, follow_redirects=False).status_code == 403
    assert "reel_bestellen" not in an.namen()


def test_reel_ablehnung_der_api_zeigt_grund(c, an):
    an.fehler["reel_bestellen"] = daten.DatenFehler("abgelehnt", "Diese Stimme gibt es nicht")
    r = _post(c, "/oberflaeche/reels/bestellen", REEL, cookie="mk_firma=alpha")
    assert r.status_code == 422 and "Diese Stimme gibt es nicht" in r.text


# --- Neu bauen und abbrechen ----------------------------------------------------------------------

def test_karten_tragen_die_passenden_knoepfe(c, an):
    arbeit = c.get("/oberflaeche/reels?reiter=arbeit", headers=H, cookies={"mk_firma": "alpha"}).text
    assert f'action="/oberflaeche/reels/{R_OFFEN}/abbrechen"' in arbeit
    fertig = c.get("/oberflaeche/reels?reiter=fertig", headers=H, cookies={"mk_firma": "alpha"}).text
    assert f'action="/oberflaeche/reels/{R_FERTIG}/nochmal"' in fertig
    assert f"/{R_FERTIG}/abbrechen" not in fertig


def test_nochmal_fertig_braucht_wunsch(c, an):
    r = _post(c, f"/oberflaeche/reels/{R_FERTIG}/nochmal", {"wunsch": " "}, cookie="mk_firma=alpha")
    assert r.status_code == 422 and "reel_nochmal" not in an.namen()
    r = _post(c, f"/oberflaeche/reels/{R_FERTIG}/nochmal", {"wunsch": "Bitte heller"}, cookie="mk_firma=alpha")
    assert r.status_code == 303 and "neu_gebaut=1" in r.headers["location"]
    assert an.von("reel_nochmal") == [("alpha", R_FERTIG, "Bitte heller")]


def test_nochmal_nach_fehler_ohne_wunsch(c, an):
    r = _post(c, f"/oberflaeche/reels/{R_FEHLER}/nochmal", {}, cookie="mk_firma=alpha")
    assert r.status_code == 303 and an.von("reel_nochmal") == [("alpha", R_FEHLER, "")]


@pytest.mark.parametrize("aid", [R_OFFEN, FREMD, "kein-auftrag"])
def test_nochmal_nur_fuer_eigene_fertige_oder_fehler(c, an, aid):
    r = _post(c, f"/oberflaeche/reels/{aid}/nochmal", {"wunsch": "x"}, cookie="mk_firma=alpha")
    assert r.status_code == 422 and "reel_nochmal" not in an.namen()


def test_abbrechen_geht_auch_mit_schalter_aus(c, a):
    r = _post(c, f"/oberflaeche/reels/{R_OFFEN}/abbrechen", {}, cookie="mk_firma=alpha")
    assert r.status_code == 303 and "abgebrochen=1" in r.headers["location"]
    assert a.von("reel_abbrechen") == [("alpha", R_OFFEN)]


@pytest.mark.parametrize("aid", [R_FERTIG, FREMD])
def test_abbrechen_nur_offene_eigene(c, a, aid):
    assert _post(c, f"/oberflaeche/reels/{aid}/abbrechen", {}, cookie="mk_firma=alpha").status_code == 422
    assert "reel_abbrechen" not in a.namen()


# --- Podcast ---------------------------------------------------------------------------------------

def test_folge_bestellen_liest_das_skript(c, an):
    r = _post(c, "/oberflaeche/podcast/bestellen", FOLGE, cookie="mk_firma=alpha")
    assert r.status_code == 303 and r.headers["location"] == "/oberflaeche/podcast?folge_bestellt=1"
    ((firma, titel, sprache, skript),) = an.von("folge_bestellen")
    assert (firma, titel, sprache) == ("alpha", "Folge 3", "de")
    assert skript == [{"sprecher": "Anna", "text": "Willkommen zur dritten Folge. heute geht es um Kuchen."},
                      {"sprecher": "Ben", "text": "Und um Blumen."}]


@pytest.mark.parametrize("aenderung", [{"skript": "ohne Sprecher am Anfang\nAnna: Hallo"}, {"skript": "  "},
                                       {"sprache": "deu"}, {"sprache": "DE"}, {"titel": ""}])
def test_folge_falsche_eingaben(c, an, aenderung):
    r = _post(c, "/oberflaeche/podcast/bestellen", {**FOLGE, **aenderung}, cookie="mk_firma=alpha")
    assert r.status_code == 422 and "folge_bestellen" not in an.namen()


def test_folge_abbrechen_nur_eigene_offene(c, a):
    assert _post(c, f"/oberflaeche/podcast/{FREMD}/abbrechen", {}, cookie="mk_firma=alpha").status_code == 422
    assert _post(c, f"/oberflaeche/podcast/{F_FERTIG}/abbrechen", {}, cookie="mk_firma=alpha").status_code == 422
    assert "folge_abbrechen" not in a.namen()
    r = _post(c, f"/oberflaeche/podcast/{F_OFFEN}/abbrechen", {}, cookie="mk_firma=alpha")
    assert r.status_code == 303 and a.von("folge_abbrechen") == [(F_OFFEN,)]


def test_podcast_karte_mit_abbrechen_und_meldung(c, a):
    r = c.get("/oberflaeche/podcast?folge_bestellt=1", headers=H, cookies={"mk_firma": "alpha"})
    assert f'action="/oberflaeche/podcast/{F_OFFEN}/abbrechen"' in r.text
    assert f"/{F_FERTIG}/abbrechen" not in r.text and "Bestellt." in r.text


# --- Datenwege gegen gefaelschte Pult-Funktionen ---------------------------------------------------

def test_daten_reel_bestellen_ruft_pult_mit_art_bilder(monkeypatch):
    gesehen = {}

    def falsch(payload, x_pult_key):
        gesehen.update(payload)
        return {"auftrag": NEU}
    monkeypatch.setattr(video, "pult_anlegen", falsch)
    assert daten.reel_bestellen("alpha", "T", {"thema": "x"}) == NEU
    assert gesehen == {"mandant": "alpha", "art": "bilder", "titel": "T", "briefing": {"thema": "x"}}


def test_daten_reel_abbrechen_gibt_die_firma_mit(monkeypatch):
    gesehen = {}
    monkeypatch.setattr(video, "pult_abbrechen", lambda aid, payload, x_pult_key: gesehen.update(aid=aid, **payload))
    daten.reel_abbrechen("alpha", R_OFFEN)
    assert gesehen == {"aid": R_OFFEN, "mandant": "alpha"}


def test_daten_folge_und_fehler(monkeypatch):
    monkeypatch.setattr(ton, "pult_anlegen", lambda payload, x_pult_key: {"auftrag": "None"})
    with pytest.raises(daten.DatenFehler) as f:
        daten.folge_bestellen("alpha", "T", "de", [{"sprecher": "A", "text": "b"}])
    assert f.value.art == "nicht_erreichbar"

    def abgelehnt(payload, x_pult_key):
        raise HTTPException(422, "skript: hoechstens 12000 Zeichen insgesamt")
    monkeypatch.setattr(ton, "pult_anlegen", abgelehnt)
    with pytest.raises(daten.DatenFehler) as f:
        daten.folge_bestellen("alpha", "T", "de", [])
    assert f.value.art == "abgelehnt" and "12000" in f.value.grund
