"""Marketing-Oberflaeche, F5: Reels aus Videomaterial, Kurzfassungen und Reels aus KI-Bildern bestellen und die
KI-Bilder pruefen (neu erzeugen, freigeben), alles hinter dem Kosten-Schalter. Ohne Datenbank, Laura und
Bild-Arbeiter: daten.* sind Attrappen, die Datenwege gehen gegen gefaelschte Pult-Funktionen. Alle Werte sind
erfunden; bestellt oder erzeugt wird in keinem Test etwas."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from spaces.marketing.api import oberflaeche, reel_bilder, video
from spaces.marketing.api.oberflaeche import bestellen, daten, huelle

KEY = "test-ui-schluessel"
H = {"Authorization": f"Bearer {KEY}"}
FIRMEN = [{"id": "alpha", "name": "Alpha GmbH", "aktiv": True}]
R_KI = "abababab-1111-4111-8111-111111111111"
R_FERTIG = "bcbcbcbc-2222-4222-8222-222222222222"
B1, B2, B3 = ("c1c1c1c1-3333-4333-8333-333333333331", "c2c2c2c2-3333-4333-8333-333333333332",
              "c3c3c3c3-3333-4333-8333-333333333333")
FREMD_BILD = "f0f0f0f0-6666-4666-8666-666666666666"
NEU = "12345678-1234-4234-8234-123456789abc"
V1, V2 = "laura-video-eins", "laura-video-zwei"
MATERIAL = {"videos": [{"kennung": V1, "name": "Werkstatt", "projekt": "Muster", "dauer_s": 95.0},
                       {"kennung": V2, "name": "Rundgang", "projekt": "Muster", "dauer_s": 300.0}]}


class Attrappe:
    def __init__(self):
        self.aufrufe: list[tuple] = []
        self.material = MATERIAL
        self.reels = [
            {"id": R_KI, "titel": "KI-Frühling", "status": "bilder_pruefen", "art": "bilder",
             "briefing": {"ki": {"anzahl": 3}},
             "ki_bilder": [{"id": B1, "nr": 1, "idee": "Kuchen im Licht", "status": "fertig",
                            "datei": "ki-abababab-1-1.jpg"},
                           {"id": B2, "nr": 2, "idee": "Blumen am Fenster", "status": "fehler",
                            "befund": "Bild zu dunkel"},
                           {"id": B3, "nr": 3, "idee": "Laden von außen", "status": "in_arbeit"}]},
            {"id": R_FERTIG, "titel": "Fertig", "status": "fertig", "art": "material", "briefing": {}}]

    def einsetzen(self, monkeypatch):
        def mach(name, holen):
            def f(*werte):
                self.aufrufe.append((name, *werte))
                return holen(*werte)
            monkeypatch.setattr(daten, name, f)
        mach("firmen", lambda: FIRMEN)
        mach("bilder", lambda m: ["ast.jpg"])
        mach("reels", lambda m, archiv: [] if archiv else self.reels)
        mach("geteilt", lambda m, art: [])
        mach("material", lambda: self.material)
        mach("reel_bestellen", lambda m, t, b: NEU)
        mach("reel_bestellen_art", lambda m, art, t, b: NEU)
        mach("reel_ki_neu", lambda m, aid, bild, idee: None)
        mach("reel_ki_freigeben", lambda m, aid: True)

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


def _post(c, pfad, werte):
    return c.post(pfad, headers=H, data={"csrf": huelle.csrf_marke(), **werte}, follow_redirects=False)


GRUND = {"titel": "Werkstatt-Reel", "thema": "Neue Werkstatt", "sprechertext": "So arbeiten wir.", "hook": "",
         "dauer_s": "30", "format": "reel"}


# --- Seite ----------------------------------------------------------------------------------------

def test_an_zeigt_drei_weitere_formulare_mit_videos(c, an):
    s = c.get("/oberflaeche/reels", headers=H).text
    for art in ("material", "kurzfassung", "ki"):
        assert f'<input type="hidden" name="art" value="{art}">' in s
    assert f'name="video:{V1}"' in s and f'type="radio" name="video" value="{V2}"' in s
    assert "Werkstatt · Muster" in s
    assert an.von("material") == [()]


def test_laura_leer_zeigt_hinweis(c, an):
    an.material = {"videos": [], "hinweis": "Laura ist gerade nicht erreichbar."}
    s = c.get("/oberflaeche/reels", headers=H).text
    assert "Laura ist gerade nicht erreichbar." in s


def test_aus_gesperrt_und_ohne_laura(c, a):
    s = c.get("/oberflaeche/reels", headers=H).text
    assert 'value="ki">' in s and "<fieldset disabled>" in s
    assert a.von("material") == []


def test_ki_pruefen_auf_der_karte(c, an):
    s = c.get("/oberflaeche/reels?reiter=arbeit", headers=H).text
    assert f'action="/oberflaeche/reels/{R_KI}/ki/freigeben"' in s
    assert f'/reels/{R_KI}/ki/{B1}/neu' in s and f'/reels/{R_KI}/ki/{B2}/neu' in s
    assert f'/reels/{R_KI}/ki/{B3}/neu' not in s          # in Arbeit: nichts neu zu erzeugen
    assert "Kuchen im Licht" in s and "Bild zu dunkel" in s and "/oberflaeche/bild/ki-abababab-1-1.jpg" in s
    assert f'action="/oberflaeche/reels/{R_KI}/abbrechen"' in s


def test_ki_pruefen_aus_gesperrt(c, a):
    s = c.get("/oberflaeche/reels?reiter=arbeit", headers=H).text
    teil = s[s.index('class="aufklapp bestellen kipruefen"'):]
    assert "Bestellen ist ausgeschaltet (Kosten-Schalter)." in teil[:3000] and "<fieldset disabled>" in teil


# --- Bestellen ------------------------------------------------------------------------------------

def test_material_mit_auswahl(c, an):
    r = _post(c, "/oberflaeche/reels/bestellen", {**GRUND, "art": "material", f"video:{V2}": "1", f"video:{V1}": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/oberflaeche/reels?reiter=arbeit&bestellt=1"
    ((firma, art, titel, briefing),) = an.von("reel_bestellen_art")
    assert (firma, art, titel) == ("alpha", "material", "Werkstatt-Reel")
    assert briefing["videos"] == [V1, V2] and briefing["dauer_s"] == 30
    assert an.von("reel_bestellen") == []


def test_material_ohne_auswahl_erlaubt(c, an):
    r = _post(c, "/oberflaeche/reels/bestellen", {**GRUND, "art": "material"})
    assert r.status_code == 303 and an.von("reel_bestellen_art")[0][3]["videos"] == []


def test_kurzfassung_ohne_sprechertext(c, an):
    r = _post(c, "/oberflaeche/reels/bestellen", {**GRUND, "art": "kurzfassung", "sprechertext": "", "video": V2,
                                                  "format": "quer"})
    assert r.status_code == 303
    ((_, art, _, briefing),) = an.von("reel_bestellen_art")
    assert art == "kurzfassung" and briefing["video"] == V2 and briefing["format"] == "quer"


def test_ki_bestellt_art_bilder_mit_anzahl(c, an):
    r = _post(c, "/oberflaeche/reels/bestellen", {**GRUND, "art": "ki", "anzahl": "5"})
    assert r.status_code == 303
    ((_, art, _, briefing),) = an.von("reel_bestellen_art")
    assert art == "bilder" and briefing["ki"] == {"anzahl": 5} and "bilder" not in briefing
    assert an.von("material") == []


@pytest.mark.parametrize("werte", [
    {"art": "material", "video:fremdes-video": "1"},
    {"art": "material", **{f"video:v{i}": "1" for i in range(9)}},
    {"art": "kurzfassung", "video": ""}, {"art": "kurzfassung", "video": "fremdes-video"},
    {"art": "ki", "anzahl": "2"}, {"art": "ki", "anzahl": "6"}, {"art": "ki", "anzahl": "viele"},
    {"art": "drohne"}, {"art": "material", "titel": ""}, {"art": "ki", "sprechertext": ""},
    {"art": "material", "dauer_s": "90"}])
def test_eingaben_geprueft(c, an, werte):
    r = _post(c, "/oberflaeche/reels/bestellen", {**GRUND, **werte})
    assert r.status_code == 422 and an.von("reel_bestellen_art") == [] and an.von("reel_bestellen") == []


def test_aus_422_ohne_aufruf(c, a):
    r = _post(c, "/oberflaeche/reels/bestellen", {**GRUND, "art": "ki", "anzahl": "4"})
    assert r.status_code == 422 and "Kosten-Schalter" in r.text
    assert a.von("reel_bestellen_art") == [] and a.von("material") == []


def test_bilder_weg_unveraendert(c, an):
    r = _post(c, "/oberflaeche/reels/bestellen", {**GRUND, "bild:ast.jpg": "1"})
    assert r.status_code == 303 and len(an.von("reel_bestellen")) == 1 and an.von("reel_bestellen_art") == []


# --- KI-Bilder pruefen ----------------------------------------------------------------------------

def test_ki_neu(c, an):
    r = _post(c, f"/oberflaeche/reels/{R_KI}/ki/{B2}/neu", {"idee": "Blumen im Sonnenlicht"})
    assert r.status_code == 303 and r.headers["location"] == "/oberflaeche/reels?reiter=arbeit&ki_neu_ok=1"
    assert an.von("reel_ki_neu") == [("alpha", R_KI, B2, "Blumen im Sonnenlicht")]


@pytest.mark.parametrize("pfad", [f"/oberflaeche/reels/{R_KI}/ki/{FREMD_BILD}/neu",
                                  f"/oberflaeche/reels/{R_KI}/ki/kein-uuid/neu",
                                  f"/oberflaeche/reels/{R_FERTIG}/ki/{B1}/neu",
                                  f"/oberflaeche/reels/{NEU}/ki/{B1}/neu"])
def test_ki_neu_nur_eigenes_bild_beim_pruefen(c, an, pfad):
    r = _post(c, pfad, {"idee": ""})
    assert r.status_code == 422 and an.von("reel_ki_neu") == []


def test_ki_neu_idee_zu_lang(c, an):
    r = _post(c, f"/oberflaeche/reels/{R_KI}/ki/{B1}/neu", {"idee": "x" * 601})
    assert r.status_code == 422 and an.von("reel_ki_neu") == []


def test_ki_freigeben(c, an):
    r = _post(c, f"/oberflaeche/reels/{R_KI}/ki/freigeben", {})
    assert r.status_code == 303 and r.headers["location"] == "/oberflaeche/reels?reiter=arbeit&ki_frei_ok=1"
    assert an.von("reel_ki_freigeben") == [("alpha", R_KI)]
    assert "Freigegeben. Das Reel wird gebaut." in c.get(r.headers["location"], headers=H).text


def test_ki_freigeben_nur_beim_pruefen(c, an):
    r = _post(c, f"/oberflaeche/reels/{R_FERTIG}/ki/freigeben", {})
    assert r.status_code == 422 and an.von("reel_ki_freigeben") == []


@pytest.mark.parametrize("pfad", [f"/oberflaeche/reels/{R_KI}/ki/freigeben", f"/oberflaeche/reels/{R_KI}/ki/{B1}/neu"])
def test_ki_wege_aus_422(c, a, pfad):
    r = _post(c, pfad, {})
    assert r.status_code == 422 and a.von("reel_ki_neu") == [] and a.von("reel_ki_freigeben") == []


def test_ki_ohne_csrf(c, an):
    r = c.post(f"/oberflaeche/reels/{R_KI}/ki/freigeben", headers=H, data={}, follow_redirects=False)
    assert r.status_code == 403 and an.von("reel_ki_freigeben") == []


# --- Datenwege gegen gefaelschte Pult-Funktionen --------------------------------------------------

def test_daten_wege(monkeypatch):
    monkeypatch.setenv(daten.PULT_ENV, "pult-test")
    gesehen = []
    monkeypatch.setattr(video, "pult_anlegen", lambda payload, x_pult_key: gesehen.append(payload) or {"auftrag": NEU})
    monkeypatch.setattr(video, "pult_material", lambda x_pult_key: MATERIAL)
    monkeypatch.setattr(reel_bilder, "pult_ki_neu",
                        lambda aid, payload, x_pult_key: gesehen.append((aid, payload)) or {"bild": NEU})
    monkeypatch.setattr(reel_bilder, "pult_ki_freigeben",
                        lambda aid, payload, x_pult_key: gesehen.append((aid, payload)) or {"ok": True})
    assert daten.reel_bestellen_art("alpha", "kurzfassung", "T", {"video": V1}) == NEU
    assert gesehen[0] == {"mandant": "alpha", "art": "kurzfassung", "titel": "T", "briefing": {"video": V1}}
    assert daten.material() == MATERIAL
    daten.reel_ki_neu("alpha", R_KI, B1, "")
    assert gesehen[1] == (R_KI, {"mandant": "alpha", "bild": B1, "idee": ""})
    assert daten.reel_ki_freigeben("alpha", R_KI) is True
    assert gesehen[2] == (R_KI, {"mandant": "alpha"})
