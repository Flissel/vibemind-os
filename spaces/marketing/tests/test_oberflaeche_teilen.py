"""Marketing-Oberflaeche, Stufe C Punkt 5: fertige Reels und Folgen als Versandauftrag an Sales teilen,
hinter dem Kosten-Schalter (MARKETING_BESTELLEN), mit Stand je Karte. Ohne Datenbank und ohne sales-claw:
daten.* und die SQL-Wege sind Attrappen. Firmen, Dateien und Texte sind erfunden; es entsteht in keinem
Test ein echter Versandauftrag, und gesendet wird nichts."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from spaces.marketing.api import oberflaeche
from spaces.marketing.api import teilen as api_teilen
from spaces.marketing.api.oberflaeche import bestellen, daten, huelle, teilen

KEY = "test-ui-schluessel"
H = {"Authorization": f"Bearer {KEY}"}
FIRMEN = [{"id": "alpha", "name": "Alpha GmbH", "aktiv": True}]
R_FERTIG, R_OFFEN = "bbbbbbbb-2222-4222-8222-222222222222", "aaaaaaaa-1111-4111-8111-111111111111"
F_FERTIG, F_OFFEN = "eeeeeeee-5555-4555-8555-555555555555", "dddddddd-4444-4444-8444-444444444444"
FREMD = "ffffffff-6666-4666-8666-666666666666"
VA = "99999999-9999-4999-8999-999999999999"


class Attrappe:
    def __init__(self):
        self.aufrufe: list[tuple] = []
        self.antwort = {"ok": True, "id": VA, "wiederholung": False, "grund": "Auftrag liegt bereit."}
        self.stand_fehler = False
        self.teilen_fehler: daten.DatenFehler | None = None
        self.reels = [
            {"id": R_FERTIG, "titel": "Frühling", "status": "fertig", "art": "bilder", "briefing": {},
             "datei": "vid-0123abcd-fruehling.mp4"},
            {"id": R_OFFEN, "titel": "Wochenmarkt", "status": "offen", "art": "bilder", "briefing": {}}]
        self.folgen = [
            {"id": F_FERTIG, "titel": "Folge 1", "status": "fertig", "datei": "pod-0123abcd-folge-1.mp3"},
            {"id": F_OFFEN, "titel": "Folge 2", "status": "offen"}]
        self.stand = {"reel": [
            {"id": VA, "kanal": "linkedin_post", "status": "angenommen", "grund": "", "auftrag": R_FERTIG,
             "angelegt": "2026-11-20 10:00:00+00"},
            {"id": VA, "kanal": "email", "status": "abgelehnt", "auftrag": R_FERTIG,
             "grund": "Kein Kontakt in sales-claw zu muster@example.org", "angelegt": "2026-11-19 10:00:00+00"}],
            "folge": []}

    def einsetzen(self, monkeypatch):
        def mach(name, holen):
            def f(*werte):
                self.aufrufe.append((name, *werte))
                return holen(*werte)
            monkeypatch.setattr(daten, name, f)

        def geteilt(m, art):
            if self.stand_fehler:
                raise daten.DatenFehler("nicht_erreichbar")
            return self.stand[art]

        def teilen_(*werte):
            if self.teilen_fehler is not None:
                raise self.teilen_fehler
            return self.antwort
        mach("firmen", lambda: FIRMEN)
        mach("bilder", lambda m: [])
        mach("reels", lambda m, archiv: [] if archiv else self.reels)
        mach("podcast", lambda m: self.folgen)
        mach("geteilt", geteilt)
        mach("teilen", teilen_)

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


def _post(c, pfad, werte, **kopf):
    return c.post(pfad, headers={**H, **kopf}, data={"csrf": huelle.csrf_marke(), **werte}, follow_redirects=False)


BEITRAG = {"kanal": "linkedin_post", "betreff": "Frühlingskuchen", "empfaenger": "",
           "nachricht": "Ab Montag gibt es neue Kuchen."}


# --- Seiten ---------------------------------------------------------------------------------------

def test_reels_zeigt_teilen_nur_an_fertigen_karten(c, an):
    s = c.get("/oberflaeche/reels?reiter=fertig", headers=H).text
    assert f'action="/oberflaeche/reels/{R_FERTIG}/teilen"' in s
    assert f"/reels/{R_OFFEN}/teilen" not in s
    for kanal in teilen.KANAELE:
        assert f'value="{kanal}"' in s


def test_reels_zeigt_stand_mit_grund(c, an):
    s = c.get("/oberflaeche/reels?reiter=fertig", headers=H).text
    assert "Entwurf bei Sales, wartet auf deine Freigabe" in s
    assert "von Sales abgelehnt" in s and "Kein Kontakt in sales-claw" in s
    assert an.von("geteilt") == [("alpha", "reel")]


def test_stand_nicht_lesbar_seite_bleibt(c, an):
    an.stand_fehler = True
    r = c.get("/oberflaeche/reels?reiter=fertig", headers=H)
    assert r.status_code == 200 and "Wie es mit dem Teilen steht, lässt sich gerade nicht abrufen." in r.text


def test_ohne_fertige_karte_kein_stand_abruf(c, an):
    c.get("/oberflaeche/reels?reiter=arbeit", headers=H)
    assert an.von("geteilt") == []


def test_schalter_aus_formular_gesperrt(c, a):
    s = c.get("/oberflaeche/reels?reiter=fertig", headers=H).text
    teil = s[s.index('class="aufklapp bestellen teilen"'):]
    assert "<fieldset disabled>" in teil[:2000]
    assert "Bestellen ist ausgeschaltet (Kosten-Schalter)." in teil[:2000]


def test_podcast_zeigt_teilen_an_fertiger_folge(c, an):
    s = c.get("/oberflaeche/podcast", headers=H).text
    assert f'action="/oberflaeche/podcast/{F_FERTIG}/teilen"' in s
    assert f"/podcast/{F_OFFEN}/teilen" not in s


def test_englisch(c, an):
    s = c.get("/oberflaeche/reels?reiter=fertig", headers={**H, "Accept-Language": "en"}).text
    assert "Create sending request" in s and "draft at Sales, waiting for your approval" in s


# --- POST -----------------------------------------------------------------------------------------

def test_teilen_legt_auftrag_an(c, an):
    r = _post(c, f"/oberflaeche/reels/{R_FERTIG}/teilen", BEITRAG)
    assert r.status_code == 303 and r.headers["location"] == "/oberflaeche/reels?reiter=fertig&geteilt=1"
    assert an.von("teilen") == [("alpha", "reel", R_FERTIG, "linkedin_post", "", "Frühlingskuchen",
                                 "Ab Montag gibt es neue Kuchen.")]
    s = c.get(r.headers["location"], headers=H).text
    assert "Versandauftrag angelegt." in s


def test_wiederholung_meldet_sich(c, an):
    an.antwort = {"ok": True, "id": VA, "wiederholung": True, "grund": "besteht"}
    r = _post(c, f"/oberflaeche/podcast/{F_FERTIG}/teilen", BEITRAG)
    assert r.status_code == 303 and r.headers["location"] == "/oberflaeche/podcast?geteilt=wiederholung"


def test_schalter_aus_422_ohne_aufruf(c, a):
    r = _post(c, f"/oberflaeche/reels/{R_FERTIG}/teilen", BEITRAG)
    assert r.status_code == 422 and "Kosten-Schalter" in r.text
    assert a.von("teilen") == []


@pytest.mark.parametrize("pfad", [f"/oberflaeche/reels/{R_OFFEN}/teilen", f"/oberflaeche/reels/{FREMD}/teilen",
                                  "/oberflaeche/reels/kein-uuid/teilen", f"/oberflaeche/podcast/{F_OFFEN}/teilen"])
def test_nur_fertige_der_firma(c, an, pfad):
    r = _post(c, pfad, BEITRAG)
    assert r.status_code == 422 and an.von("teilen") == []


@pytest.mark.parametrize("werte", [{**BEITRAG, "kanal": "fax"}, {**BEITRAG, "kanal": ""},
                                   {**BEITRAG, "nachricht": "  "},
                                   {**BEITRAG, "nachricht": "x" * (teilen.NACHRICHT_MAX + 1)},
                                   {**BEITRAG, "betreff": "x" * (teilen.BETREFF_MAX + 1)}])
def test_eingaben_geprueft(c, an, werte):
    r = _post(c, f"/oberflaeche/reels/{R_FERTIG}/teilen", werte)
    assert r.status_code == 422 and an.von("teilen") == []


def test_absage_zeigt_grund_und_behaelt_text(c, an):
    an.antwort = {"ok": False, "grund": "linkedin_post braucht ein Thema."}
    r = _post(c, f"/oberflaeche/reels/{R_FERTIG}/teilen", {**BEITRAG, "betreff": ""})
    assert r.status_code == 422
    assert "linkedin_post braucht ein Thema." in r.text and "Ab Montag gibt es neue Kuchen." in r.text


def test_ablehnung_der_pult_schicht_behaelt_text(c, an):
    an.teilen_fehler = daten.DatenFehler("abgelehnt", "Nur Fertiges laesst sich teilen")
    r = _post(c, f"/oberflaeche/reels/{R_FERTIG}/teilen", BEITRAG)
    assert r.status_code == 422 and "Ab Montag gibt es neue Kuchen." in r.text


def test_ohne_schluessel_und_ohne_csrf(c, an):
    assert c.post(f"/oberflaeche/reels/{R_FERTIG}/teilen", data=BEITRAG,
                  follow_redirects=False).status_code == 401
    r = c.post(f"/oberflaeche/reels/{R_FERTIG}/teilen", headers=H, data=BEITRAG, follow_redirects=False)
    assert r.status_code == 403 and an.von("teilen") == []


# --- Pult-Schicht (api/teilen.py) gegen gefaelschte SQL-Wege ---------------------------------------

class Sql:
    def __init__(self):
        self.geschrieben: list[str] = []
        self.gelesen: list[str] = []
        self.zeile = {"mandant": "alpha", "status": "fertig", "datei": "vid-0123abcd-fruehling.mp4"}
        self.ergebnis = {"ok": True, "id": VA, "wiederholung": False, "grund": "Auftrag liegt bereit."}
        self.liste = [{"id": VA, "kanal": "email", "status": "offen", "grund": "", "draft_id": None,
                       "quelle": f"marketing-oberflaeche:alpha:reel:{R_FERTIG}", "angelegt": "x", "erledigt": None}]


@pytest.fixture
def sql(monkeypatch):
    monkeypatch.setenv("MARKETING_PULT_KEY", "pult-test")
    f = Sql()

    def einer(bauen):
        f.gelesen.append(bauen())
        return f.zeile

    def schreiben(bauen):
        f.geschrieben.append(bauen())
        return {"ergebnis": f.ergebnis}

    def lesen(bauen):
        f.gelesen.append(bauen())
        return f.liste
    monkeypatch.setattr(api_teilen, "_lesen_einer", einer)
    monkeypatch.setattr(api_teilen, "_schreiben", schreiben)
    monkeypatch.setattr(api_teilen, "_lesen", lesen)
    return f


def _pl(**mehr):
    return {"mandant": "alpha", "art": "reel", "id": R_FERTIG, "kanal": "youtube_post", "betreff": "Kuchen",
            "nachricht": "Neue Kuchen.", **mehr}


def test_pult_nimmt_datei_aus_der_db_und_firma_in_quelle(sql):
    r = api_teilen.pult_teilen(payload=_pl(medien_datei="anderes.mp4"), x_pult_key="pult-test")
    assert r == {"ok": True, "id": VA, "wiederholung": False, "grund": "Auftrag liegt bereit."}
    (befehl,) = sql.geschrieben
    assert "marketing.versandauftrag_anlegen('youtube_post', '', 'Neue Kuchen.', 'Kuchen', " \
           "'vid-0123abcd-fruehling.mp4', '', " in befehl
    assert f"'marketing-oberflaeche:alpha:reel:{R_FERTIG}'" in befehl
    assert "anderes.mp4" not in befehl
    assert "marketing.video_auftraege" in sql.gelesen[0]


def test_pult_folge_liest_ton_auftraege(sql):
    sql.zeile = {"mandant": "alpha", "status": "fertig", "datei": "pod-0123abcd-folge-1.mp3"}
    api_teilen.pult_teilen(payload=_pl(art="folge", id=F_FERTIG, kanal="email", empfaenger="muster@example.org"),
                           x_pult_key="pult-test")
    assert "marketing.ton_auftraege" in sql.gelesen[0]
    assert f":folge:{F_FERTIG}'" in sql.geschrieben[0]


@pytest.mark.parametrize("zeile,code", [(None, 404), ({"mandant": "beta", "status": "fertig", "datei": "x.mp4"}, 404),
                                        ({"mandant": "alpha", "status": "offen", "datei": None}, 422),
                                        ({"mandant": "alpha", "status": "fertig", "datei": ""}, 422)])
def test_pult_nur_fertige_der_firma(sql, zeile, code):
    sql.zeile = zeile
    with pytest.raises(HTTPException) as f:
        api_teilen.pult_teilen(payload=_pl(), x_pult_key="pult-test")
    assert f.value.status_code == code and sql.geschrieben == []


@pytest.mark.parametrize("mehr", [{"kanal": "fax"}, {"art": "bild"}, {"mandant": "Alpha!"}, {"mandant": None},
                                  {"nachricht": ""}, {"nachricht": 5}, {"betreff": "x" * 301}, {"id": "kein-uuid"}])
def test_pult_eingaben(sql, mehr):
    with pytest.raises(HTTPException) as f:
        api_teilen.pult_teilen(payload=_pl(**mehr), x_pult_key="pult-test")
    assert f.value.status_code in (404, 422) and sql.geschrieben == []


def test_pult_ohne_schluessel(sql):
    with pytest.raises(HTTPException) as f:
        api_teilen.pult_teilen(payload=_pl(), x_pult_key="falsch")
    assert f.value.status_code == 401 and sql.geschrieben == [] and sql.gelesen == []


def test_pult_absage_ist_ok_false(sql):
    sql.ergebnis = '{"ok": false, "grund": "Die Nachricht ist leer."}'
    r = api_teilen.pult_teilen(payload=_pl(), x_pult_key="pult-test")
    assert r["ok"] is False and r["grund"] == "Die Nachricht ist leer."


def test_pult_stand_nur_diese_firma_ohne_text(sql):
    r = api_teilen.pult_stand(mandant="alpha", art="reel", x_pult_key="pult-test")
    (befehl,) = sql.gelesen
    assert "left(quelle, 33) = 'marketing-oberflaeche:alpha:reel:'" in befehl
    assert "nachricht" not in befehl and "empfaenger" not in befehl
    assert r["auftraege"][0]["auftrag"] == R_FERTIG and "quelle" not in r["auftraege"][0]
