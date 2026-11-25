"""Marketing-Oberflaeche, Stufe B Tag 3: Plan und Kampagnen planen, Markenprofil ueber den Assistenten
bearbeiten, Vorschlaege uebernehmen oder verwerfen. Ohne Datenbank: daten.* wird durch Attrappen ersetzt,
der Datenzugang gegen gefaelschte Pult-Funktionen geprueft. Alle Firmen und Inhalte sind erfunden;
in keinem Test wird etwas versendet."""
from __future__ import annotations

import html
import re
from datetime import date

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from spaces.marketing.api import oberflaeche
from spaces.marketing.api.oberflaeche import daten, huelle, marke, plan
from spaces.marketing.claw.markenprofil import ABSCHNITT_REIHENFOLGE

KEY = "test-ui-schluessel"
H = {"Authorization": f"Bearer {KEY}"}
FIRMEN = [{"id": "alpha", "name": "Alpha GmbH", "aktiv": True}, {"id": "beta", "name": "Beta AG", "aktiv": True}]
P1 = "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
P2 = "11111111-2222-4333-8444-555555555555"
K1 = "22222222-2222-4333-8444-555555555555"
V1 = "33333333-2222-4333-8444-555555555555"


class Attrappe:
    def __init__(self):
        self.aufrufe: list[tuple] = []
        self.plan = [
            {"id": P1, "datum": "2026-12-03", "art": "newsletter", "titel": "Adventsmail", "status": "vorschlag",
             "notiz": "Mit Gutschein"},
            {"id": P2, "datum": "2026-12-03", "art": "post", "titel": "Teamfoto", "status": "geplant", "notiz": ""}]
        self.kampagnen = [{"id": K1, "name": "Winteraktion", "ziel": "Mehr Anfragen", "zielgruppe": "verteiler",
                           "von": "2026-12-01", "bis": "2026-12-24", "status": "in_planung", "beitraege": []}]
        self.marke = {
            "mandant": "alpha", "name": "Alpha GmbH",
            "spiegel": {"gestalt": {"akzent": "#C2185B", "flaeche": "#fce4ec",
                                    "schriften": {"anzeige": "inter", "text": "inter"}}, "stand": "2026-11-20"},
            "profil": {"werte": {"grund": "#ffffff", "text": "#111111", "stimme": "warm", "webseite": ""},
                       "abschnitte": {"Kurz gesagt": "Wir bauen Dinge.", "Ton": "Freundlich <und> klar"}},
            "abschnitt_namen": list(ABSCHNITT_REIHENFOLGE), "pflichtteil": {}, "laeuft": False,
            "vorschlag": {"id": V1, "ist_rueckgaengig": False, "vorschlag": {
                "akzent": "#00897b", "aenderungen": [
                    {"feld": "akzent", "vorher": "#c2185b", "nachher": "#00897b"},
                    {"feld": "Ton", "vorher": "Freundlich", "nachher": "Freundlich & <direkt>"}]}}}
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
        mach("plan", lambda m, von, bis: [p for p in self.plan if von <= p["datum"] <= bis])
        mach("kampagnen", lambda m: self.kampagnen)
        mach("marke", lambda m: self.marke)
        mach("plan_anlegen", lambda *w: P1)
        mach("plan_aendern", lambda pid, aenderung: aenderung.get("status", "geplant"))
        mach("kampagne_anlegen", lambda *w: K1)
        mach("kampagne_status", lambda kid, status: None)
        mach("marke_bearbeiten", lambda m, formular: "auftrag-1")
        mach("marke_uebernehmen", lambda m, vid, von: None)
        mach("marke_verwerfen", lambda m, vid, von: None)

    def namen(self) -> list[str]:
        return [x[0] for x in self.aufrufe]

    def aufruf(self, name: str) -> tuple:
        return next(x for x in self.aufrufe if x[0] == name)


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


def _csrf() -> dict:
    return {"csrf": huelle.csrf_marke()}


def _post(c, pfad: str, daten_: dict, **mehr):
    return c.post(pfad, headers={**H, **mehr.pop("headers", {})}, data=daten_, follow_redirects=False, **mehr)


def formularwerte(text: str, action: str, nummer: int = 0) -> dict:
    """Alle Felder des n-ten Formulars mit dieser action, wie ein Browser sie (ohne Knopf) schicken wuerde."""
    teil = text.split(f'action="{action}"')[nummer + 1].split("</form>", 1)[0]
    werte = {html.unescape(n): html.unescape(v)
             for n, v in re.findall(r'<input[^>]* name="([^"]+)" value="([^"]*)"', teil)}
    for n, v in re.findall(r'<textarea name="([^"]+)"[^>]*>(.*?)</textarea>', teil, flags=re.S):
        werte[html.unescape(n)] = html.unescape(v)
    for n, auswahl in re.findall(r'<select name="([^"]+)">(.*?)</select>', teil, flags=re.S):
        gewaehlt = re.search(r'<option value="([^"]*)" selected>', auswahl)
        werte[n] = html.unescape(gewaehlt.group(1)) if gewaehlt else ""
    return werte


PLAN = "/oberflaeche/plan"
MARKE = "/oberflaeche/marke"


# --- Plan: Seite --------------------------------------------------------------------------------

def test_plan_zeigt_formulare_und_neuen_hinweis(c, a):
    r = c.get(f"{PLAN}?monat=2026-12", headers=H)
    assert r.status_code == 200
    assert "Neuer Eintrag" in r.text and "Neue Kampagne" in r.text and "Nur Ansicht" not in r.text
    assert "Ein Planeintrag versendet nichts" in r.text
    assert f'action="{PLAN}/anlegen"' in r.text and f'action="{PLAN}/kampagne"' in r.text
    assert r.text.count(f'action="{PLAN}/aendern"') == 3      # Bestaetigen/Absagen + zwei Bearbeiten
    assert 'value="geplant">Bestätigen</button>' in r.text
    assert f'action="{PLAN}/kampagne/status"' in r.text
    assert "<script" not in r.text and "frame-src" not in r.headers["content-security-policy"]
    assert "Bestätigen oder absagen direkt am Eintrag" in r.text


def test_plan_neuer_eintrag_vorgabe_ist_monatsanfang(c, a):
    r = c.get(f"{PLAN}?monat=2030-05", headers=H)
    werte = formularwerte(r.text, f"{PLAN}/anlegen")
    assert werte["datum"] == "2030-05-01" and werte["monat"] == "2030-05" and werte["art"] == "newsletter"


def test_plan_ohne_gueltige_id_kein_formular(c, a):
    a.plan = [{"id": "kaputt", "datum": "2026-12-03", "art": "post", "titel": "Ohne Id", "status": "geplant"}]
    r = c.get(f"{PLAN}?monat=2026-12", headers=H)
    assert "Ohne Id" in r.text and f'action="{PLAN}/aendern"' not in r.text


@pytest.mark.parametrize("meldung, text", [("angelegt", "Eintrag angelegt."), ("geaendert", "Eintrag geändert."),
                                           ("k_angelegt", "Der Agent schlägt jetzt Beiträge vor."),
                                           ("unveraendert", "Nichts geändert.")])
def test_plan_meldungen(c, a, meldung, text):
    r = c.get(f"{PLAN}?monat=2026-12&{meldung}=1", headers=H)
    assert '<p class="erfolg">' in r.text and html.escape(text, quote=False) in r.text


# --- Plan: Eintrag anlegen ----------------------------------------------------------------------

def test_eintrag_anlegen(c, a):
    werte = {**_csrf(), "monat": "2026-12", "datum": "2027-01-15", "art": "aktion",
             "titel": "  Messe\r\nStand  ", "notiz": "Halle 3\r\nStand 12"}
    r = _post(c, f"{PLAN}/anlegen", werte)
    assert r.status_code == 303 and r.headers["location"] == f"{PLAN}?monat=2027-01&angelegt=1"
    assert a.aufruf("plan_anlegen") == ("plan_anlegen", "alpha", "2027-01-15", "aktion", "Messe Stand",
                                        "Halle 3\nStand 12")


def test_eintrag_anlegen_fuer_gewaehlte_firma(c, a):
    werte = {**_csrf(), "datum": "2026-12-01", "art": "post", "titel": "Beta-Post"}
    c.cookies.set(huelle.COOKIE_FIRMA, "beta")
    r = _post(c, f"{PLAN}/anlegen", werte)
    assert r.status_code == 303 and a.aufruf("plan_anlegen")[1] == "beta"


@pytest.mark.parametrize("aenderung, text", [
    ({"datum": "kaputt"}, "Das Datum fehlt oder ist ungültig."),
    ({"datum": "1999-12-31"}, "Das Datum fehlt oder ist ungültig."),
    ({"art": "werbung"}, "Bitte eine Art wählen."),
    ({"titel": "   "}, "Der Titel braucht 1 bis 120 Zeichen."),
    ({"titel": "x" * 121}, "Der Titel braucht 1 bis 120 Zeichen."),
    ({"notiz": "x" * 1001}, "Die Notiz darf höchstens 1000 Zeichen haben."),
])
def test_eintrag_anlegen_abgewiesen(c, a, aenderung, text):
    werte = {**_csrf(), "datum": "2026-12-01", "art": "post", "titel": "Gut", **aenderung}
    r = _post(c, f"{PLAN}/anlegen", werte)
    assert r.status_code == 422 and html.escape(text, quote=False) in r.text
    assert "plan_anlegen" not in a.namen()


def test_eintrag_anlegen_csrf_und_schluessel(c, a):
    werte = {"datum": "2026-12-01", "art": "post", "titel": "Gut"}
    assert _post(c, f"{PLAN}/anlegen", werte).status_code == 403
    assert c.post(f"{PLAN}/anlegen", data={**_csrf(), **werte}, follow_redirects=False).status_code == 401
    assert "plan_anlegen" not in a.namen()


def test_eintrag_anlegen_datenbank_lehnt_ab(c, a):
    a.fehler["plan_anlegen"] = daten.DatenFehler("abgelehnt", "titel: 1 bis 120 Zeichen")
    r = _post(c, f"{PLAN}/anlegen", {**_csrf(), "datum": "2026-12-01", "art": "post", "titel": "Gut"})
    assert r.status_code == 422 and "titel: 1 bis 120 Zeichen" in r.text


# --- Plan: Eintrag aendern, verschieben, bestaetigen --------------------------------------------

def test_vorschlag_bestaetigen(c, a):
    r = c.get(f"{PLAN}?monat=2026-12", headers=H)
    werte = formularwerte(r.text, f"{PLAN}/aendern", 0)
    assert werte["id"] == P1 and werte["alt"] == "2026-12-03"
    r = _post(c, f"{PLAN}/aendern", {**werte, "status": "geplant"})
    assert r.status_code == 303 and r.headers["location"] == f"{PLAN}?monat=2026-12&geaendert=1"
    assert ("plan", "alpha", "2026-12-03", "2026-12-03") in a.aufrufe
    assert a.aufruf("plan_aendern") == ("plan_aendern", P1, {"status": "geplant"})


def test_eintrag_verschieben_schickt_nur_das_datum(c, a):
    r = c.get(f"{PLAN}?monat=2026-12", headers=H)
    werte = formularwerte(r.text, f"{PLAN}/aendern", 2)       # Bearbeiten-Formular von Teamfoto
    assert werte["id"] == P2 and werte["status"] == "" and werte["titel"] == "Teamfoto"
    r = _post(c, f"{PLAN}/aendern", {**werte, "datum": "2027-01-08"})
    assert r.headers["location"] == f"{PLAN}?monat=2027-01&geaendert=1"
    assert a.aufruf("plan_aendern") == ("plan_aendern", P2, {"datum": "2027-01-08"})


def test_eintrag_ohne_aenderung(c, a):
    r = c.get(f"{PLAN}?monat=2026-12", headers=H)
    werte = formularwerte(r.text, f"{PLAN}/aendern", 1)
    assert werte["id"] == P1 and werte["notiz"] == "Mit Gutschein"
    r = _post(c, f"{PLAN}/aendern", werte)
    assert r.headers["location"] == f"{PLAN}?monat=2026-12&unveraendert=1"
    assert "plan_aendern" not in a.namen()


def test_eintrag_titel_notiz_und_status(c, a):
    werte = {**_csrf(), "monat": "2026-12", "id": P2, "alt": "2026-12-03", "datum": "2026-12-03",
             "status": "erledigt", "titel": "Teamfoto neu", "notiz": "mit allen"}
    r = _post(c, f"{PLAN}/aendern", werte)
    assert r.status_code == 303
    assert a.aufruf("plan_aendern")[2] == {"status": "erledigt", "titel": "Teamfoto neu", "notiz": "mit allen"}


@pytest.mark.parametrize("aenderung", [{"id": "kaputt"}, {"alt": "2026-12-04"}, {"id": "44444444-2222-4333-8444-555555555555"}])
def test_eintrag_weg_oder_fremd(c, a, aenderung):
    werte = {**_csrf(), "id": P2, "alt": "2026-12-03", "status": "abgesagt", **aenderung}
    r = _post(c, f"{PLAN}/aendern", werte)
    assert r.status_code == 422 and "Diesen Eintrag gibt es an seinem Datum nicht mehr" in r.text
    assert "plan_aendern" not in a.namen()


def test_eintrag_status_vorschlag_nicht_setzbar(c, a):
    r = _post(c, f"{PLAN}/aendern", {**_csrf(), "id": P2, "alt": "2026-12-03", "status": "vorschlag"})
    assert r.status_code == 422 and "Diesen Status gibt es nicht." in r.text
    assert "plan_aendern" not in a.namen()


def test_aenderung_lesen_laesst_fehlendes_stehen():
    alt = {"status": "geplant", "datum": "2026-12-03", "titel": "T", "notiz": "N  "}
    assert plan.aenderung_lesen({"notiz": "N"}, alt, "de") == {}
    assert plan.aenderung_lesen({"status": "geplant", "titel": "T"}, alt, "de") == {}
    assert plan.aenderung_lesen({"titel": "T2"}, alt, "en") == {"titel": "T2"}


# --- Kampagnen ----------------------------------------------------------------------------------

def test_kampagne_anlegen(c, a):
    werte = {**_csrf(), "monat": "2026-12", "name": "Frühjahr", "ziel": "Neue Kunden", "zielgruppe": "sales",
             "von": "2027-03-01", "bis": "2027-03-31"}
    r = _post(c, f"{PLAN}/kampagne", werte)
    assert r.status_code == 303 and r.headers["location"] == f"{PLAN}?monat=2027-03&k_angelegt=1"
    assert a.aufruf("kampagne_anlegen") == ("kampagne_anlegen", "alpha", "Frühjahr", "Neue Kunden", "sales",
                                            "2027-03-01", "2027-03-31", plan.VON)


@pytest.mark.parametrize("aenderung, text", [
    ({"bis": "2027-02-28"}, "„Bis“ darf nicht vor „Von“ liegen."),
    ({"zielgruppe": "alle"}, "Bitte eine Zielgruppe wählen."),
    ({"name": ""}, "Der Name braucht 1 bis 120 Zeichen."),
    ({"ziel": "x" * 2001}, "Das Ziel darf höchstens 2000 Zeichen haben."),
])
def test_kampagne_anlegen_abgewiesen(c, a, aenderung, text):
    werte = {**_csrf(), "name": "Frühjahr", "zielgruppe": "sales", "von": "2027-03-01", "bis": "2027-03-31",
             **aenderung}
    r = _post(c, f"{PLAN}/kampagne", werte)
    assert r.status_code == 422 and html.escape(text, quote=False) in r.text
    assert "kampagne_anlegen" not in a.namen()


def test_kampagne_status(c, a):
    r = c.get(f"{PLAN}?monat=2026-12", headers=H)
    werte = formularwerte(r.text, f"{PLAN}/kampagne/status")
    assert werte["id"] == K1 and werte["status"] == "in_planung"
    assert _post(c, f"{PLAN}/kampagne/status", werte).headers["location"].endswith("&unveraendert=1")
    r = _post(c, f"{PLAN}/kampagne/status", {**werte, "status": "laeuft"})
    assert r.headers["location"] == f"{PLAN}?monat=2026-12&k_geaendert=1"
    assert a.aufruf("kampagne_status") == ("kampagne_status", K1, "laeuft")


@pytest.mark.parametrize("werte, text", [({"id": K1, "status": "fertig"}, "Diesen Status gibt es nicht."),
                                         ({"id": P1, "status": "laeuft"}, "Diese Kampagne gibt es bei dieser Firma nicht."),
                                         ({"id": "x", "status": "laeuft"}, "Diese Kampagne gibt es bei dieser Firma nicht.")])
def test_kampagne_status_abgewiesen(c, a, werte, text):
    r = _post(c, f"{PLAN}/kampagne/status", {**_csrf(), **werte})
    assert r.status_code == 422 and text in r.text and "kampagne_status" not in a.namen()


# --- Marke: Seite -------------------------------------------------------------------------------

def test_marke_zeigt_vorschlag_und_formular(c, a):
    r = c.get(MARKE, headers=H)
    assert r.status_code == 200 and "<script" not in r.text
    assert "Vorschlag des Assistenten" in r.text and "Akzentfarbe" in r.text
    assert "Freundlich &amp; &lt;direkt&gt;" in r.text and "<direkt>" not in r.text
    assert f'name="id" value="{V1}"' in r.text and 'value="uebernehmen">Übernehmen' in r.text
    assert "Profil bearbeiten" in r.text and "Jede Änderung geht als Auftrag" in r.text
    werte = formularwerte(r.text, f"{MARKE}/bearbeiten")
    assert werte["akzent"] == "#C2185B" and werte["zweitfarbe"] == "#fce4ec" and werte["grund"] == "#ffffff"
    assert werte["schrift_anzeige"] == "inter" and werte["stimme"] == "warm"
    assert werte[f"ab{ABSCHNITT_REIHENFOLGE.index('Ton')}"] == "Freundlich <und> klar"
    assert werte["ab0"] == "Wir bauen Dinge." and len([n for n in werte if n.startswith("ab")]) == len(ABSCHNITT_REIHENFOLGE)


def test_marke_ohne_vorschlag_und_mit_lauf(c, a):
    a.marke["vorschlag"] = None
    a.marke["laeuft"] = True
    a.marke["uebernahme"] = "laeuft"
    r = c.get(MARKE, headers={**H, "Accept-Language": "en"})
    assert "Suggestion from the assistant" not in r.text and "Edit profile" in r.text
    assert "The assistant is working on a task right now." in r.text and "A takeover is running" in r.text


def test_marke_vorschlag_ohne_liste_mit_logo(c, a):
    a.marke["vorschlag"] = {"id": V1, "ist_rueckgaengig": True, "vorschlag": {"logo": "medien:neu.png"}}
    r = c.get(MARKE, headers=H)
    assert "nennt keine Änderungsliste" in r.text and "ändert auch das Logo" in r.text
    assert "macht eine frühere Änderung rückgängig" in r.text


# --- Marke: Bearbeiten --------------------------------------------------------------------------

def _marke_formular(c) -> dict:
    return formularwerte(c.get(MARKE, headers=H).text, f"{MARKE}/bearbeiten")


def test_marke_bearbeiten_legt_auftrag_an(c, a):
    werte = _marke_formular(c)
    werte["akzent"] = "#00897b"
    werte["ab0"] = "Wir bauen\r\nbessere Dinge.  "
    r = _post(c, f"{MARKE}/bearbeiten", werte)
    assert r.status_code == 303 and r.headers["location"] == f"{MARKE}?gesendet=1"
    _, firma, formular = a.aufruf("marke_bearbeiten")
    assert firma == "alpha" and formular["akzent"] == "#00897b" and formular["stimme"] == "warm"
    assert set(formular) == set(marke.WERTE) | {"abschnitte"}
    assert list(formular["abschnitte"]) == list(ABSCHNITT_REIHENFOLGE)
    assert formular["abschnitte"]["Kurz gesagt"] == "Wir bauen\nbessere Dinge."


def test_marke_bearbeiten_ohne_aenderung_kein_auftrag(c, a):
    werte = _marke_formular(c)
    werte["akzent"] = "#c2185b"                       # nur Gross/klein: gleiche Farbe
    r = _post(c, f"{MARKE}/bearbeiten", werte)
    assert r.headers["location"] == f"{MARKE}?unveraendert=1" and "marke_bearbeiten" not in a.namen()


def test_marke_bearbeiten_zu_lang(c, a):
    werte = {**_marke_formular(c), "webseite": "https://example.org/" + "x" * 300}
    r = _post(c, f"{MARKE}/bearbeiten", werte)
    assert r.status_code == 422 and "Webseite, höchstens 300 Zeichen" in r.text
    werte = {**_marke_formular(c), "ab1": "x" * 8001}
    r = _post(c, f"{MARKE}/bearbeiten", werte)
    assert r.status_code == 422 and f"{ABSCHNITT_REIHENFOLGE[1]}, höchstens 8000 Zeichen" in r.text
    assert "marke_bearbeiten" not in a.namen()


def test_marke_bearbeiten_assistent_arbeitet(c, a):
    a.fehler["marke_bearbeiten"] = daten.DatenFehler("abgelehnt", "Der Assistent arbeitet gerade")
    r = _post(c, f"{MARKE}/bearbeiten", {**_marke_formular(c), "akzent": "#000000"})
    assert r.status_code == 422 and "Der Assistent arbeitet gerade" in r.text


def test_marke_bearbeiten_csrf(c, a):
    werte = _marke_formular(c)
    werte.pop("csrf")
    assert _post(c, f"{MARKE}/bearbeiten", {**werte, "akzent": "#000000"}).status_code == 403
    assert "marke_bearbeiten" not in a.namen()


# --- Marke: Vorschlag ---------------------------------------------------------------------------

@pytest.mark.parametrize("wahl, aufruf, meldung", [("uebernehmen", "marke_uebernehmen", "uebernommen"),
                                                   ("verwerfen", "marke_verwerfen", "verworfen")])
def test_vorschlag_entscheiden(c, a, wahl, aufruf, meldung):
    r = _post(c, f"{MARKE}/vorschlag", {**_csrf(), "id": V1, "wahl": wahl})
    assert r.status_code == 303 and r.headers["location"] == f"{MARKE}?{meldung}=1"
    assert a.aufruf(aufruf) == (aufruf, "alpha", V1, marke.VON)


@pytest.mark.parametrize("werte", [{"id": V1, "wahl": "loeschen"}, {"id": P1, "wahl": "uebernehmen"},
                                   {"id": "", "wahl": "verwerfen"}])
def test_vorschlag_abgewiesen(c, a, werte):
    r = _post(c, f"{MARKE}/vorschlag", {**_csrf(), **werte})
    assert r.status_code == 422 and "Diesen Vorschlag gibt es nicht mehr offen" in r.text
    assert "marke_uebernehmen" not in a.namen() and "marke_verwerfen" not in a.namen()


def test_vorschlag_ohne_offenen(c, a):
    a.marke["vorschlag"] = None
    r = _post(c, f"{MARKE}/vorschlag", {**_csrf(), "id": V1, "wahl": "uebernehmen"})
    assert r.status_code == 422 and "marke_uebernehmen" not in a.namen()


def test_vorschlag_neueres_profil(c, a):
    a.fehler["marke_uebernehmen"] = daten.DatenFehler("abgelehnt", "Inzwischen gibt es ein neueres Profil")
    r = _post(c, f"{MARKE}/vorschlag", {**_csrf(), "id": V1, "wahl": "uebernehmen"})
    assert r.status_code == 422 and "neueres Profil" in r.text


# --- Grenzen und Datenzugang --------------------------------------------------------------------

def test_formulargrenzen_wie_die_api():
    from spaces.marketing.api import marke as api_marke
    from spaces.marketing.api import plan as api_plan
    from spaces.marketing.api import kampagnen as api_kampagnen
    assert marke.WERTE == {**api_marke.FORMULAR_GRENZEN, **api_marke.FORMULAR_OPTIONAL}
    assert marke.ABSCHNITT_MAX == api_marke.ABSCHNITT_FORM_MAX
    assert set(ABSCHNITT_REIHENFOLGE) == set(api_marke.ABSCHNITTE)
    assert (plan.TITEL_MAX, plan.NOTIZ_MAX) == (api_plan.TITEL_MAX, api_plan.NOTIZ_MAX)
    assert (plan.NAME_MAX, plan.ZIEL_MAX) == (api_kampagnen.NAME_MAX, api_kampagnen.ZIEL_MAX)
    assert plan.ARTEN == api_plan.ARTEN and set(plan.STATUS_BETREIBER) == set(api_plan.STATUS_BETREIBER)
    assert plan.K_STATUS == api_kampagnen.STATUS and plan.ZIELGRUPPEN == api_kampagnen.ZIELGRUPPEN


class FalscheRoute:
    def __init__(self, antwort=None, fehler=None):
        self.antwort, self.fehler, self.werte = antwort, fehler, None

    def __call__(self, **werte):
        self.werte = werte
        if self.fehler:
            raise self.fehler
        return self.antwort


def test_daten_plan_und_kampagnen(monkeypatch):
    from spaces.marketing.api import kampagnen as api_kampagnen
    from spaces.marketing.api import plan as api_plan
    monkeypatch.setenv(daten.PULT_ENV, "pult-test")
    anlegen, aendern = FalscheRoute({"id": P1}), FalscheRoute({"status": "verschoben"})
    monkeypatch.setattr(api_plan, "pult_anlegen", anlegen)
    monkeypatch.setattr(api_plan, "pult_aendern", aendern)
    assert daten.plan_anlegen("alpha", "2026-12-01", "post", "T", "") == P1
    assert anlegen.werte == {"x_pult_key": "pult-test", "payload": {
        "mandant": "alpha", "datum": "2026-12-01", "art": "post", "titel": "T", "notiz": ""}}
    assert daten.plan_aendern(P1, {"datum": "2026-12-02", "status": None, "inhalt": "x"}) == "verschoben"
    assert aendern.werte == {"pid": P1, "x_pult_key": "pult-test", "payload": {"datum": "2026-12-02"}}
    k_an, k_st = FalscheRoute({"id": K1, "status": "in_planung"}), FalscheRoute({"status": "laeuft"})
    monkeypatch.setattr(api_kampagnen, "pult_anlegen", k_an)
    monkeypatch.setattr(api_kampagnen, "pult_status", k_st)
    assert daten.kampagne_anlegen("alpha", "N", "Z", "sales", "2027-01-01", "2027-01-31", "Wer") == K1
    assert k_an.werte["payload"] == {"mandant": "alpha", "name": "N", "ziel": "Z", "zielgruppe": "sales",
                                     "filter": {}, "von": "2027-01-01", "bis": "2027-01-31", "von_wer": "Wer"}
    daten.kampagne_status(K1, "laeuft")
    assert k_st.werte == {"kid": K1, "x_pult_key": "pult-test", "payload": {"status": "laeuft"}}
    monkeypatch.setattr(api_plan, "pult_anlegen", FalscheRoute({}))
    with pytest.raises(daten.DatenFehler):
        daten.plan_anlegen("alpha", "2026-12-01", "post", "T", "")


def test_daten_marke(monkeypatch):
    from spaces.marketing.api import marke as api_marke
    monkeypatch.setenv(daten.PULT_ENV, "pult-test")
    bearbeiten, ueber, verw = FalscheRoute({"auftrag": "a1"}), FalscheRoute({"auftrag": "a2"}), FalscheRoute({})
    monkeypatch.setattr(api_marke, "marke_bearbeiten", bearbeiten)
    monkeypatch.setattr(api_marke, "marke_uebernehmen", ueber)
    monkeypatch.setattr(api_marke, "marke_verwerfen", verw)
    assert daten.marke_bearbeiten("alpha", {"akzent": "#000000"}) == "a1"
    assert bearbeiten.werte["payload"] == {"mandant": "alpha", "formular": {"akzent": "#000000"}}
    daten.marke_uebernehmen("alpha", V1, "Wer")
    daten.marke_verwerfen("alpha", V1, "Wer")
    assert ueber.werte == {"vid": V1, "x_pult_key": "pult-test", "payload": {"von": "Wer", "mandant": "alpha"}}
    assert verw.werte["vid"] == V1
    monkeypatch.setattr(api_marke, "marke_uebernehmen", FalscheRoute(fehler=HTTPException(422, "neueres Profil")))
    with pytest.raises(daten.DatenFehler) as f:
        daten.marke_uebernehmen("alpha", V1, "Wer")
    assert f.value.art == "abgelehnt" and "neueres Profil" in f.value.grund


def test_echte_formularwerte_passen_zur_api():
    """Was das Formular baut, nimmt _formular der API an (ohne Datenbank)."""
    from spaces.marketing.api import marke as api_marke
    formular = marke.formular_lesen({"akzent": "#123456", "ab0": "Text", "stimme": "warm"}, "de")
    assert api_marke._formular(formular)["abschnitte"]["Kurz gesagt"] == "Text"
    neu = plan.eintrag_lesen({"datum": "2026-12-01", "art": "post", "titel": "T"}, "de")
    from spaces.marketing.api import plan as api_plan
    assert api_plan._datum(neu["datum"]) == "2026-12-01" and neu["art"] in api_plan.ARTEN
    assert date.fromisoformat(neu["datum"]) == date(2026, 12, 1)


def test_server_haengt_die_wege_ein():
    from spaces.marketing.api import server
    pfade = {getattr(r, "path", "") for r in server.app.routes}
    for pfad in ("/oberflaeche/plan/anlegen", "/oberflaeche/plan/aendern", "/oberflaeche/plan/kampagne",
                 "/oberflaeche/plan/kampagne/status", "/oberflaeche/marke/bearbeiten", "/oberflaeche/marke/vorschlag"):
        assert pfad in pfade
