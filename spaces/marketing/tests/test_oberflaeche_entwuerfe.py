"""Marketing-Oberflaeche, Stufe B Tag 2: Entwuerfe, Block-Editor ohne Skript, Speichern, Einreichen und
Zurueckziehen. Ohne Datenbank: daten.* wird durch Attrappen ersetzt, der Datenzugang gegen gefaelschte
Pult- und Freigabe-Funktionen geprueft. Alle Firmen und Inhalte sind erfunden. Einreichen setzt nur den
Status; versendet wird in keinem Test etwas."""
from __future__ import annotations

import copy
import html
import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from spaces.marketing.api import oberflaeche
from spaces.marketing.api.oberflaeche import daten, entwuerfe, huelle

KEY = "test-ui-schluessel"
H = {"Authorization": f"Bearer {KEY}"}
IID = "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
FIRMEN = [{"id": "alpha", "name": "Alpha GmbH", "aktiv": True}]

DOK = {
    "root": {"type": "EmailLayout", "data": {"childrenIds": ["kopf", "rahmen", "spalten", "trenner"]}},
    "kopf": {"type": "Heading", "data": {"props": {"text": "Frühling bei Muster", "level": "h1"}, "style": {}}},
    "rahmen": {"type": "Container", "data": {"props": {"childrenIds": ["absatz"]}}},
    "absatz": {"type": "Text", "data": {"props": {"text": "Hallo {vorname},\nschön, dass du da bist."}}},
    "spalten": {"type": "ColumnsContainer", "data": {"props": {"columns": [
        {"childrenIds": ["bild"]}, {"childrenIds": ["knopf"]}]}}},
    "bild": {"type": "Image", "data": {"props": {"url": "medien:fruehling.jpg", "alt": "Blumen"}}},
    "knopf": {"type": "Button", "data": {"props": {"text": "Zum Angebot", "url": "https://example.org/a"}}},
    "trenner": {"type": "Divider", "data": {"props": {"lineColor": "#333333"}}},
    "verwaist": {"type": "Text", "data": {"props": {"text": "nicht im Baum"}}},
}


class Attrappe:
    def __init__(self):
        self.aufrufe: list[tuple] = []
        self.liste = [
            {"id": IID, "titel": "Frühlingsbrief", "status": "entwurf", "fassungen": 2, "format": "bloecke",
             "erstellt_am": "2026-11-20T10:00:00+00:00"},
            {"id": "11111111-2222-4333-8444-555555555555", "titel": "Zurückgegeben", "status": "entwurf",
             "offene_rueckmeldungen": 1, "fassungen": 1},
            {"id": "22222222-2222-4333-8444-555555555555", "titel": "Liegt bei Sales", "status": "eingereicht",
             "fassungen": 3},
            {"id": "33333333-2222-4333-8444-555555555555", "titel": "Alt freigegeben", "status": "freigegeben"},
        ]
        self.entwurf = {
            "inhalt": {"id": IID, "mandant": "alpha", "titel": "Frühlingsbrief", "status": "entwurf"},
            "fassungen": [{"fassung": 2, "format": "bloecke", "bloecke": copy.deepcopy(DOK),
                           "felder": {"betreff": "Frühling", "vorschautext": "Neu im Laden"}},
                          {"fassung": 1, "format": "bloecke", "bloecke": None}],
            "rueckmeldungen": [{"text": "Bitte den Knopf ändern", "von": "Erika Muster", "am": "2026-11-21",
                                "erledigt": False},
                               {"text": "erledigt", "von": "x", "am": "2026-11-20", "erledigt": True}]}
        self.gespeichert: list[tuple] = []
        # Bilderliste hier nicht lesbar: der Editor zeigt dann nur den Dateinamen (Bildwahl: test_oberflaeche_bildwahl)
        self.fehler: dict[str, daten.DatenFehler] = {"bilder": daten.DatenFehler("nicht_erreichbar"),
                                                     "chat_stand": daten.DatenFehler("nicht_erreichbar")}
        self.chat = {"laeuft": False, "verlauf": [], "neueste_fassung": 2}
        self.gerechnet: list[tuple] = []
        self.angelegt: list[tuple] = []
        self.bilder: list[str] = []

    def einsetzen(self, monkeypatch):
        def mach(name, holen):
            def f(*werte):
                self.aufrufe.append((name, *werte))
                if name in self.fehler:
                    raise self.fehler[name]
                return holen(*werte)
            monkeypatch.setattr(daten, name, f)
        mach("firmen", lambda: FIRMEN)
        mach("newsletter", lambda m: self.liste)
        mach("entwurf", lambda i: self.entwurf)
        mach("bloecke_speichern", lambda i, b, be, v, d: self._speichern(i, b, be, v, d))
        mach("einreichen", lambda i, von: None)
        mach("zurueckziehen", lambda i, von: None)
        mach("bilder", lambda m: self.bilder)
        mach("seite_speichern", lambda i, b, be, v, d: self._speichern(i, b, be, v, d))
        # Tag 6: Assistent, Flaechen, Umwandeln, neue Collage
        mach("chat_stand", lambda i: self.chat)
        mach("chat_senden", lambda i, n: {"auftrag": "a1", "status": "offen"})
        mach("chat_stoppen", lambda i, art, auftrag: None)
        mach("chat_rueckgaengig", lambda i, auftrag: 4)
        mach("gestaltung_rechnen", lambda i, g: self._rechnen(i, g))
        mach("in_bloecke", lambda i, von: 3)
        mach("collage_anlegen", lambda m, ti, be: "77777777-2222-4333-8444-555555555555")

    def _rechnen(self, i, g):
        self.gerechnet.append((i, g))
        return {"url": "medien:gs-abcdefabcdef.jpg", "width": 600, "height": 400, "hinweise": []}

    def _speichern(self, i, b, be, v, d):
        self.gespeichert.append((i, b, be, v, d))
        return b + 1

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


def formularwerte(text: str, action: str) -> dict:
    """Alle Felder des Formulars mit dieser action, wie ein Browser sie schicken wuerde."""
    teil = text.split(f'action="{action}"', 1)[1].split("</form>", 1)[0]
    werte = {html.unescape(n): html.unescape(v) for n, v in re.findall(r'<input[^>]* name="([^"]+)" value="([^"]*)"', teil)}
    for n, v in re.findall(r'<textarea name="([^"]+)"[^>]*>(.*?)</textarea>', teil, flags=re.S):
        werte[html.unescape(n)] = html.unescape(v)
    return werte


EDITOR = f"/oberflaeche/entwuerfe/{IID}"
SPEICHERN = f"{EDITOR}/speichern"


# --- Liste --------------------------------------------------------------------------------------

def test_liste_gruppiert_und_verlinkt(c, a):
    r = c.get("/oberflaeche/entwuerfe", headers=H)
    assert r.status_code == 200
    assert r.headers["Content-Security-Policy"] == huelle.CSP
    for gruppe in ("In Arbeit (1)", "Zurückgegeben (1)", "Zur Freigabe in Sales (1)"):
        assert gruppe in r.text
    assert f'href="/oberflaeche/entwuerfe/{IID}"' in r.text
    assert "Alt freigegeben" not in r.text
    assert '<a href="/oberflaeche/entwuerfe" class="aktiv" aria-current="page">' in r.text
    assert "<script" not in r.text.lower() and " style=" not in r.text


def test_liste_leer(c, a):
    a.liste = []
    assert "Keine Entwürfe" in c.get("/oberflaeche/entwuerfe", headers=H).text


def test_liste_ohne_schluessel(c, a):
    assert c.get("/oberflaeche/entwuerfe").status_code == 401 and a.aufrufe == []


# --- Editor -------------------------------------------------------------------------------------

def test_editor_zeigt_felder_in_lesereihenfolge(c, a):
    r = c.get(EDITOR, headers=H)
    assert r.status_code == 200
    assert r.headers["Content-Security-Policy"] == huelle.CSP_RAHMEN
    assert "<script" not in r.text.lower() and " style=" not in r.text
    namen = re.findall(r'name="([tua]:[a-z]+)"', r.text)
    assert namen == ["t:kopf", "t:absatz", "a:bild", "t:knopf", "u:knopf"]
    assert "nicht im Baum" not in r.text                 # Bloecke ausserhalb des Baums nie
    assert 'name="basis" value="2"' in r.text
    assert "Hallo {vorname},\nschön, dass du da bist.</textarea>" in r.text
    assert "Datei: fruehling.jpg" in r.text
    assert "Bitte den Knopf ändern" in r.text and ">erledigt<" not in r.text
    assert f'action="{EDITOR}/einreichen"' in r.text and "<iframe" in r.text
    assert r.text.count(f'name="csrf" value="{huelle.csrf_marke()}"') == 3   # Sprache, Speichern, Einreichen


def test_editor_fremde_firma(c, a):
    a.entwurf["inhalt"]["mandant"] = "beta"
    r = c.get(EDITOR, headers=H)
    assert r.status_code == 422 and "anderen Firma" in r.text and "<form method=\"post\" action=\"/oberflaeche/entwuerfe" not in r.text


def test_editor_kaputte_id(c, a):
    assert c.get("/oberflaeche/entwuerfe/kein-uuid", headers=H).status_code == 422
    assert "entwurf" not in a.namen()


def test_editor_eingereicht_zeigt_nur_zurueckziehen(c, a):
    a.entwurf["inhalt"]["status"] = "eingereicht"
    r = c.get(EDITOR, headers=H)
    assert f'action="{EDITOR}/zurueckziehen"' in r.text
    assert f'action="{SPEICHERN}"' not in r.text and f'action="{EDITOR}/einreichen"' not in r.text
    assert "liegt zur Freigabe in Sales" in r.text


def test_editor_felder_und_kaputte_collage_nur_hinweis(c, a):
    a.entwurf["fassungen"][0] = {"fassung": 2, "format": "felder", "felder": {"betreff": "x"}}
    r = c.get(EDITOR, headers=H)
    assert f'action="{EDITOR}/umwandeln"' in r.text and f'action="{SPEICHERN}"' not in r.text   # seit Tag 6
    a.entwurf["fassungen"][0] = {"fassung": 2, "format": "seite", "seite": {}}      # Collage ohne Ebenen
    r = c.get(EDITOR, headers=H)
    assert "nicht bearbeiten" in r.text and f'action="{SPEICHERN}"' not in r.text and "<iframe" not in r.text


def test_editor_meldungen(c, a):
    assert "Gespeichert als Fassung 3." in c.get(f"{EDITOR}?gespeichert=3", headers=H).text
    assert "Eingereicht." in c.get(f"{EDITOR}?eingereicht=1", headers=H).text


# --- Speichern ----------------------------------------------------------------------------------

def _post(c, pfad, werte, **kopf):
    return c.post(pfad, headers={**H, **kopf}, data=werte, follow_redirects=False)


def test_speichern_aendert_nur_die_feldtexte(c, a):
    werte = formularwerte(c.get(EDITOR, headers=H).text, SPEICHERN)
    werte.update({"t:kopf": "Sommer\nbei Muster", "t:absatz": "Hallo {vorname},\r\nneu.", "u:knopf": "https://example.org/b",
                  "a:bild": "Sonne", "betreff": "Sommer", "t:fremd": "eingeschmuggelt", "t:verwaist": "auch nicht"})
    r = _post(c, SPEICHERN, werte)
    assert r.status_code == 303 and r.headers["location"] == f"{EDITOR}?gespeichert=3"
    iid, basis, betreff, vorschau, dok = a.gespeichert[0]
    assert (iid, basis, betreff, vorschau) == (IID, 2, "Sommer", "Neu im Laden")
    erwartet = copy.deepcopy(DOK)
    erwartet["kopf"]["data"]["props"]["text"] = "Sommer bei Muster"      # Ueberschrift einzeilig
    erwartet["absatz"]["data"]["props"]["text"] = "Hallo {vorname},\nneu."
    erwartet["knopf"]["data"]["props"]["url"] = "https://example.org/b"
    erwartet["bild"]["data"]["props"]["alt"] = "Sonne"
    assert dok == erwartet                                               # nichts sonst, nichts Fremdes
    assert a.entwurf["fassungen"][0]["bloecke"] == DOK                   # Original unberuehrt


def test_speichern_ohne_aenderung_legt_keine_fassung_an(c, a):
    werte = formularwerte(c.get(EDITOR, headers=H).text, SPEICHERN)
    r = _post(c, SPEICHERN, werte)
    assert r.headers["location"] == f"{EDITOR}?unveraendert=1" and a.gespeichert == []


@pytest.mark.parametrize("aenderung, text", [
    ({"u:knopf": "http://example.org"}, "https://"),
    ({"u:knopf": "javascript:alert(1)"}, "https://"),
    ({"u:knopf": "https://example.org/a b"}, "https://"),
    ({"t:kopf": "x" * 301}, "zu lang"),
    ({"betreff": "x" * 201}, "zu lang"),
    ({"basis": "1"}, "neuere Fassung"),
    ({"basis": ""}, "Fehlende Fassung"),
])
def test_speichern_abgewiesen(c, a, aenderung, text):
    werte = formularwerte(c.get(EDITOR, headers=H).text, SPEICHERN)
    werte.update(aenderung)
    r = _post(c, SPEICHERN, werte)
    assert r.status_code == 422 and text in r.text and a.gespeichert == []


def test_speichern_csrf_und_schluessel(c, a):
    werte = formularwerte(c.get(EDITOR, headers=H).text, SPEICHERN)
    ohne = {k: v for k, v in werte.items() if k != "csrf"}
    assert _post(c, SPEICHERN, ohne).status_code == 403
    assert _post(c, SPEICHERN, werte, **{"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post(SPEICHERN, data=werte).status_code == 401
    assert a.gespeichert == []


def test_speichern_nur_im_status_entwurf_und_eigener_firma(c, a):
    werte = formularwerte(c.get(EDITOR, headers=H).text, SPEICHERN)
    a.entwurf["inhalt"]["status"] = "eingereicht"
    assert _post(c, SPEICHERN, werte).status_code == 422
    a.entwurf["inhalt"]["status"] = "entwurf"
    a.entwurf["inhalt"]["mandant"] = "beta"
    assert _post(c, SPEICHERN, werte).status_code == 422
    assert a.gespeichert == []


def test_speichern_grosses_formular_geht(c, a):
    werte = formularwerte(c.get(EDITOR, headers=H).text, SPEICHERN)
    werte["t:absatz"] = "ä" * 9000                        # mehr als die 4 KB der Umschalter
    assert _post(c, SPEICHERN, werte).status_code == 303
    assert a.gespeichert[0][4]["absatz"]["data"]["props"]["text"] == "ä" * 9000


def test_speichern_datenbank_lehnt_ab(c, a):
    a.fehler["bloecke_speichern"] = daten.DatenFehler("abgelehnt", "Basis ist nicht die neueste Fassung")
    werte = formularwerte(c.get(EDITOR, headers=H).text, SPEICHERN)
    werte["betreff"] = "Neu"
    r = _post(c, SPEICHERN, werte)
    assert r.status_code == 422 and "nicht die neueste Fassung" in r.text


# --- Einreichen und Zurueckziehen ---------------------------------------------------------------

def _csrf() -> dict:
    return {"csrf": huelle.csrf_marke()}


def test_einreichen(c, a):
    r = _post(c, f"{EDITOR}/einreichen", _csrf())
    assert r.status_code == 303 and r.headers["location"] == f"{EDITOR}?eingereicht=1"
    assert ("einreichen", IID, entwuerfe.VON) in a.aufrufe


def test_einreichen_nur_aus_entwurf_und_mit_csrf(c, a):
    assert _post(c, f"{EDITOR}/einreichen", {}).status_code == 403
    a.entwurf["inhalt"]["status"] = "eingereicht"
    assert _post(c, f"{EDITOR}/einreichen", _csrf()).status_code == 422
    assert "einreichen" not in a.namen()


def test_einreichen_grund_der_datenbank(c, a):
    a.fehler["einreichen"] = daten.DatenFehler("abgelehnt", "Der Assistent arbeitet gerade")
    r = _post(c, f"{EDITOR}/einreichen", _csrf())
    assert r.status_code == 422 and "Der Assistent arbeitet gerade" in r.text


def test_zurueckziehen(c, a):
    assert _post(c, f"{EDITOR}/zurueckziehen", _csrf()).status_code == 422     # Entwurf: nichts zu ziehen
    a.entwurf["inhalt"]["status"] = "eingereicht"
    r = _post(c, f"{EDITOR}/zurueckziehen", _csrf())
    assert r.status_code == 303 and r.headers["location"] == f"{EDITOR}?zurueckgezogen=1"
    assert ("zurueckziehen", IID, entwuerfe.VON) in a.aufrufe


def test_post_fremde_firma(c, a):
    a.entwurf["inhalt"]["mandant"] = "beta"
    assert _post(c, f"{EDITOR}/einreichen", _csrf()).status_code == 422
    assert "einreichen" not in a.namen()


# --- Bloecke und Datenzugang --------------------------------------------------------------------

def test_bearbeitbare_ueberspringt_kreise_und_kaputtes():
    dok = {"root": {"data": {"childrenIds": ["a", "a", "kreis", 7, "../x", "fehlt"]}},
           "a": {"type": "Text", "data": {"props": {"text": "eins"}}},
           "kreis": {"type": "Container", "data": {"props": {"childrenIds": ["kreis", "a"]}}}}
    assert [bid for bid, _ in entwuerfe.bearbeitbare(dok)] == ["a"]
    assert entwuerfe.bearbeitbare({}) == [] and entwuerfe.bearbeitbare({"root": "kaputt"}) == []


class FalscheRoute:
    def __init__(self, antwort=None, fehler=None):
        self.antwort, self.fehler, self.werte = antwort, fehler, None

    def __call__(self, **werte):
        self.werte = werte
        if self.fehler:
            raise self.fehler
        return self.antwort


def test_daten_bloecke_speichern(monkeypatch):
    from spaces.marketing.api import pult
    route = FalscheRoute({"fassung": 3})
    monkeypatch.setattr(pult, "bloecke_speichern", route)
    monkeypatch.setenv(daten.PULT_ENV, "pult-test")
    assert daten.bloecke_speichern(IID, 2, "B", "V", {"root": {}}) == 3
    assert route.werte == {"iid": IID, "x_pult_key": "pult-test", "payload": {
        "basis_fassung": 2, "betreff": "B", "vorschautext": "V", "bloecke": {"root": {}}, "urheber": "betreiber"}}
    monkeypatch.setattr(pult, "bloecke_speichern", FalscheRoute({"fassung": True}))
    with pytest.raises(daten.DatenFehler):
        daten.bloecke_speichern(IID, 2, "B", "V", {})


def test_daten_einreichen_und_zurueckziehen(monkeypatch):
    from spaces.marketing.api import freigabe
    ein, zurueck = FalscheRoute({"status": "eingereicht"}), FalscheRoute({"status": "entwurf"})
    monkeypatch.setattr(freigabe, "einreichen", ein)
    monkeypatch.setattr(freigabe, "zurueckziehen", zurueck)
    daten.einreichen(IID, "Wer")
    daten.zurueckziehen(IID, "Wer")
    assert ein.werte["payload"] == {"von": "Wer"} and zurueck.werte["iid"] == IID
    monkeypatch.setattr(freigabe, "einreichen", FalscheRoute(fehler=HTTPException(422, "Liegt schon zur Freigabe")))
    with pytest.raises(daten.DatenFehler) as f:
        daten.einreichen(IID, "Wer")
    assert f.value.art == "abgelehnt" and "Freigabe" in f.value.grund


def test_server_haengt_die_editor_wege_ein():
    from spaces.marketing.api import server
    pfade = {getattr(r, "path", "") for r in server.app.routes}
    for pfad in ("/oberflaeche/entwuerfe", "/oberflaeche/entwuerfe/{iid}", "/oberflaeche/entwuerfe/{iid}/speichern",
                 "/oberflaeche/entwuerfe/{iid}/einreichen", "/oberflaeche/entwuerfe/{iid}/zurueckziehen"):
        assert pfad in pfade
