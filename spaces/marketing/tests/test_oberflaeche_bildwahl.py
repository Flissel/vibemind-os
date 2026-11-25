"""Marketing-Oberflaeche, Stufe B Tag 5: Bilder zuordnen (Bildbloecke, Collagen) und Collagen bearbeiten.
Ohne Datenbank: daten.* wird durch die Attrappe aus dem Editor-Test ersetzt. Alle Firmen, Bilder und Texte sind
erfunden. Gespeichert wird nur in die Attrappe; versendet wird in keinem Test etwas."""
from __future__ import annotations

import copy
import html
import re
import urllib.parse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from spaces.marketing.api import oberflaeche
from spaces.marketing.api.oberflaeche import daten, entwuerfe, huelle, medien
from spaces.marketing.claw import seite as seite_modell
from spaces.marketing.tests.test_oberflaeche_entwuerfe import (EDITOR, H, IID, KEY, SPEICHERN, Attrappe, FalscheRoute,
                                                               formularwerte)

BILDER = ["ast.jpg", "fruehling.jpg", "laden-innen.png"]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

SEITE = {
    "version": 1, "breite": 600, "hoehe": 1200,
    "hintergrund": {"farbe": "#ffffff", "bild": "medien:ast.jpg"},
    "schriften": {"anzeige": "dm-sans", "text": "dm-sans"},
    "ebenen": [
        {"id": "knopf", "art": "knopf", "x": 300, "y": 900, "breite": 220, "hoehe": 48, "text": "Mehr erfahren",
         "link": "https://example.org/a", "farbe": "#5b3fd6", "textfarbe": "#ffffff"},
        {"id": "titel", "art": "text", "x": 300, "y": 120, "breite": 520, "hoehe": 120, "text": "Frühling",
         "rolle": "ANZEIGE", "groesse": 48, "farbe": "#111111"},
        {"id": "deko", "art": "form", "x": 300, "y": 400, "breite": 600, "hoehe": 10, "form": "rechteck",
         "farbe": "#eeeeee"},
        {"id": "foto", "art": "bild", "x": 300, "y": 500, "breite": 400, "hoehe": 300, "quelle": "medien:fruehling.jpg"},
        {"id": "text", "art": "text", "x": 300, "y": 300, "breite": 480, "hoehe": 120,
         "text": "Hallo {vorname}, schön, dass du da bist.", "rolle": "TEXT", "groesse": 17, "farbe": "#333333"},
    ],
}


@pytest.fixture
def a(monkeypatch):
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, KEY)
    attrappe = Attrappe()
    attrappe.fehler.pop("bilder")
    attrappe.bilder = list(BILDER)
    attrappe.einsetzen(monkeypatch)
    return attrappe


@pytest.fixture
def c():
    app = FastAPI()
    app.include_router(oberflaeche.router)
    return TestClient(app)


def _collage(a, seite: dict | None = None):
    a.entwurf["fassungen"][0] = {"fassung": 2, "format": "seite", "seite": copy.deepcopy(seite or SEITE),
                                 "felder": {"betreff": "Frühling", "vorschautext": "Neu im Laden"}}


def auswahl(text: str) -> dict:
    """Gewaehlte Option je <select>, wie ein Browser sie schicken wuerde."""
    werte = {}
    for name, rumpf in re.findall(r'<select name="([^"]+)">(.*?)</select>', text, flags=re.S):
        gewaehlt = re.search(r'<option value="([^"]*)" selected>', rumpf)
        werte[html.unescape(name)] = html.unescape(gewaehlt.group(1)) if gewaehlt else ""
    return werte


def optionen(text: str, name: str) -> list[str]:
    rumpf = re.search(rf'<select name="{re.escape(name)}">(.*?)</select>', text, flags=re.S).group(1)
    return [html.unescape(v) for v in re.findall(r'<option value="([^"]*)"', rumpf)]


def alles(text: str) -> dict:
    return {**formularwerte(text, SPEICHERN), **auswahl(text)}


def _post(c, werte):
    return c.post(SPEICHERN, headers=H, data=werte, follow_redirects=False)


# --- Bildbloecke --------------------------------------------------------------------------------

def test_bildblock_zeigt_vorschau_wahl_und_galerie(c, a):
    r = c.get(EDITOR, headers=H)
    assert r.status_code == 200 and "<script" not in r.text.lower() and " style=" not in r.text
    assert auswahl(r.text) == {"b:bild": "fruehling.jpg"}
    assert optionen(r.text, "b:bild") == BILDER                 # aktuelles Bild steht in der Liste: kein Leereintrag
    assert '<img class="bildchen" src="/oberflaeche/bild/fruehling.jpg?' in r.text
    assert "Bilder dieser Firma (3)" in r.text and r.text.count("<figure>") == 3
    assert ("bilder", "alpha") in a.aufrufe


def test_bildblock_neues_bild_speichern(c, a):
    werte = alles(c.get(EDITOR, headers=H).text)
    werte["b:bild"] = "laden-innen.png"
    r = _post(c, werte)
    assert r.status_code == 303 and r.headers["location"] == f"{EDITOR}?gespeichert=3"
    gespeichert = a.gespeichert[-1][4]
    assert gespeichert["bild"]["data"]["props"] == {"url": "medien:laden-innen.png", "alt": "Blumen"}
    rest = {k: v for k, v in gespeichert.items() if k != "bild"}
    assert rest == {k: v for k, v in a.entwurf["fassungen"][0]["bloecke"].items() if k != "bild"}


def test_bildblock_fremdes_bild_abgelehnt(c, a):
    werte = alles(c.get(EDITOR, headers=H).text)
    for fremd in ("fremd-firma-beta.jpg", "../geheim.jpg", "gs-0123456789ab.jpg"):
        werte["b:bild"] = fremd
        r = _post(c, werte)
        assert r.status_code == 422 and "gibt es für diese Firma nicht" in r.text
    assert a.gespeichert == []


def test_bildblock_unveraendert_und_leer_bleibt(c, a):
    werte = alles(c.get(EDITOR, headers=H).text)
    assert _post(c, werte).headers["location"] == f"{EDITOR}?unveraendert=1"
    werte["b:bild"] = ""                                       # ein Block verliert sein Bild nie ueber die Wahl
    assert _post(c, werte).headers["location"] == f"{EDITOR}?unveraendert=1"


def test_bildblock_platzhalter_und_altes_bild_ausserhalb_der_liste(c, a):
    bloecke = a.entwurf["fassungen"][0]["bloecke"]
    bloecke["bild"]["data"]["props"]["url"] = "medien:altbestand.jpg"
    r = c.get(EDITOR, headers=H)
    assert optionen(r.text, "b:bild") == ["altbestand.jpg"] + BILDER and auswahl(r.text)["b:bild"] == "altbestand.jpg"
    assert _post(c, alles(r.text)).headers["location"] == f"{EDITOR}?unveraendert=1"
    bloecke["bild"]["data"]["props"]["url"] = ""
    r = c.get(EDITOR, headers=H)
    assert optionen(r.text, "b:bild") == [""] + BILDER and "noch kein Bild" in r.text
    werte = alles(r.text)
    werte["b:bild"] = "ast.jpg"
    assert _post(c, werte).status_code == 303
    assert a.gespeichert[-1][4]["bild"]["data"]["props"]["url"] == "medien:ast.jpg"


def test_gestaltete_flaeche_ohne_bildebene_tauscht_nichts(c, a):
    # Seit Tag 6 tauschen Flaechen die Bilder ihrer Bildebenen (test_oberflaeche_tag6); ohne Bildebene nichts
    props = a.entwurf["fassungen"][0]["bloecke"]["bild"]["data"]["props"]
    props.update({"url": "medien:gs-0123456789ab.jpg", "gestaltung": {"format": "quer", "ebenen": []}})
    r = c.get(EDITOR, headers=H)
    assert "b:bild" not in auswahl(r.text) and "keine Bilder" in r.text
    werte = alles(r.text)
    werte["b:bild"] = "ast.jpg"
    assert _post(c, werte).headers["location"] == f"{EDITOR}?unveraendert=1"


def test_bilderliste_nicht_lesbar(c, a):
    a.fehler["bilder"] = daten.DatenFehler("nicht_erreichbar")
    r = c.get(EDITOR, headers=H)
    assert r.status_code == 200 and "Datei: fruehling.jpg" in r.text and "nicht lesbar" in r.text
    werte = alles(r.text)
    assert _post(c, werte).headers["location"] == f"{EDITOR}?unveraendert=1"
    werte["b:bild"] = "ast.jpg"
    r = _post(c, werte)
    assert r.status_code == 422 and "nicht lesbar" in r.text and a.gespeichert == []


# --- Collagen -----------------------------------------------------------------------------------

def test_collage_editor_felder_in_lesereihenfolge(c, a):
    _collage(a)
    r = c.get(EDITOR, headers=H)
    assert r.status_code == 200 and "<script" not in r.text.lower() and " style=" not in r.text
    namen = re.findall(r'name="([tuq]:[a-z]+|hg)"', r.text)
    assert namen == ["hg", "t:titel", "t:text", "q:foto", "t:knopf", "u:knopf"]   # nach y; Formen nie
    assert "Text: Überschrift" in r.text and "Text: Fließtext" in r.text and "Collage: Texte" in r.text
    assert auswahl(r.text) == {"hg": "ast.jpg", "q:foto": "fruehling.jpg"}
    assert optionen(r.text, "hg") == [""] + BILDER and optionen(r.text, "q:foto") == BILDER
    assert 'name="basis" value="2"' in r.text and f'action="{EDITOR}/einreichen"' in r.text
    assert "nicht bearbeiten" not in r.text and "<iframe" not in r.text


def test_collage_speichern(c, a):
    _collage(a)
    werte = alles(c.get(EDITOR, headers=H).text)
    werte.update({"t:titel": "Sommer", "t:text": "Hallo {vorname},\r\nneu bei uns.", "t:knopf": "Jetzt ansehen",
                  "u:knopf": "https://example.org/b", "q:foto": "laden-innen.png", "hg": "", "betreff": "Sommer"})
    r = _post(c, werte)
    assert r.status_code == 303 and r.headers["location"] == f"{EDITOR}?gespeichert=3"
    iid, basis, betreff, vorschau, neu = a.gespeichert[-1]
    assert (iid, basis, betreff, vorschau) == (IID, 2, "Sommer", "Neu im Laden")
    assert neu == seite_modell.pruefen(neu)                     # normalisiert, wie die Pult-Funktion es speichert
    eb = {x["id"]: x for x in neu["ebenen"]}
    assert [x["id"] for x in neu["ebenen"]] == [x["id"] for x in SEITE["ebenen"]]   # Stapelfolge bleibt
    assert eb["titel"]["text"] == "Sommer" and eb["text"]["text"] == "Hallo {vorname},\nneu bei uns."
    assert eb["knopf"]["text"] == "Jetzt ansehen" and eb["knopf"]["link"] == "https://example.org/b"
    assert eb["foto"]["quelle"] == "medien:laden-innen.png" and neu["hintergrund"]["bild"] is None
    for eid in ("titel", "foto", "deko"):
        alt = next(x for x in SEITE["ebenen"] if x["id"] == eid)
        assert all(eb[eid][k] == alt[k] for k in ("x", "y", "breite", "hoehe"))


def test_collage_unveraendert(c, a):
    _collage(a)
    assert _post(c, alles(c.get(EDITOR, headers=H).text)).headers["location"] == f"{EDITOR}?unveraendert=1"


@pytest.mark.parametrize("feld, wert, satz", [
    ("u:knopf", "http://example.org", "https://"),
    ("u:knopf", "https://example.org/a b", "https://"),
    ("t:titel", "", "Die Collage passt so nicht"),
    ("t:titel", "Hallo {vorname}", "Die Collage passt so nicht"),          # Anrede nur im Fliesstext
    ("t:knopf", "x" * 61, "höchstens 60"),
    ("q:foto", "fremd-firma-beta.jpg", "gibt es für diese Firma nicht"),
    ("hg", "../geheim.jpg", "gibt es für diese Firma nicht"),
])
def test_collage_abgelehnt(c, a, feld, wert, satz):
    _collage(a)
    werte = alles(c.get(EDITOR, headers=H).text)
    werte[feld] = wert
    r = _post(c, werte)
    assert r.status_code == 422 and satz in r.text
    assert a.gespeichert == []


def test_collage_veraltet_und_nicht_entwurf(c, a):
    _collage(a)
    werte = alles(c.get(EDITOR, headers=H).text)
    werte["t:titel"] = "Neu"
    r = _post(c, {**werte, "basis": "1"})
    assert r.status_code == 422 and "neuere Fassung" in r.text
    a.entwurf["inhalt"]["status"] = "eingereicht"
    r = _post(c, werte)
    assert r.status_code == 422 and a.gespeichert == []


def test_collage_ohne_csrf_und_ohne_schluessel(c, a):
    _collage(a)
    werte = alles(c.get(EDITOR, headers=H).text)
    werte["t:titel"] = "Neu"
    assert c.post(SPEICHERN, headers=H, data={k: v for k, v in werte.items() if k != "csrf"},
                  follow_redirects=False).status_code == 403
    assert c.post(SPEICHERN, data=werte, follow_redirects=False).status_code == 401
    assert a.gespeichert == []


def test_collage_eingereicht_ohne_formular(c, a):
    _collage(a)
    a.entwurf["inhalt"]["status"] = "eingereicht"
    r = c.get(EDITOR, headers=H)
    assert f'action="{SPEICHERN}"' not in r.text and "q:foto" not in r.text


# --- Datenzugang --------------------------------------------------------------------------------

def test_daten_bilder_nur_eigene_und_gemeinsame(monkeypatch, tmp_path):
    from spaces.marketing.api import medien_mandant
    for n in ("eigen.jpg", "gemeinsam.png", "fremd.jpg", "logo-beta-0123456789.png", "gs-0123456789ab.jpg",
              "notiz.txt"):
        (tmp_path / n).write_bytes(PNG)
    (tmp_path / "ordner.jpg").mkdir()
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.delenv("MARKETING_MEDIEN_ORDNER", raising=False)
    monkeypatch.setenv(daten.PULT_ENV, "pult-test")           # derselbe Name, den die Pult-Funktion prueft
    monkeypatch.setattr(medien_mandant, "sicht", lambda m: {
        "name": "Alpha", "eigene": {"eigen.jpg"}, "fremde": {"fremd.jpg"}, "gemeinsam": {"gemeinsam.png"}})
    assert daten.bilder("alpha") == ["eigen.jpg", "gemeinsam.png"]
    monkeypatch.delenv(daten.PULT_ENV)
    with pytest.raises(daten.DatenFehler) as f:
        daten.bilder("alpha")
    assert f.value.art == "nicht_verbunden"


def test_daten_seite_speichern(monkeypatch):
    from spaces.marketing.api import pult
    route = FalscheRoute({"fassung": 4})
    monkeypatch.setattr(pult, "seite_speichern", route)
    monkeypatch.setenv(daten.PULT_ENV, "pult-test")
    assert daten.seite_speichern(IID, 3, "B", "V", {"ebenen": []}) == 4
    assert route.werte == {"iid": IID, "x_pult_key": "pult-test", "payload": {
        "basis_fassung": 3, "betreff": "B", "vorschautext": "V", "seite": {"ebenen": []}, "urheber": "betreiber"}}
    monkeypatch.setattr(pult, "seite_speichern", FalscheRoute({}))
    with pytest.raises(daten.DatenFehler):
        daten.seite_speichern(IID, 3, "B", "V", {})


# --- Bilder abrufen -----------------------------------------------------------------------------

@pytest.fixture
def ordner(monkeypatch, tmp_path):
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, KEY)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.delenv("MARKETING_MEDIEN_ORDNER", raising=False)
    (tmp_path / "ast.jpg").write_bytes(PNG)
    return tmp_path


def _holen(c, adr: str):
    return c.get(html.unescape(adr))                            # ohne Bearer, wie <img>


def test_bild_abrufen(c, ordner):
    r = _holen(c, medien.bild_adresse("alpha", "ast.jpg"))
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "private, no-store" and "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_bild_abrufen_abgewiesen(c, ordner):
    adr = medien.bild_adresse("alpha", "ast.jpg")
    pfad, _, query = adr.partition("?")
    q = dict(urllib.parse.parse_qsl(query))
    assert _holen(c, pfad).status_code == 404                                       # ohne Firma
    assert _holen(c, pfad + "?firma=alpha").status_code == 403                      # ohne Signatur
    assert _holen(c, pfad + "?" + urllib.parse.urlencode({**q, "firma": "beta"})).status_code == 403
    assert _holen(c, f"/oberflaeche/bild/fehlt.jpg?{query}").status_code == 403      # Signatur je Name
    assert _holen(c, medien.bild_adresse("alpha", "fehlt.jpg")).status_code == 404
    assert _holen(c, f"/oberflaeche/bild/notiz.txt?{query}").status_code == 404
    # Eine Medien-Signatur (Reels, Zweck v1) oeffnet kein Bild und umgekehrt
    bis = q["bis"]
    fremd_zweck = medien._signatur("alpha", "ast.jpg", bis)
    assert _holen(c, pfad + "?" + urllib.parse.urlencode({**q, "sig": fremd_zweck})).status_code == 403
    assert not medien.signatur_ok("alpha", "ast.jpg", bis, q["sig"])


def test_bild_folgt_keinem_link(c, ordner, tmp_path_factory):
    draussen = tmp_path_factory.mktemp("draussen") / "geheim.jpg"
    draussen.write_bytes(PNG)
    try:
        (ordner / "link.jpg").symlink_to(draussen)
    except OSError:
        pytest.skip("Symlinks hier nicht erlaubt")
    assert _holen(c, medien.bild_adresse("alpha", "link.jpg")).status_code == 404


def test_bild_namen():
    for gut in ("ast.jpg", "Laden_1.JPG".lower(), "a.webp", "b.gif", "c.jpeg"):
        assert medien.bild_name_ok(gut)
    for schlecht in ("../a.jpg", "a..b.jpg", ".a.jpg", "a.svg", "a.jpg.exe", "", None, "a/b.jpg", "a" * 130 + ".jpg"):
        assert not medien.bild_name_ok(schlecht)
    assert medien.bild_adresse("Alpha!", "ast.jpg") is None and medien.bild_html("alpha", "../x.jpg", "x") == ""


def test_server_haengt_den_bildweg_ein():
    from spaces.marketing.api import server
    wege = {getattr(r, "path", "") for r in server.app.routes}
    assert "/oberflaeche/bild/{name}" in wege


def test_texte_beider_sprachen_gleich():
    de, en = entwuerfe.TEXTE["de"], entwuerfe.TEXTE["en"]
    assert set(de) == set(en)
    for k in de:
        assert set(re.findall(r"{(\w+)}", de[k])) == set(re.findall(r"{(\w+)}", en[k])), k
