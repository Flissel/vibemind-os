"""Marketing-Oberflaeche, Stufe B Punkt 3: Werkbank (lange Videos Szene fuer Szene abnehmen). Lesen geht immer,
Abnahme nur mit dem Kosten-Schalter (MARKETING_BESTELLEN=an), Wunsch speichern immer. Ohne Datenbank: daten.* sind
Attrappen, die Datenwege und die neue Pult-Liste werden gegen Faelschungen geprueft. Alle Firmen, Videos und
Texte sind erfunden; ausgeloest oder versendet wird in keinem Test etwas."""
from __future__ import annotations

import copy

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from spaces.marketing.api import medienablage, oberflaeche
from spaces.marketing.api import werkbank as werkbank_api
from spaces.marketing.api.oberflaeche import bestellen, daten, huelle, werkbank

KEY = "test-ui-schluessel"
H = {"Authorization": f"Bearer {KEY}"}
C = {"cookie": "mk_firma=alpha"}
FIRMEN = [{"id": "alpha", "name": "Alpha GmbH", "aktiv": True}]
AID = "aaaaaaaa-1111-4111-8111-111111111111"
FREMD = "ffffffff-6666-4666-8666-666666666666"
KENNUNG = "wb-abcdefghij"
CLIP = "wb-aaaaaaaa-s01-f1.mp4"
VIDEO = "wb-aaaaaaaa-video-f2.mp4"
WERK = {
    "gliederung_stand": "text_wartet", "gliederung_wunsch": "",
    "gliederung": {"titel": "Warum Sauerteig", "hook": "Brot braucht Zeit.",
                   "stufen": [{"name": "Einstieg", "szenen": [{"kurz": "Der Teig ruht",
                                                                "quellen": [{"titel": "Backbuch"}]}]}],
                   "fehlt": ["Eine Zahl zur Ruhezeit"]},
    "quellen": [{"titel": "Backbuch", "stelle": "S. 12"}],
    "szenen": [{"nr": 1, "kurz": "Der Teig ruht", "stand": "clip_wartet", "clip_datei": CLIP},
               {"nr": 2, "kurz": "Das Brot im Ofen", "stand": "geplant", "aufgegeben": "Zweimal durchgefallen."},
               {"nr": 3, "kurz": "Anschnitt", "stand": "fertig", "fehler": "Clip zu kurz"}],
    "alle_fertig": False, "video_fehler": None}
SZENE = {"nr": 1, "kurz": "Der Teig ruht", "stufe": "Einstieg", "stand": "clip_wartet", "clip_datei": CLIP,
         "dauer_s": 42, "sprechtext": "Der Teig ruht zwölf Stunden.", "bildart": "slide",
         "bild": {"schritte": [{"vorlage": "zahl", "zahl": "12", "text": "Stunden Ruhe"},
                               {"vorlage": "titel", "titel": "Geduld"}]},
         "uebergang": "blende", "belege": [{"aussage": "12 Stunden Ruhe", "quelle": {"titel": "Backbuch"}}],
         "quellen": [{"titel": "Backbuch"}], "wuensche": [{"text": "etwas ruhiger sprechen"}], "wunsch": ""}


class Attrappe:
    def __init__(self):
        self.aufrufe: list[tuple] = []
        self.fehler: dict[str, Exception] = {}
        self.werk = copy.deepcopy(WERK)
        self.szene = copy.deepcopy(SZENE)
        self.kennung: str | None = KENNUNG
        self.ok = True
        self.liste = [{"id": AID, "stand": "werkbank", "thema": "Sauerteig erklärt", "angelegt": "2026-11-24T09:00:00",
                       "werkbank": {"wartet": 1}},
                      {"id": "bbbbbbbb-2222-4222-8222-222222222222", "stand": "erzeugt", "thema": "",
                       "titel": "Wochenmarkt", "angelegt": "2026-11-20T09:00:00", "datei": VIDEO}]

    def einsetzen(self, monkeypatch):
        def mach(name, holen):
            def f(*werte):
                self.aufrufe.append((name, *werte))
                if name in self.fehler:
                    raise self.fehler[name]
                return holen(*werte)
            monkeypatch.setattr(daten, name, f)
        mach("firmen", lambda: FIRMEN)
        mach("werke", lambda m: self.liste)
        mach("werk", lambda m, a: {"werk": self.werk, "gliederung_kennung": self.kennung})
        mach("werk_szene", lambda m, a, n: {"szene": self.szene, "kennung": self.kennung})
        for name in ("werk_wunsch", "werk_urteil", "werk_szene_nochmal", "werk_szene_oeffnen", "werk_neu_schreiben",
                     "werk_neu_bauen"):
            mach(name, lambda *w: self.ok)

    def von(self, name: str) -> list[tuple]:
        return [x[1:] for x in self.aufrufe if x[0] == name]

    def schreibend(self) -> set[str]:
        return {x[0] for x in self.aufrufe} - {"firmen", "werke", "werk", "werk_szene"}


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


def _get(c, pfad):
    return c.get(pfad, headers={**H, **C})


def _post(c, pfad, werte, csrf=True):
    daten_ = {**({"csrf": huelle.csrf_marke()} if csrf else {}), **werte}
    return c.post(pfad, headers={**H, **C}, data=daten_, follow_redirects=False)


# --- Lesen ---------------------------------------------------------------------------------------

def test_menue_und_liste(c, a):
    r = _get(c, "/oberflaeche/werkbank")
    assert r.status_code == 200 and "Lange Videos" in r.text
    assert f'href="/oberflaeche/werkbank/{AID}"' in r.text and "Sauerteig erklärt" in r.text
    assert "1 warten auf dich" in r.text and "Wochenmarkt" in r.text and "Video ist fertig" in r.text


def test_liste_leer(c, a):
    a.liste = []
    assert "Noch kein langes Video" in _get(c, "/oberflaeche/werkbank").text


def test_uebersicht(c, a):
    r = _get(c, f"/oberflaeche/werkbank/{AID}")
    assert r.status_code == 200
    t = r.text
    assert "3 Szenen, 1 fertig, 1 warten auf dich." in t and "Warum Sauerteig" in t and "Eine Zahl zur Ruhezeit" in t
    assert f'href="/oberflaeche/werkbank/{AID}/szene/1"' in t and "braucht deinen Wunsch" in t
    assert "Passt die Gliederung?" in t and f'value="{KENNUNG}"' in t
    assert "Backbuch · S. 12" in t


def test_uebersicht_fremd_oder_kaputt(c, a):
    a.fehler["werk"] = daten.DatenFehler("abgelehnt", "Unbekannter Auftrag")
    assert _get(c, f"/oberflaeche/werkbank/{FREMD}").status_code == 422
    assert _get(c, "/oberflaeche/werkbank/kein-auftrag").status_code == 422
    assert ("werk", "alpha", "kein-auftrag") not in a.aufrufe


def test_szene(c, a):
    r = _get(c, f"/oberflaeche/werkbank/{AID}/szene/1")
    t = r.text
    assert r.status_code == 200 and "Szene 1: Der Teig ruht" in t and "Clip wartet auf dich" in t
    assert 'src="/oberflaeche/medien/wb-aaaaaaaa-s01-f1.mp4?' in t and "Länge 0:42" in t
    assert "Der Teig ruht zwölf Stunden." in t and "12 Stunden Ruhe" in t and "Quelle: Backbuch" in t
    assert "Bild 1: <b>Zahl</b> – 12 · Stunden Ruhe" in t and "Blende" in t
    assert "etwas ruhiger sprechen" in t and "Passt der Clip?" in t


def test_szene_ohne_gueltigen_clipnamen_kein_spieler(c, a):
    a.szene["clip_datei"] = "../geheim.mp4"
    assert "<video" not in _get(c, f"/oberflaeche/werkbank/{AID}/szene/1").text


@pytest.mark.parametrize("nr", ["0", "1000", "x"])
def test_szene_falsche_nummer(c, a, nr):
    assert _get(c, f"/oberflaeche/werkbank/{AID}/szene/{nr}").status_code in (404, 422)
    assert "werk_szene" not in {x[0] for x in a.aufrufe}


def test_fertiges_video_mit_kopierfeldern(c, a):
    a.werk.update({"alle_fertig": True, "szenen": [{"nr": 1, "stand": "fertig"}]})
    a.liste[0].update({"datei": VIDEO, "dauer_s": 600, "video_titel": "Sauerteig in zehn Minuten",
                       "beschreibung": "Alles zur Ruhezeit.", "zeitmarken": [{"zeit": "0:00", "name": "Start"},
                                                                              {"zeit": "1:30", "name": "Ofen"}]})
    t = _get(c, f"/oberflaeche/werkbank/{AID}").text
    assert "Das fertige Video" in t and 'src="/oberflaeche/medien/wb-aaaaaaaa-video-f2.mp4?' in t
    assert "Sauerteig in zehn Minuten" in t and "0:00 Start\n1:30 Ofen" in t and "Länge 10:00" in t


def test_clip_signatur_spielt_aus_der_ablage(c, a, tmp_path, monkeypatch):
    (tmp_path / CLIP).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
    monkeypatch.setenv(medienablage.LAEDEN_ENV, str(tmp_path))
    monkeypatch.delenv(medienablage.ENV, raising=False)
    t = _get(c, f"/oberflaeche/werkbank/{AID}/szene/1").text
    adresse = t.split('src="', 1)[1].split('"', 1)[0].replace("&amp;", "&")
    assert c.get(adresse).status_code == 200


def test_medienablage_kennt_werkbank_namen():
    for gut in (CLIP, VIDEO, "wb-0123abcd-s123-f12.mp4"):
        assert medienablage.name_ok(gut)
    for schlecht in ("wb-0123abcd-s1-f1.mp4", "wb-0123abcd-video-f1.mov", "wb-xyz-s01-f1.mp4", "../" + CLIP):
        assert not medienablage.name_ok(schlecht)


# --- Kosten-Schalter -----------------------------------------------------------------------------

def test_aus_knoepfe_gesperrt_wunsch_speichern_frei(c, a):
    t = _get(c, f"/oberflaeche/werkbank/{AID}/szene/1").text
    assert "Abnehmen ist ausgeschaltet" in t
    assert 'value="passt" disabled' in t and 'value="nochmal" class="leise" disabled' in t
    assert f'formaction="/oberflaeche/werkbank/{AID}/wunsch" class="leise">' in t


@pytest.mark.parametrize("pfad, werte", [
    ("urteil", {"kennung": KENNUNG, "urteil": "passt"}), ("szene/1/nochmal", {}),
    ("szene/3/oeffnen", {"wunsch": "heller"}), ("neu-schreiben", {"wunsch": "kürzer", "nr": "2"}),
    ("neu-bauen", {})])
def test_aus_lehnt_jede_abnahme_ab(c, a, pfad, werte):
    r = _post(c, f"/oberflaeche/werkbank/{AID}/{pfad}", werte)
    assert r.status_code == 422 and "Kosten-Schalter" in r.text and not a.schreibend()


def test_aus_wunsch_speichern_geht(c, a):
    r = _post(c, f"/oberflaeche/werkbank/{AID}/wunsch", {"kennung": KENNUNG, "wunsch": "ruhiger", "nr": "1"})
    assert r.status_code == 303 and r.headers["location"] == f"/oberflaeche/werkbank/{AID}/szene/1?m=gespeichert"
    assert a.von("werk_wunsch") == [("alpha", KENNUNG, "ruhiger")]


# --- Abnahme mit Schalter an -------------------------------------------------------------------

def test_urteil_passt_und_nochmal(c, an):
    r = _post(c, f"/oberflaeche/werkbank/{AID}/urteil", {"kennung": KENNUNG, "urteil": "passt", "nr": "1"})
    assert r.status_code == 303 and r.headers["location"].endswith("/szene/1?m=passt")
    r = _post(c, f"/oberflaeche/werkbank/{AID}/urteil", {"kennung": KENNUNG, "urteil": "nochmal", "wunsch": "mehr Zahl"})
    assert r.headers["location"] == f"/oberflaeche/werkbank/{AID}?m=nochmal"
    assert an.von("werk_urteil") == [("alpha", KENNUNG, "passt", ""), ("alpha", KENNUNG, "nochmal", "mehr Zahl")]


@pytest.mark.parametrize("werte", [{"kennung": "wb-x", "urteil": "passt"}, {"kennung": KENNUNG, "urteil": "egal"},
                                   {"kennung": KENNUNG, "urteil": "passt", "wunsch": "x" * 2001},
                                   {"kennung": KENNUNG, "urteil": "passt", "nr": "0"}])
def test_urteil_falsche_eingaben(c, an, werte):
    assert _post(c, f"/oberflaeche/werkbank/{AID}/urteil", werte).status_code == 422 and not an.schreibend()


def test_schon_entschieden_ohne_und_mit_wunsch(c, an):
    an.fehler["werk_urteil"] = daten.DatenFehler("abgelehnt", "Der Vorschlag ist nicht mehr aktuell")
    r = _post(c, f"/oberflaeche/werkbank/{AID}/urteil", {"kennung": KENNUNG, "urteil": "passt", "nr": "1"})
    assert r.status_code == 303 and r.headers["location"].endswith("/szene/1?m=entschieden")
    r = _post(c, f"/oberflaeche/werkbank/{AID}/urteil", {"kennung": KENNUNG, "urteil": "nochmal", "wunsch": "Mein Text"})
    assert r.status_code == 409 and "Mein Text" in r.text and "Seite neu laden" in r.text


def test_ok_false_heisst_schon_entschieden(c, an):
    an.ok = False
    r = _post(c, f"/oberflaeche/werkbank/{AID}/szene/3/nochmal", {})
    assert r.status_code == 303 and r.headers["location"].endswith("/szene/3?m=entschieden")


def test_nicht_erreichbar_zeigt_fehlerseite(c, an):
    an.fehler["werk_neu_bauen"] = daten.DatenFehler("nicht_erreichbar")
    assert _post(c, f"/oberflaeche/werkbank/{AID}/neu-bauen", {}).status_code == 503


def test_oeffnen_braucht_wunsch(c, an):
    assert _post(c, f"/oberflaeche/werkbank/{AID}/szene/3/oeffnen", {"wunsch": " "}).status_code == 422
    r = _post(c, f"/oberflaeche/werkbank/{AID}/szene/3/oeffnen", {"wunsch": "heller"})
    assert r.status_code == 303 and an.von("werk_szene_oeffnen") == [("alpha", AID, 3, "heller")]


def test_neu_schreiben_szene_und_gliederung(c, an):
    _post(c, f"/oberflaeche/werkbank/{AID}/neu-schreiben", {"wunsch": "kürzer", "nr": "2"})
    _post(c, f"/oberflaeche/werkbank/{AID}/neu-schreiben", {"wunsch": "eine Szene weniger"})
    assert an.von("werk_neu_schreiben") == [("alpha", AID, 2, "kürzer"), ("alpha", AID, None, "eine Szene weniger")]


def test_ohne_schluessel_oder_marke(c, an):
    assert c.post(f"/oberflaeche/werkbank/{AID}/neu-bauen", headers=C, data={"csrf": huelle.csrf_marke()},
                  follow_redirects=False).status_code == 401
    assert _post(c, f"/oberflaeche/werkbank/{AID}/neu-bauen", {}, csrf=False).status_code == 403
    assert _post(c, "/oberflaeche/werkbank/kein-auftrag/neu-bauen", {}).status_code == 422
    assert not an.schreibend()


def test_knoepfe_je_stand(c, an):
    an.szene.update({"stand": "fertig", "fehler": "Clip zu kurz"})
    t = _get(c, f"/oberflaeche/werkbank/{AID}/szene/3").text
    assert "Nochmal versuchen" in t and "Szene wieder öffnen" in t and "Passt der Clip?" not in t
    an.szene.update({"stand": "geplant", "fehler": None, "aufgegeben": "Zweimal durchgefallen."})
    t = _get(c, f"/oberflaeche/werkbank/{AID}/szene/2").text
    assert "Zweimal durchgefallen." in t and "Nochmal schreiben" in t and 'name="nr" value="2"' in t


# --- Datenwege und Pult-Liste --------------------------------------------------------------------

def test_daten_wege_rufen_die_pult_routen(monkeypatch):
    gesehen = []

    def falsch(name):
        def f(**werte):
            gesehen.append((name, werte))
            return {"ok": True}
        return f
    for name in ("pult_wunsch", "pult_urteil", "pult_nochmal", "pult_oeffnen", "pult_szene_wunsch",
                 "pult_gliederung_wunsch", "pult_neu_bauen"):
        monkeypatch.setattr(werkbank_api, name, falsch(name))
    assert daten.werk_urteil("alpha", KENNUNG, "passt", "") is True
    daten.werk_neu_schreiben("alpha", AID, None, "w")
    daten.werk_neu_schreiben("alpha", AID, 2, "w")
    daten.werk_neu_bauen("alpha", AID)
    namen = [g[0] for g in gesehen]
    assert namen == ["pult_urteil", "pult_gliederung_wunsch", "pult_szene_wunsch", "pult_neu_bauen"]
    assert gesehen[0][1]["payload"] == {"mandant": "alpha", "von": "marketing-oberflaeche", "urteil": "passt",
                                        "wunsch": None}
    assert gesehen[2][1]["nr"] == 2


def test_daten_ok_false_und_409(monkeypatch):
    monkeypatch.setattr(werkbank_api, "pult_wunsch", lambda **w: {"ok": False})
    assert daten.werk_wunsch("alpha", KENNUNG, "w") is False

    def besetzt(**w):
        raise HTTPException(409, "Das Video kann gerade nicht neu zusammengefügt werden")
    monkeypatch.setattr(werkbank_api, "pult_neu_bauen", besetzt)
    with pytest.raises(daten.DatenFehler) as f:
        daten.werk_neu_bauen("alpha", AID)
    assert f.value.art == "abgelehnt"


def test_pult_liste_nur_die_firma_und_nur_lesend(monkeypatch):
    sql = []
    monkeypatch.setenv("MARKETING_PULT_KEY", "pult")
    monkeypatch.setattr(werkbank_api, "_lesen", lambda f: sql.append(f()) or [{"id": AID}, "kaputt"])
    monkeypatch.setattr(werkbank_api, "_schreiben", lambda f: pytest.fail("die Liste schreibt nie"))
    antwort = werkbank_api.pult_liste(mandant="alpha", limit=5, x_pult_key="pult")
    assert antwort == {"werke": [{"id": AID}]}
    assert "mandant = 'alpha'" in sql[0] and "an = 'marketing'" in sql[0] and "art = 'video'" in sql[0]
    assert sql[0].lstrip().upper().startswith("SELECT") and "LIMIT 5" in sql[0]
    with pytest.raises(HTTPException) as f:
        werkbank_api.pult_liste(mandant="alpha", limit=5, x_pult_key="falsch")
    assert f.value.status_code in (401, 403)
