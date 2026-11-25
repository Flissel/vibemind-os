"""Marketing-Oberflaeche, Stufe B Tag 6: Assistent zum Entwurf, Bilder gestalteter Flaechen tauschen,
Feld-Newsletter in Bloecke uebernehmen, neue Collage anlegen. Ohne Datenbank und ohne Modell: daten.* sind
Attrappen. Alle Firmen, Bilder und Texte sind erfunden; versendet wird nichts."""
from __future__ import annotations

import copy
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from spaces.marketing.api import oberflaeche
from spaces.marketing.api.oberflaeche import assistent, daten, entwuerfe, huelle
from spaces.marketing.tests.test_oberflaeche_bildwahl import BILDER, alles, auswahl
from spaces.marketing.tests.test_oberflaeche_entwuerfe import EDITOR, H, IID, KEY, SPEICHERN, Attrappe, FalscheRoute

A1, A2 = "aaaaaaaa-1111-4111-8111-111111111111", "bbbbbbbb-2222-4222-8222-222222222222"
NEU = "77777777-2222-4333-8444-555555555555"
GESTALTUNG = {"version": 1, "format": "quer", "hintergrund": "#ffffff", "ebenen": [
    {"id": "foto1", "art": "bild", "x": 150, "y": 200, "breite": 300, "quelle": "medien:ast.jpg"},
    {"id": "titel", "art": "text", "x": 300, "y": 50, "text": "Frühling"},
    {"id": "foto2", "art": "bild", "x": 450, "y": 200, "breite": 300, "quelle": "medien:fruehling.jpg"}]}


@pytest.fixture
def a(monkeypatch):
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, KEY)
    attrappe = Attrappe()
    attrappe.fehler.clear()
    attrappe.bilder = list(BILDER)
    attrappe.einsetzen(monkeypatch)
    return attrappe


@pytest.fixture
def c():
    app = FastAPI()
    app.include_router(oberflaeche.router)
    return TestClient(app)


def _csrf(text: str) -> str:
    return re.search(r'name="csrf" value="([^"]*)"', text).group(1)


def _post(c, pfad, werte, **kopf):
    return c.post(pfad, headers={**H, **kopf}, data=werte, follow_redirects=False)


# --- Assistent ----------------------------------------------------------------------------------

def _verlauf(a, laeuft=False):
    a.chat = {"laeuft": laeuft, "neueste_fassung": 2, "verlauf": [
        {"id": A1, "art": "chat", "nachricht": "Mach die Überschrift kürzer", "antwort": "Erledigt.",
         "status": "fertig", "fassung_vorher": 1, "fassung_nachher": 2, "hinweise": ["Bild fehlt noch"],
         "erstellt_am": "2026-11-25T10:00:00"},
        {"id": "cccccccc-3333-4333-8333-333333333333", "art": "export", "nachricht": "intern", "status": "fertig"},
        {"id": A2, "art": "chat", "nachricht": "Mehr Farbe bitte", "antwort": "",
         "status": "in_arbeit" if laeuft else "fehler", "fassung_vorher": 2, "fassung_nachher": None,
         "erstellt_am": "2026-11-25T11:00:00"}]}


def test_assistent_abschnitt_mit_verlauf(c, a):
    _verlauf(a)
    r = c.get(EDITOR, headers=H)
    assert r.status_code == 200 and "<script" not in r.text.lower() and " style=" not in r.text
    assert "Mach die Überschrift kürzer" in r.text and "Bild fehlt noch" in r.text and "nicht geklappt" in r.text
    assert "intern" not in r.text                                          # nur Chat-Runden
    assert r.text.count(f'action="{EDITOR}/assistent/rueckgaengig"') == 1
    assert f'name="auftrag" value="{A1}"' in r.text and f'action="{EDITOR}/assistent"' in r.text
    assert "assistent/stopp" not in r.text and "Fassung 1 → 2" in r.text


def test_assistent_laeuft_zeigt_stopp(c, a):
    _verlauf(a, laeuft=True)
    r = c.get(EDITOR, headers=H)
    teil = r.text.split(f'action="{EDITOR}/assistent/stopp"', 1)[1].split("</form>", 1)[0]
    assert f'name="auftrag" value="{A2}"' in teil and 'value="behalten"' in teil and 'value="verwerfen"' in teil
    assert "arbeitet gerade" in r.text and "assistent/rueckgaengig" in r.text


def test_assistent_rueckgaengig_nur_fuer_die_neueste(c, a):
    _verlauf(a)
    a.chat["neueste_fassung"] = 3
    assert "assistent/rueckgaengig" not in c.get(EDITOR, headers=H).text


def test_assistent_verlauf_nicht_lesbar(c, a):
    a.fehler["chat_stand"] = daten.DatenFehler("nicht_erreichbar")
    r = c.get(EDITOR, headers=H)
    assert r.status_code == 200 and "Verlauf des Assistenten ist gerade nicht lesbar" in r.text
    assert f'action="{SPEICHERN}"' in r.text


def test_assistent_bitte_senden(c, a):
    csrf = _csrf(c.get(EDITOR, headers=H).text)
    r = _post(c, f"{EDITOR}/assistent", {"csrf": csrf, "nachricht": "  Bitte\r\nkürzer  "})
    assert r.status_code == 303 and r.headers["location"] == f"{EDITOR}?gefragt=1"
    assert ("chat_senden", IID, "Bitte\nkürzer") in a.aufrufe
    assert "Bitte gesendet" in c.get(f"{EDITOR}?gefragt=1", headers=H).text


@pytest.mark.parametrize("nachricht, satz", [("", "Ohne Bitte"), ("   ", "Ohne Bitte"),
                                             ("x" * 2001, "höchstens 2000")])
def test_assistent_bitte_abgelehnt(c, a, nachricht, satz):
    csrf = _csrf(c.get(EDITOR, headers=H).text)
    r = _post(c, f"{EDITOR}/assistent", {"csrf": csrf, "nachricht": nachricht})
    assert r.status_code == 422 and satz in r.text and "chat_senden" not in a.namen()


def test_assistent_schutz(c, a):
    csrf = _csrf(c.get(EDITOR, headers=H).text)
    assert c.post(f"{EDITOR}/assistent", data={"csrf": csrf, "nachricht": "x"}, follow_redirects=False).status_code == 401
    assert _post(c, f"{EDITOR}/assistent", {"nachricht": "x"}).status_code == 403
    a.entwurf["inhalt"]["status"] = "eingereicht"
    assert _post(c, f"{EDITOR}/assistent", {"csrf": csrf, "nachricht": "x"}).status_code == 422
    a.entwurf["inhalt"]["status"] = "entwurf"
    a.entwurf["inhalt"]["mandant"] = "beta"
    assert _post(c, f"{EDITOR}/assistent", {"csrf": csrf, "nachricht": "x"}).status_code == 422
    assert "chat_senden" not in a.namen()


def test_assistent_abgelehnt_von_der_datenbank(c, a):
    a.fehler["chat_senden"] = daten.DatenFehler("abgelehnt", "Es läuft schon ein Auftrag")
    csrf = _csrf(c.get(EDITOR, headers=H).text)
    r = _post(c, f"{EDITOR}/assistent", {"csrf": csrf, "nachricht": "x"})
    assert r.status_code == 422 and "Es läuft schon ein Auftrag" in r.text


def test_assistent_stoppen_und_rueckgaengig(c, a):
    csrf = _csrf(c.get(EDITOR, headers=H).text)
    r = _post(c, f"{EDITOR}/assistent/stopp", {"csrf": csrf, "art": "verwerfen", "auftrag": A2})
    assert r.headers["location"] == f"{EDITOR}?gestoppt=1" and ("chat_stoppen", IID, "verwerfen", A2) in a.aufrufe
    assert _post(c, f"{EDITOR}/assistent/stopp", {"csrf": csrf, "art": "alles", "auftrag": A2}).status_code == 422
    assert _post(c, f"{EDITOR}/assistent/stopp", {"csrf": csrf, "art": "behalten", "auftrag": "x"}).status_code == 422
    r = _post(c, f"{EDITOR}/assistent/rueckgaengig", {"csrf": csrf, "auftrag": A1})
    assert r.headers["location"] == f"{EDITOR}?gespeichert=4" and ("chat_rueckgaengig", IID, A1) in a.aufrufe
    assert _post(c, f"{EDITOR}/assistent/rueckgaengig", {"csrf": csrf, "auftrag": "../x"}).status_code == 422


def test_assistent_bei_collage(c, a):
    from spaces.marketing.tests.test_oberflaeche_bildwahl import _collage
    _collage(a)
    assert f'action="{EDITOR}/assistent"' in c.get(EDITOR, headers=H).text


# --- Gestaltete Flaechen ------------------------------------------------------------------------

def _flaeche(a):
    props = a.entwurf["fassungen"][0]["bloecke"]["bild"]["data"]["props"]
    props.update({"url": "medien:gs-0123456789ab.jpg", "width": 600, "height": 400,
                  "gestaltung": copy.deepcopy(GESTALTUNG)})
    return props


def test_flaeche_zeigt_ihre_bildebenen(c, a):
    _flaeche(a)
    r = c.get(EDITOR, headers=H)
    assert auswahl(r.text) == {"fb:bild:foto1": "ast.jpg", "fb:bild:foto2": "fruehling.jpg"}
    assert 'name="b:bild"' not in r.text and "Bild 2 der Fläche" in r.text and "neu gerechnet" in r.text


def test_flaeche_bild_tauschen_rechnet_neu(c, a):
    _flaeche(a)
    werte = alles(c.get(EDITOR, headers=H).text)
    werte["fb:bild:foto2"] = "laden-innen.png"
    r = _post(c, SPEICHERN, werte)
    assert r.status_code == 303 and r.headers["location"] == f"{EDITOR}?gespeichert=3"
    (i, g), = a.gerechnet
    erwartet = copy.deepcopy(GESTALTUNG)
    erwartet["ebenen"][2]["quelle"] = "medien:laden-innen.png"
    assert i == IID and g == erwartet
    props = a.gespeichert[-1][4]["bild"]["data"]["props"]
    assert props["gestaltung"] == erwartet and props["url"] == "medien:gs-abcdefabcdef.jpg"
    assert (props["width"], props["height"]) == (600, 400) and props["alt"] == "Blumen"


def test_flaeche_unveraendert_rechnet_nicht(c, a):
    _flaeche(a)
    assert _post(c, SPEICHERN, alles(c.get(EDITOR, headers=H).text)).headers["location"] == f"{EDITOR}?unveraendert=1"
    assert a.gerechnet == []


def test_flaeche_fremdes_bild_und_rechenfehler(c, a):
    _flaeche(a)
    werte = alles(c.get(EDITOR, headers=H).text)
    werte["fb:bild:foto1"] = "fremd-firma-beta.jpg"
    assert _post(c, SPEICHERN, werte).status_code == 422 and a.gerechnet == []
    werte["fb:bild:foto1"] = "laden-innen.png"
    a.fehler["gestaltung_rechnen"] = daten.DatenFehler("abgelehnt", "Bild laden-innen.png fehlt in den Medien")
    r = _post(c, SPEICHERN, werte)
    assert r.status_code == 422 and "fehlt in den Medien" in r.text and a.gespeichert == []


# --- Feld-Newsletter umwandeln ------------------------------------------------------------------

def _felder(a):
    a.entwurf["fassungen"][0] = {"fassung": 2, "format": "felder", "felder": {"betreff": "Alt"}}


def test_felder_zeigt_umwandeln(c, a):
    _felder(a)
    r = c.get(EDITOR, headers=H)
    assert f'action="{EDITOR}/umwandeln"' in r.text and f'action="{SPEICHERN}"' not in r.text
    assert "nicht bearbeiten" not in r.text and f'action="{EDITOR}/assistent"' not in r.text


def test_umwandeln(c, a):
    _felder(a)
    csrf = _csrf(c.get(EDITOR, headers=H).text)
    r = _post(c, f"{EDITOR}/umwandeln", {"csrf": csrf})
    assert r.headers["location"] == f"{EDITOR}?umgewandelt=1" and ("in_bloecke", IID, entwuerfe.VON) in a.aufrufe


def test_umwandeln_abgelehnt(c, a):
    csrf = _csrf(c.get(EDITOR, headers=H).text)
    assert _post(c, f"{EDITOR}/umwandeln", {"csrf": csrf}).status_code == 422          # schon Bloecke
    _felder(a)
    a.entwurf["inhalt"]["status"] = "eingereicht"
    assert _post(c, f"{EDITOR}/umwandeln", {"csrf": csrf}).status_code == 422
    assert _post(c, f"{EDITOR}/umwandeln", {}).status_code == 403
    assert "in_bloecke" not in a.namen()


# --- Neue Collage -------------------------------------------------------------------------------

NEU_WEG = "/oberflaeche/entwuerfe/neu-collage"


def test_liste_zeigt_neue_collage(c, a):
    r = c.get("/oberflaeche/entwuerfe", headers=H)
    assert f'action="{NEU_WEG}"' in r.text and "Neue Collage" in r.text
    a.liste = []
    assert f'action="{NEU_WEG}"' in c.get("/oberflaeche/entwuerfe", headers=H).text


def test_neue_collage_anlegen(c, a):
    csrf = _csrf(c.get("/oberflaeche/entwuerfe", headers=H).text)
    r = _post(c, NEU_WEG, {"csrf": csrf, "titel": "  Sommer   im Laden ", "betreff": "Neu"})
    assert r.status_code == 303 and r.headers["location"] == f"/oberflaeche/entwuerfe/{NEU}?angelegt=1"
    assert ("collage_anlegen", "alpha", "Sommer im Laden", "Neu") in a.aufrufe


@pytest.mark.parametrize("werte, satz", [({"titel": ""}, "Titel fehlt"), ({"titel": "x" * 201}, "Titel fehlt"),
                                         ({"titel": "Gut", "betreff": "x" * 201}, "zu lang")])
def test_neue_collage_abgelehnt(c, a, werte, satz):
    csrf = _csrf(c.get("/oberflaeche/entwuerfe", headers=H).text)
    r = _post(c, NEU_WEG, {"csrf": csrf, **werte})
    assert r.status_code == 422 and satz in r.text and "collage_anlegen" not in a.namen()


def test_neue_collage_schutz(c, a):
    assert c.post(NEU_WEG, data={"titel": "x"}, follow_redirects=False).status_code == 401
    assert _post(c, NEU_WEG, {"titel": "x"}).status_code == 403
    assert "collage_anlegen" not in a.namen()


# --- Datenzugang --------------------------------------------------------------------------------

def test_daten_tag6(monkeypatch):
    from spaces.marketing.api import chat, gestaltung, pult
    monkeypatch.setenv(daten.PULT_ENV, "pult-test")
    wege = {"senden": FalscheRoute({"auftrag": "a1", "status": "offen"}), "stand": FalscheRoute({"verlauf": []}),
            "stopp": FalscheRoute({"abgeschlossen": True}), "zurueck": FalscheRoute({"fassung": 5}),
            "rechnen": FalscheRoute({"url": "medien:gs-abcdefabcdef.jpg", "width": 600, "height": 400}),
            "bloecke": FalscheRoute({"fassung": 3}), "collage": FalscheRoute({"inhalt": NEU, "fassung": 1})}
    monkeypatch.setattr(chat, "chat_anlegen", wege["senden"])
    monkeypatch.setattr(chat, "chat_stand", wege["stand"])
    monkeypatch.setattr(chat, "chat_stoppen", wege["stopp"])
    monkeypatch.setattr(chat, "chat_rueckgaengig", wege["zurueck"])
    monkeypatch.setattr(gestaltung, "pult_gestaltung", wege["rechnen"])
    monkeypatch.setattr(pult, "in_bloecke", wege["bloecke"])
    monkeypatch.setattr(pult, "collage_anlegen", wege["collage"])
    assert daten.chat_senden(IID, "Hallo") == {"auftrag": "a1", "status": "offen"}
    assert wege["senden"].werte == {"iid": IID, "x_pult_key": "pult-test",
                                    "payload": {"nachricht": "Hallo", "kontext": {}}}
    assert daten.chat_stand(IID) == {"verlauf": []}
    daten.chat_stoppen(IID, "behalten", A1)
    assert wege["stopp"].werte["payload"] == {"art": "behalten", "auftrag": A1}
    assert daten.chat_rueckgaengig(IID, A1) == 5 and wege["zurueck"].werte["payload"] == {"auftrag": A1}
    assert daten.gestaltung_rechnen(IID, {"v": 1})["url"] == "medien:gs-abcdefabcdef.jpg"
    assert wege["rechnen"].werte["payload"] == {"gestaltung": {"v": 1}}
    assert daten.in_bloecke(IID, "Betreiber") == 3 and wege["bloecke"].werte["payload"] == {"von": "Betreiber"}
    assert daten.collage_anlegen("alpha", "T", "B") == NEU
    assert wege["collage"].werte == {"x_pult_key": "pult-test",
                                     "payload": {"mandant": "alpha", "titel": "T", "betreff": "B"}}
    for name, modul, attr in (("rechnen", gestaltung, "pult_gestaltung"), ("bloecke", pult, "in_bloecke"),
                              ("collage", pult, "collage_anlegen"), ("zurueck", chat, "chat_rueckgaengig")):
        monkeypatch.setattr(modul, attr, FalscheRoute({}))
    for aufruf in (lambda: daten.gestaltung_rechnen(IID, {}), lambda: daten.in_bloecke(IID, "x"),
                   lambda: daten.collage_anlegen("alpha", "T", ""), lambda: daten.chat_rueckgaengig(IID, A1)):
        with pytest.raises(daten.DatenFehler):
            aufruf()


def test_server_haengt_die_tag6_wege_ein():
    from spaces.marketing.api import server
    wege = {getattr(r, "path", "") for r in server.app.routes}
    for w in ("/oberflaeche/entwuerfe/neu-collage", "/oberflaeche/entwuerfe/{iid}/umwandeln",
              "/oberflaeche/entwuerfe/{iid}/assistent", "/oberflaeche/entwuerfe/{iid}/assistent/stopp",
              "/oberflaeche/entwuerfe/{iid}/assistent/rueckgaengig"):
        assert w in wege


def test_assistent_texte_gleich():
    de, en = assistent.TEXTE["de"], assistent.TEXTE["en"]
    assert set(de) == set(en)
    for k in de:
        assert set(re.findall(r"{(\w+)}", de[k])) == set(re.findall(r"{(\w+)}", en[k])), k
