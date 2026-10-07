"""Marken-Arbeiter am PC (Plan 2026-10-07 marke-per-chat, Task 5): Chat -> Vorschlag, Uebernehmen ->
Rowboat + Spiegel, Abgleich. Rowboat nur unter tmp_path, API und Claude gefaelscht."""
import datetime
import io
import json

import pytest
from PIL import Image

from spaces.marketing.claw import markenprofil as mp
from spaces.marketing.claw.webseite import Fund, Seite
from spaces.marketing.workers import chat_worker as cw
from spaces.marketing.workers import marken_arbeiter as ma


@pytest.fixture(autouse=True)
def wurzel(tmp_path, monkeypatch):
    w = tmp_path / "companys"
    w.mkdir()
    monkeypatch.setenv("ROWBOAT_WISSEN_ORDNER", str(w))
    return w


def _png(farbe="#b45309", groesse=(40, 30)):
    puffer = io.BytesIO()
    Image.new("RGB", groesse, farbe).save(puffer, "PNG")
    return puffer.getvalue()


LOGO = _png()
JETZT = datetime.datetime(2026, 10, 7, 12, 30)

VORSCHLAG = {"akzent": "#b45309", "zweitfarbe": "#3b2f2f", "grund": "#faf7f2", "text": "#2b2724",
             "schrift_anzeige": "playfair", "schrift_text": "manrope", "logo": None,
             "abschnitte": {"Ton": "Ruhig, per Du.", "Bildstil": "Warme Werkstattfotos, Tageslicht."},
             "mustertext": {"betreff": "Neu im Herbst", "ueberschrift": "Frisch aus der Werkstatt",
                            "absatz": "Drei neue Räder warten auf dich."}}


def _antwort(vorschlag=VORSCHLAG, antwort="Hier mein Vorschlag."):
    return json.dumps({"antwort": antwort, "vorschlag": vorschlag}, ensure_ascii=False)


class Api:
    def __init__(self, auftrag=None, medien=None, spiegel=None, firmen=None, logo_name="marke-radhaus-logo-0123456789.png"):
        self.auftrag, self.log = auftrag, []
        self.medien = medien or {}
        self._spiegel = spiegel or {"ok": True, "fassung": 2}
        self._firmen = firmen or []
        self.logo_name = logo_name

    def naechster(self):
        a, self.auftrag = self.auftrag, None
        return a

    def weiter(self, aid):
        self.log.append(("weiter", aid)); return True

    def medium(self, aid, name):
        self.log.append(("medium", aid, name)); return self.medien.get(name)

    def logo(self, aid, roh, typ):
        self.log.append(("logo", aid, roh, typ)); return self.logo_name

    def vorschlag(self, aid, daten):
        self.log.append(("vorschlag", aid, daten)); return {"vorschlag": "v1"}

    def fertig(self, aid, daten):
        self.log.append(("fertig", aid, daten)); return {"markiert": 2}

    def zurueck(self, aid, antwort):
        self.log.append(("zurueck", aid, antwort)); return "fehler"

    def spiegeln(self, mandant, gestalt, stand):
        self.log.append(("spiegeln", mandant, gestalt, stand)); return self._spiegel

    def firmen(self):
        return self._firmen

    def aufrufe(self, name):
        return [e for e in self.log if e[0] == name]


class Fragen:
    def __init__(self, *antworten):
        self.antworten, self.gesehen = list(antworten), []

    def __call__(self, system, nachrichten):
        self.gesehen.append((system, [dict(n) for n in nachrichten]))
        a = self.antworten.pop(0)
        if isinstance(a, Exception):
            raise a
        return iter([a[: len(a) // 2], a[len(a) // 2:]])   # wie ein Strom in Stuecken


def _text(fragen, i=0):
    inhalt = fragen.gesehen[i][1][0]["content"]
    return inhalt[0]["text"] if isinstance(inhalt, list) else inhalt


def _lauf(api, fragen, **kw):
    kw.setdefault("webseite_lesen", lambda url: Fund())
    kw.setdefault("logo_laden", lambda url: None)
    return ma.ein_durchlauf(api, fragen, jetzt=lambda: JETZT, schlafen=lambda s: None, **kw)


CHAT = {"id": "a1", "art": "chat", "mandant": "radhaus", "firma": "Radhaus", "nachricht": "Hallo",
        "kontext": {}, "verlauf": [], "vorschlag": None}


# --- chat ---------------------------------------------------------------------------

def test_leer():
    assert _lauf(Api(None), Fragen()) == "leer"


def test_chat_mit_webseite_und_uploads(wurzel):
    gelesen = []
    fund = Fund(seiten=[Seite(url="https://radhaus.example/", text="Wir reparieren Räder seit 1990.",
                              ueberschriften=["Werkstatt"])],
                farben=["#b45309"], schriften=["Playfair Display"],
                logos=["https://radhaus.example/logo.png"], hinweise=["Webseite https://radhaus.example/x nicht lesbar: 404"])
    geladen = []
    auftrag = {**CHAT, "nachricht": "Unsere Seite: https://radhaus.example/ – mach was Ruhiges.",
               "kontext": {"anhaenge": [{"name": "foto.png", "art": "bild"},
                                        {"name": "preise.txt", "art": "dokument"}]}}
    api = Api(auftrag, medien={"foto.png": LOGO, "preise.txt": "Inspektion 49 Euro".encode()})
    fragen = Fragen(_antwort({**VORSCHLAG, "logo": "web:1"}))
    erg = _lauf(api, fragen, webseite_lesen=lambda url: gelesen.append(url) or fund,
                logo_laden=lambda url: geladen.append(url) or (LOGO, "image/png"))
    assert erg == "fertig"
    assert gelesen == ["https://radhaus.example/"]
    system, nachrichten = fragen.gesehen[0]
    assert system == ma.marken_prompt.SYSTEM
    teile = nachrichten[0]["content"]
    assert isinstance(teile, list) and teile[1]["type"] == "image_url"      # Upload als Bildteil
    t = teile[0]["text"]
    assert "Wir reparieren Räder seit 1990." in t and "web:1 = https://radhaus.example/logo.png" in t
    assert "Inspektion 49 Euro" in t and "Bild 1 = anhang:foto.png" in t
    assert "Noch kein Branding" in t
    assert geladen == ["https://radhaus.example/logo.png"]
    (_, aid, roh, typ), = api.aufrufe("logo")
    assert aid == "a1" and roh == LOGO and typ == "image/png"
    (_, _, daten), = api.aufrufe("vorschlag")
    assert daten["vorschlag"]["logo"] == "marke-radhaus-logo-0123456789.png"    # R8: Name vom Server
    assert daten["vorschlag"]["akzent"] == "#b45309" and daten["antwort"] == "Hier mein Vorschlag."
    assert "Webseite https://radhaus.example/x nicht lesbar: 404" in daten["hinweise"]
    assert api.aufrufe("zurueck") == [] and not list(wurzel.iterdir())        # chat schreibt nie in Rowboat


def test_chat_ohne_adresse_liest_keine_webseite_und_anhang_logo_bleibt_medienname():
    auftrag = {**CHAT, "kontext": {"anhaenge": [{"name": "logo.png", "art": "bild"}]}}
    api = Api(auftrag, medien={"logo.png": LOGO})
    erg = _lauf(api, Fragen(_antwort({**VORSCHLAG, "logo": "anhang:logo.png"})),
                webseite_lesen=lambda url: pytest.fail("keine Adresse"))
    assert erg == "fertig"
    assert api.aufrufe("vorschlag")[0][2]["vorschlag"]["logo"] == "anhang:logo.png"
    assert api.aufrufe("logo") == []


def test_chat_nur_http_und_https_adressen():
    gelesen = []
    api = Api({**CHAT, "nachricht": "ftp://x.example und dann http://radhaus.example/seite."})
    _lauf(api, Fragen(_antwort(None, "Danke")), webseite_lesen=lambda url: gelesen.append(url) or Fund())
    assert gelesen == ["http://radhaus.example/seite"]


def test_chat_rueckfrage_ohne_vorschlag_ist_fertig():
    api = Api(dict(CHAT))
    assert _lauf(api, Fragen(_antwort(None, "Wie heißt eure Webseite?"))) == "fertig"
    (_, aid, daten), = api.aufrufe("fertig")
    assert aid == "a1" and daten["antwort"] == "Wie heißt eure Webseite?"
    assert api.aufrufe("vorschlag") == []


def test_chat_bestehendes_profil_und_ungueltige_werte_als_hinweis(wurzel):
    (wurzel / "Radhaus").mkdir()
    (wurzel / "Radhaus" / "Marke.md").write_text("---\nakzent: #12\nschrift_text: manrope\n---\n## Ton\nLocker\n",
                                                 encoding="utf-8")
    api, fragen = Api(dict(CHAT)), Fragen(_antwort(None, "ok"))
    _lauf(api, fragen)
    t = _text(fragen)
    assert "Manrope" in t and "## Ton\nLocker" in t and "#12" not in t
    assert "Marke.md: akzent ungültig" in api.aufrufe("fertig")[0][2]["hinweise"]


def test_review_focus_4_hellgrau_auf_weiss_bekommt_korrekturversuch():
    api = Api(dict(CHAT))
    schlecht = _antwort({**VORSCHLAG, "grund": "#ffffff", "text": "#cccccc"})
    fragen = Fragen(schlecht, _antwort())
    assert _lauf(api, fragen) == "fertig"
    zweite = fragen.gesehen[1][1]
    assert [n["role"] for n in zweite] == ["user", "assistant", "user"]
    assert "Kontrast" in zweite[2]["content"]
    assert api.aufrufe("vorschlag")[0][2]["vorschlag"]["text"] == "#2b2724"


def test_zweimal_ungueltig_wird_nie_vorgeschlagen():
    api = Api(dict(CHAT))
    schlecht = _antwort({**VORSCHLAG, "grund": "#ffffff", "text": "#cccccc"})
    assert _lauf(api, Fragen(schlecht, schlecht)) == "fehler"
    (_, _, text), = api.aufrufe("zurueck")
    assert text.startswith("Das habe ich nicht umsetzen können: ") and "Kontrast" in text
    assert api.aufrufe("vorschlag") == [] and api.aufrufe("fertig") == []


def test_web_logo_nicht_ladbar_wird_hinweis():
    fund = Fund(logos=["https://radhaus.example/logo.png"])
    api = Api({**CHAT, "nachricht": "https://radhaus.example"})
    _lauf(api, Fragen(_antwort({**VORSCHLAG, "logo": "web:1"})), webseite_lesen=lambda url: fund,
          logo_laden=lambda url: None)
    (_, _, daten), = api.aufrufe("vorschlag")
    assert daten["vorschlag"]["logo"] is None
    assert any("Logo" in h for h in daten["hinweise"])


def test_shim_aus_gibt_auftrag_zurueck(monkeypatch):
    uhr = iter(range(0, 10_000, 100))
    api = Api(dict(CHAT))
    fragen = Fragen(*[cw.LlmFehler("weg")] * 50)
    assert ma.ein_durchlauf(api, fragen, webseite_lesen=lambda u: Fund(), logo_laden=lambda u: None,
                            uhr=lambda: next(uhr), schlafen=lambda s: None) == "fehler"
    assert api.aufrufe("zurueck")[0][2] == cw.NICHT_ERREICHBAR


# --- uebernehmen --------------------------------------------------------------------

def _uebernehmen(vorschlag=VORSCHLAG, von="Anna"):
    return {"id": "u1", "art": "uebernehmen", "mandant": "radhaus", "firma": "Radhaus", "nachricht": "",
            "kontext": {}, "verlauf": [], "von": von, "vorschlag": {"id": "v1", "vorschlag": vorschlag}}


def test_uebernehmen_schreibt_rowboat_spiegelt_und_meldet_fertig(wurzel):
    (wurzel / "Radhaus").mkdir()
    (wurzel / "Radhaus" / "Marke.md").write_text("## Wer wir sind\nFahrradwerkstatt in Köln.\n\n## Ton\nLaut\n",
                                                 encoding="utf-8")
    api = Api(_uebernehmen({**VORSCHLAG, "logo": "anhang:logo.png"}), medien={"logo.png": LOGO})
    assert _lauf(api, Fragen()) == "fertig"
    profil = mp.lesen(str(wurzel), "radhaus", "Radhaus")
    assert profil.werte["akzent"] == "#b45309" and profil.werte["schrift_anzeige"] == "playfair"
    assert profil.werte["stand"] == "2026-10-07 12:30 von Anna"
    assert profil.abschnitte["Ton"] == "Ruhig, per Du."
    assert profil.abschnitte["Wer wir sind"] == "Fahrradwerkstatt in Köln."            # nicht im Vorschlag: bleibt
    assert (wurzel / "Radhaus" / "logo.png").read_bytes() == LOGO
    assert len(list((wurzel / "Radhaus" / "Marke-Verlauf").iterdir())) == 1
    (_, mandant, gestalt, stand), = api.aufrufe("spiegeln")
    assert mandant == "radhaus" and stand == "2026-10-07 12:30 von Anna"
    assert gestalt["akzent"] == "#b45309" and gestalt["flaeche"] == "#3b2f2f"
    assert gestalt["schriften"] == {"anzeige": "playfair", "text": "manrope"}
    assert gestalt["logo"].startswith("data:image/")
    (_, aid, daten), = api.aufrufe("fertig")                     # fertig markiert die Entwuerfe (DB)
    assert aid == "u1" and daten["hinweise"] == []
    assert api.aufrufe("zurueck") == []
    assert [e[0] for e in api.log if e[0] in ("spiegeln", "fertig")] == ["spiegeln", "fertig"]


def test_uebernehmen_ohne_ordner_legt_ihn_an(wurzel):
    api = Api(_uebernehmen())
    assert _lauf(api, Fragen()) == "fertig"
    assert (wurzel / "Radhaus" / "Marke.md").is_file()
    assert "logo" not in api.aufrufe("spiegeln")[0][2]


def test_uebernehmen_fehlendes_logo_schreibt_nichts(wurzel):
    api = Api(_uebernehmen({**VORSCHLAG, "logo": "anhang:weg.png"}))
    assert _lauf(api, Fragen()) == "fehler"
    assert not (wurzel / "Radhaus").exists()
    (_, aid, grund), = api.aufrufe("zurueck")
    assert aid == "u1" and "weg.png" in grund
    assert api.aufrufe("spiegeln") == [] and api.aufrufe("fertig") == []


def test_uebernehmen_kaputtes_logo_schreibt_nichts(wurzel):
    (wurzel / "Radhaus").mkdir()
    (wurzel / "Radhaus" / "Marke.md").write_text("## Ton\nalt\n", encoding="utf-8")
    api = Api(_uebernehmen({**VORSCHLAG, "logo": "anhang:logo.png"}),
              medien={"logo.png": b"\x89PNG\r\n\x1a\nkaputt"})
    assert _lauf(api, Fragen()) == "fehler"
    assert (wurzel / "Radhaus" / "Marke.md").read_text(encoding="utf-8") == "## Ton\nalt\n"
    assert sorted(p.name for p in (wurzel / "Radhaus").iterdir()) == ["Marke.md"]
    assert "Logo" in api.aufrufe("zurueck")[0][2]


def test_uebernehmen_ungueltiger_vorschlag_wird_nicht_geschrieben(wurzel):
    api = Api(_uebernehmen({**VORSCHLAG, "grund": "#ffffff", "text": "#cccccc"}))
    assert _lauf(api, Fragen()) == "fehler"
    assert not (wurzel / "Radhaus").exists()
    assert "Kontrast" in api.aufrufe("zurueck")[0][2]


def test_uebernehmen_ohne_neues_logo_behaelt_das_alte(wurzel):
    mp.schreiben(str(wurzel), "radhaus", "Radhaus", {"akzent": "#000000"}, {"Ton": "alt"},
                 (LOGO, "image/png"), "Bob", JETZT - datetime.timedelta(days=1))
    api = Api(_uebernehmen({**VORSCHLAG, "logo": None}))
    assert _lauf(api, Fragen()) == "fertig"
    profil = mp.lesen(str(wurzel), "radhaus", "Radhaus")
    assert profil.werte["logo"] == "logo.png" and profil.logo_pfad
    assert api.aufrufe("spiegeln")[0][2]["logo"].startswith("data:image/")


def test_uebernehmen_spiegel_abgelehnt_bleibt_fertig_mit_hinweis(wurzel):
    api = Api(_uebernehmen(), spiegel={"ok": False, "fehler": "Layout ungueltig: x"})
    assert _lauf(api, Fragen()) == "fertig"
    assert (wurzel / "Radhaus" / "Marke.md").is_file()                 # Rowboat bleibt Wahrheit
    assert any("Layout ungueltig: x" in h for h in api.aufrufe("fertig")[0][2]["hinweise"])


def test_uebernehmen_ohne_von_nennt_den_marken_chat(wurzel):
    api = Api(_uebernehmen(von=None))
    assert _lauf(api, Fragen()) == "fertig"
    assert mp.lesen(str(wurzel), "radhaus", "Radhaus").werte["stand"] == "2026-10-07 12:30 von Marken-Chat"


def test_unbekannte_art_wird_zurueckgegeben():
    api = Api({**CHAT, "art": "raten"})
    assert _lauf(api, Fragen()) == "fehler"
    assert api.aufrufe("zurueck")


# --- Abgleich -----------------------------------------------------------------------

def _spiegel(**gestalt):
    return {"id": "radhaus", "name": "Radhaus", "stand": "alt", "gestalt": gestalt}


def test_abgleich_spiegelt_handaenderung(wurzel):
    (wurzel / "Radhaus").mkdir()
    (wurzel / "Radhaus" / "Marke.md").write_text(
        "---\nakzent: #b45309\nzweitfarbe: #3b2f2f\nschrift_anzeige: playfair\nschrift_text: manrope\n"
        "stand: 2026-10-07 12:00 von Hand\n---\n", encoding="utf-8")
    api = Api()
    meldungen = ma.abgleichen(api, str(wurzel), [_spiegel(akzent="#000000", flaeche="#3b2f2f")])
    (_, mandant, gestalt, stand), = api.aufrufe("spiegeln")
    assert mandant == "radhaus" and stand == "2026-10-07 12:00 von Hand"
    assert gestalt == {"akzent": "#b45309", "flaeche": "#3b2f2f",
                       "schriften": {"anzeige": "playfair", "text": "manrope"}}
    assert any("Radhaus" in m for m in meldungen)


def test_abgleich_gleich_spiegelt_nicht(wurzel):
    (wurzel / "Radhaus").mkdir()
    (wurzel / "Radhaus" / "Marke.md").write_text("---\nakzent: #b45309\n---\n", encoding="utf-8")
    api = Api()
    ma.abgleichen(api, str(wurzel), [_spiegel(akzent="#b45309", flaeche="#123456")])
    assert api.aufrufe("spiegeln") == []


def test_review_focus_2_kaputte_werte_werden_nie_gespiegelt(wurzel):
    (wurzel / "Radhaus").mkdir()
    (wurzel / "Radhaus" / "Marke.md").write_text("---\nakzent: #12\nzweitfarbe: #3b2f2f\nschrift_anzeige: comic\n---\n",
                                                 encoding="utf-8")
    api = Api()
    ma.abgleichen(api, str(wurzel), [_spiegel(akzent="#b45309", flaeche="#000000")])
    (_, _, gestalt, _), = api.aufrufe("spiegeln")
    assert gestalt == {"flaeche": "#3b2f2f"}                    # akzent bleibt beim letzten gueltigen Stand

    (wurzel / "Radhaus" / "Marke.md").write_text("---\nakzent: #12\n---\n## Ton\nx\n", encoding="utf-8")
    api = Api()
    ma.abgleichen(api, str(wurzel), [_spiegel(akzent="#b45309")])
    assert api.aufrufe("spiegeln") == []


def test_abgleich_ohne_kopfteil_oder_ordner_spiegelt_nicht(wurzel):
    (wurzel / "Radhaus").mkdir()
    (wurzel / "Radhaus" / "Marke.md").write_text("# Radhaus\n## Ton\nlocker\n", encoding="utf-8")
    api = Api()
    ma.abgleichen(api, str(wurzel), [_spiegel(), {"id": "fin", "name": "fin2gether", "stand": "", "gestalt": {}}])
    assert api.aufrufe("spiegeln") == []


def test_abgleich_spiegel_abgelehnt_wird_meldung_und_faellt_nicht_um(wurzel):
    (wurzel / "Radhaus").mkdir()
    (wurzel / "Radhaus" / "Marke.md").write_text("---\nakzent: #b45309\n---\n", encoding="utf-8")
    api = Api(spiegel={"ok": False, "fehler": "Layout ungueltig: akzent"})
    meldungen = ma.abgleichen(api, str(wurzel), [_spiegel()])
    assert any("Layout ungueltig: akzent" in m for m in meldungen)


def test_abgleich_takt_beim_start_und_alle_zehn_minuten(wurzel):
    gerufen = []
    zeit = [0.0]
    takt = ma.Abgleich(uhr=lambda: zeit[0], abgleichen=lambda api, w, f: gerufen.append(f) or [])
    api = Api(firmen=[_spiegel()])
    takt.schritt(api)
    zeit[0] = 599.0
    takt.schritt(api)
    zeit[0] = 600.0
    takt.schritt(api)
    assert len(gerufen) == 2 and gerufen[0] == [_spiegel()]


def test_abgleich_takt_ueberlebt_api_ausfall():
    class Weg(Api):
        def firmen(self):
            raise OSError("netz weg")
    takt = ma.Abgleich(uhr=lambda: 0.0)
    assert "netz weg" in takt.schritt(Weg())[0]


# --- API-Anbindung ------------------------------------------------------------------

class _Antwort:
    def __init__(self, roh):
        self.roh = roh

    def read(self):
        return self.roh

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_markenapi_routen(monkeypatch):
    gesehen = []

    def fake(req, timeout=None, context=None):
        gesehen.append((req.full_url, req.get_method(), req.data, req.get_header("Content-type")))
        return _Antwort(b'{"auftrag": null, "name": "marke-x.png", "ok": true, "firmen": [{"id": "r"}],'
                        b' "vorschlag": "v1", "markiert": 0}')
    monkeypatch.setattr(cw.urllib.request, "urlopen", fake)
    api = ma.MarkenApi("https://vm/", "K")
    assert api.naechster() is None and gesehen[-1][0] == "https://vm/api/marke/arbeiter/naechster"
    assert api.logo("a1", LOGO, "image/png") == "marke-x.png"
    assert gesehen[-1][0].endswith("/api/marke/arbeiter/a1/logo") and gesehen[-1][2] == LOGO
    assert gesehen[-1][3] == "image/png"
    assert api.spiegeln("r", {"akzent": "#000000"}, "s") == {"auftrag": None, "name": "marke-x.png", "ok": True,
                                                             "firmen": [{"id": "r"}], "vorschlag": "v1",
                                                             "markiert": 0}
    assert gesehen[-1][0] == "https://vm/api/marke/arbeiter/spiegeln"
    assert json.loads(gesehen[-1][2]) == {"mandant": "r", "gestalt": {"akzent": "#000000"}, "stand": "s"}
    assert api.firmen() == [{"id": "r"}] and gesehen[-1][:2] == ("https://vm/api/marke/arbeiter/firmen", "GET")
    api.vorschlag("a1", {"vorschlag": {}, "antwort": "x", "hinweise": []})
    assert gesehen[-1][0].endswith("/a1/vorschlag")
    # der Chat-Arbeiter bleibt auf seinen Routen
    cw.ChatApi("https://vm/", "K").naechster()
    assert gesehen[-1][0] == "https://vm/api/chat/arbeiter/naechster"


def test_schleifenschritt_schreibt_eigenes_feld(monkeypatch):
    monkeypatch.setattr(cw, "STAND", {"letzter_lauf": None, "letztes_ergebnis": None})
    cw.schleifenschritt(None, lambda api: "leer", feld="marke")
    assert cw.STAND["marke"] == "leer" and cw.STAND["letztes_ergebnis"] is None


def test_runde_chat_dann_marke_dann_abgleich(monkeypatch):
    monkeypatch.setattr(cw, "STAND", {"letzter_lauf": None, "letztes_ergebnis": None})
    reihenfolge = []

    class Takt:
        def schritt(self, api):
            reihenfolge.append(("abgleich", api)); return ["Radhaus: gespiegelt"]
    chat, marke = object(), object()
    cw.runde(chat, marke, Takt(),
             chat_ein=lambda api: reihenfolge.append(("chat", api)) or "leer",
             marke_ein=lambda api: reihenfolge.append(("marke", api)) or "fertig")
    assert reihenfolge == [("chat", chat), ("marke", marke), ("abgleich", marke)]
    assert cw.STAND["letztes_ergebnis"] == "leer" and cw.STAND["marke"] == "fertig"
    assert cw.STAND["abgleich"] == ["Radhaus: gespiegelt"]
