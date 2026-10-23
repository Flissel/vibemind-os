"""scripts/collage_vorlagen_erzeugen mit gefaelschter API, DB und Uhr."""
import pytest

from spaces.marketing.claw import seite_vorlagen
from spaces.marketing.scripts import collage_vorlagen_erzeugen as k

IID = "11111111-1111-1111-1111-111111111111"
SEITE = {"version": 1, "breite": 600, "hoehe": 900, "hintergrund": {"farbe": "#ffffff", "bild": None},
         "schriften": {"anzeige": "montserrat", "text": "dm-sans"}, "ebenen": [
             {"id": "titel", "art": "text", "x": 300, "y": 100, "breite": 400, "hoehe": 80, "text": "Überschrift",
              "rolle": "ANZEIGE", "groesse": 56, "farbe": "#1a1a1a"}]}
MARKE = {"akzent": "#c4573a", "zweitfarbe": "#222222", "grund": "#ffffff", "text": "#1a1a1a"}


class Fake:
    """Ersetzt Umgebung: eigene Uhr (warten rueckt sie vor), Skript fuer Chat-Status und Bildauftraege."""

    def __init__(self, chat=("fertig",), bilder_offen=0, loeschen_ok=True, aendert_fassung=False, vorhanden=(),
                 bilder_kaputt=0, auftraege=1, warteschlange=(0,), antwort="Nicht umgesetzt: kaputt"):
        self.vorhanden = set(vorhanden)
        self.auftraege = auftraege            # Bildauftraege der Probe insgesamt
        self.warteschlange = list(warteschlange)   # offene Bildauftraege aller Inhalte, je Abfrage
        self.antwort = antwort
        self.bilder_kaputt = bilder_kaputt
        self.t = 0.0
        self.aufrufe, self.sql = [], []
        self.chat = list(chat)
        self.bilder_offen = bilder_offen
        self.loeschen_ok = loeschen_ok
        self.aendert = aendert_fassung
        self.fassung = 1
        self.zeit = lambda: self.t

    def warten(self, s):
        self.t += s
        if self.aendert:
            self.fassung += 1

    def api(self, methode, pfad, koerper=None):
        self.aufrufe.append((methode, pfad, koerper))
        if pfad == "/api/pult/inhalte/collage":
            return {"inhalt": IID, "fassung": 0}
        if pfad.endswith("/loeschen"):
            if not self.loeschen_ok:
                raise k.LaufFehler("POST /loeschen: HTTP 409")
            return {}
        if methode == "GET":
            return {"fassungen": [{"seite": SEITE}]}
        return {}

    def abfrage(self, sql):
        self.sql.append(sql)
        if "FROM marketing.newsletter_vorlagen WHERE name IN" in sql:
            return [{"name": n} for n in sorted(self.vorhanden) if f"'{n}'" in sql]
        if "status = 'fehler'" in sql:
            return [{"n": self.bilder_kaputt}]
        if "FROM marketing.chat_auftraege" in sql:
            return [{"status": self.chat.pop(0) if len(self.chat) > 1 else self.chat[0], "antwort": self.antwort}]
        if "AS gesamt" in sql:
            return [{"gesamt": self.auftraege}]
        if "count(*)" in sql and "inhalt =" not in sql:
            return [{"n": self.warteschlange.pop(0) if len(self.warteschlange) > 1 else self.warteschlange[0]}]
        if "count(*)" in sql:
            return [{"n": self.bilder_offen}]
        if "max(fassung)" in sql:
            return [{"f": self.fassung}]
        if "FROM marketing.bild_auftraege" in sql:
            return [{"platz": "motiv1", "hinweis": "helles Atelier"}]
        return [{"fassung": 1}]

    def marke(self):
        return MARKE

    def pfade(self):
        return [a[1] for a in self.aufrufe]


@pytest.fixture(autouse=True)
def _ohne_db(monkeypatch):
    monkeypatch.setattr(k, "_lit", lambda w: "'" + str(w).replace("'", "''") + "'")


def _lauf(f, **kw):
    ausgabe = []
    code = k.lauf(f, ausgabe=ausgabe.append, **kw)
    return code, ausgabe


def test_acht_grundformen_mit_eindeutigen_slugs():
    assert len(k.GRUNDFORMEN) == 8 and len({g[0] for g in k.GRUNDFORMEN}) == 8
    for _, name, aufbau in k.GRUNDFORMEN:
        a = k.auftrag(name, aufbau)
        assert name in a and f"Vorlage: {name}" in a and "bild_erzeugen" in a and "Mehr erfahren" in a
        assert "nicht gestapelt" in a and len(a) <= 2000


GESTEN = ("bühne", "fläche", "farbband", "handschrift", "hand", "sticker", "rahmen", "schatten", "drehung",
          "anschnitt", "angeschnitten", "großschrift")
FARBEN = ("seite_hintergrund", "markenfarbe", "akzentfarbe")


def test_grundformen_haben_akzentgeste_und_farbe():
    assert set(k.GESTALTUNG) == {g[0] for g in k.GRUNDFORMEN}
    for slug, _, _ in k.GRUNDFORMEN:
        text = k.GESTALTUNG[slug]
        assert text.count("Geste:") == 1 and text.count("Farbe:") == 1, slug
        geste = text.split("Geste:", 1)[1].split("Farbe:", 1)[0].lower()
        farbe = text.split("Farbe:", 1)[1].lower()
        assert any(g in geste for g in GESTEN), slug
        assert any(f in farbe for f in FARBEN), slug
        assert "Dachzeile" in text, slug
        for zeichen in "°−×≈≥→":
            assert zeichen not in text, (slug, zeichen)


def test_auftrag_unter_2000_zeichen_fuer_alle():
    for slug, name, aufbau in k.GRUNDFORMEN:
        for a in (k.auftrag(name, aufbau), k.auftrag(name, k.GESTALTUNG[slug])):
            assert len(a) < 2000, name
            assert "„Dachzeile“" in a and "eine prägende Geste" in a and "Magazin-Niveau" in a
            assert "Farbe mit Absicht" in a and "logo_einsetzen" in a and "neutralen" in a
            assert "nicht gestapelt" in a and "Mehr erfahren" in a


def test_generator_schickt_die_ausfuehrliche_gestaltung(monkeypatch):
    gesendet = []
    monkeypatch.setattr(k, "_runde", lambda u, iid, text, ausgabe: gesendet.append(text))
    f = Fake()
    code, _ = _lauf(f, nur="split")
    assert code == 0 and gesendet and k.GESTALTUNG["split"] in gesendet[0]


def test_happy_path_eine_vorlage(monkeypatch):
    f = Fake()
    gesehen = {}
    echt = seite_vorlagen.rollen_ableiten
    monkeypatch.setattr(seite_vorlagen, "rollen_ableiten",
                        lambda s, m, b=None: gesehen.update(m=m, b=b) or echt(s, m, b))
    code, aus = _lauf(f, nur="split")
    assert code == 0 and any("collage-split" in z for z in aus)
    assert f.pfade() == ["/api/pult/inhalte/collage", f"/api/pult/inhalte/{IID}/chat", f"/api/pult/inhalte/{IID}",
                         f"/api/pult/inhalte/{IID}/loeschen"]
    chat = f.aufrufe[1][2]
    assert chat["kontext"] == {"fenster": "seite", "auswahl": []} and "Split" in chat["nachricht"]
    speichern = [s for s in f.sql if "pult_vorlage_seite_speichern" in s][0]
    assert "'collage-split'" in speichern and "'vorschlag', true" in speichern and "'collage-vorlagen'" in speichern
    assert gesehen["m"] == MARKE and gesehen["b"] == [{"platz": "motiv1", "hinweis": "helles Atelier"}]
    assert f.t >= k.STABIL      # Fassung musste 3 Minuten stabil sein


def test_trocken_ruft_nichts():
    f = Fake()
    code, aus = _lauf(f, trocken=True)
    assert code == 0 and len(aus) == 8 and not f.aufrufe and not f.sql


def test_nur_unbekannter_slug():
    f = Fake()
    code, aus = _lauf(f, nur="gibt-es-nicht")
    assert code == 2 and not f.aufrufe


def test_alle_acht_ohne_nur():
    f = Fake()
    code, _ = _lauf(f)
    assert code == 0 and sum(1 for p in f.pfade() if p == "/api/pult/inhalte/collage") == 8


def test_zeitlimit_der_runde_raeumt_auf_und_speichert_nicht():
    f = Fake(chat=("in_arbeit",))
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any("Zeitlimit der Chat-Runde" in z for z in aus)
    assert f.pfade()[-1].endswith("/loeschen") and not any("pult_vorlage_seite_speichern" in s for s in f.sql)
    assert f.t >= k.RUNDE_MAX


def test_zeitlimit_der_bilder():
    f = Fake(bilder_offen=1)
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any("Zeitlimit für die Bilder" in z for z in aus)
    assert f.t >= k.BILD_JE_AUFTRAG and f.pfade()[-1].endswith("/loeschen")


def test_fassung_aendert_sich_dauernd_laeuft_ins_limit():
    f = Fake(aendert_fassung=True)
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any("Zeitlimit für die Bilder" in z for z in aus)


def test_chat_fehler_raeumt_auf():
    f = Fake(chat=("in_arbeit", "fehler"))
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any("Status fehler" in z for z in aus) and f.pfade()[-1].endswith("/loeschen")
    # zweimal gefragt (ein Neuversuch), die Antwort des Arbeiters steht in der Meldung
    assert f.pfade().count(f"/api/pult/inhalte/{IID}/chat") == 2
    assert any("Nicht umgesetzt: kaputt" in z for z in aus)


def test_chat_fehler_einmal_dann_fertig_speichert():
    f = Fake(chat=("fehler", "fertig"))
    code, aus = _lauf(f, nur="split")
    assert code == 0 and any("collage-split als Vorschlag" in z for z in aus)
    assert f.pfade().count(f"/api/pult/inhalte/{IID}/chat") == 2
    assert any("Neuversuch" in z and "Nicht umgesetzt: kaputt" in z for z in aus)
    assert f.t >= k.NEUVERSUCH_PAUSE             # gewartet, bis eine neu gestartete Modell-Tuer wieder da ist


def test_chat_fehler_meldung_gekappt_und_einzeilig():
    f = Fake(chat=("fehler",), antwort="Zeile1\nZeile2 " + "x" * 500)
    _, aus = _lauf(f, nur="split")
    z = [z for z in aus if z.startswith("FEHLER split")][0]
    assert "\n" not in z and len(z) < 400


# --- Bilder: Zeitlimit je Auftrag, Warteschlange vor der naechsten Vorlage ---

def test_bilder_limit_waechst_mit_der_zahl_der_auftraege():
    f = Fake(bilder_offen=1, auftraege=3)
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any("Zeitlimit für die Bilder (36 Minuten, 3 Aufträge)" in z for z in aus)
    assert 3 * k.BILD_JE_AUFTRAG <= f.t < 3 * k.BILD_JE_AUFTRAG + k.STABIL + 120


def test_bilder_limit_hoechstens_eine_stunde():
    f = Fake(bilder_offen=1, auftraege=9)
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any("Zeitlimit für die Bilder (60 Minuten, 9 Aufträge)" in z for z in aus)
    assert k.BILDER_MAX <= f.t < k.BILDER_MAX + 120


def test_acht_minuten_bilder_reichen_jetzt():
    """Der Live-Fall: 2 Bilder je ~8 min hintereinander - frueher nach 10 min abgebrochen."""
    f = Fake(auftraege=2)
    offen = iter([1] * (16 * 6) + [0] * 1000)          # 16 Minuten offen (Takt 10 s), dann fertig
    echt = f.abfrage

    def abfrage(sql):
        if "status IN ('offen','in_arbeit')" in sql and "inhalt =" in sql:
            return [{"n": next(offen)}]
        return echt(sql)
    f.abfrage = abfrage
    code, aus = _lauf(f, nur="split")
    assert code == 0 and any("collage-split als Vorschlag" in z for z in aus)


def test_naechste_vorlage_wartet_auf_freie_bildwarteschlange():
    f = Fake(warteschlange=(2, 2, 1, 0))
    code, _ = _lauf(f, nur="split")
    assert code == 0
    warte = [s for s in f.sql if "count(*)" in s and "inhalt =" not in s]
    assert len(warte) == 4                       # bis die Warteschlange leer war
    i_warte = f.sql.index(warte[-1])
    assert f.aufrufe[0][1] == "/api/pult/inhalte/collage"   # die Probe entsteht erst danach
    assert f.t >= 3 * k.TAKT and i_warte < len(f.sql)


def test_warteschlange_bleibt_voll_vorlage_faellt_aus():
    f = Fake(warteschlange=(1,))
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any("Bildwarteschlange" in z for z in aus)
    assert not f.aufrufe                         # keine Probe angelegt
    assert f.t >= k.WARTESCHLANGE_MAX


# --- --behalten ---

def test_behalten_loescht_die_probe_nicht():
    f = Fake(chat=("fehler",))
    code, aus = _lauf(f, nur="split", behalten=True)
    assert code == 1 and not any(p.endswith("/loeschen") for p in f.pfade())
    assert any(IID in z and "behalten" in z for z in aus)


def test_main_reicht_behalten_durch(monkeypatch):
    monkeypatch.setenv("MARKETING_PULT_KEY", "x")
    gesehen = {}
    monkeypatch.setattr(k, "lauf", lambda u, nur, trocken, **kw: gesehen.update(nur=nur, **kw) or 0)
    assert k.main(["--nur", "split", "--behalten"]) == 0
    assert gesehen["behalten"] is True and gesehen["nur"] == "split"


def test_loeschen_scheitert_wird_am_ende_wiederholt_und_gemeldet():
    f = Fake(loeschen_ok=False)
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any(IID in z for z in aus)
    assert f.pfade().count(f"/api/pult/inhalte/{IID}/loeschen") == 1 + k.LOESCH_VERSUCHE


def test_fehler_zeigt_nur_den_typ():
    f = Fake()

    def kaputt(*a, **kw):
        raise ValueError("X-Pult-Key: geheim")
    f.abfrage = kaputt
    code, aus = _lauf(f, nur="split")
    assert code == 1 and all("geheim" not in z for z in aus) and any("ValueError" in z for z in aus)


# --- Gesamtpruefung Stufe 5 ---

def test_grundformen_aus_dem_prompt():
    from spaces.marketing.claw import seite_prompt
    assert k.GRUNDFORMEN is seite_prompt.GRUNDFORMEN
    for _, name, aufbau in k.GRUNDFORMEN:
        assert f"- {name}: {aufbau}" in seite_prompt.system_seite(True)


def test_bestehende_vorlage_wird_uebersprungen():
    f = Fake(vorhanden={"collage-split"})
    code, aus = _lauf(f)
    assert code == 0 and any("übersprungen: collage-split" in z and "--ersetzen" in z for z in aus)
    assert sum(1 for p in f.pfade() if p == "/api/pult/inhalte/collage") == 7
    assert not any("'collage-split'" in s and "pult_vorlage_seite_speichern" in s for s in f.sql)


def test_ersetzen_erzeugt_bestehende_neu():
    f = Fake(vorhanden={"collage-split"})
    code, aus = _lauf(f, nur="split", ersetzen=True)
    assert code == 0 and any("'collage-split'" in s and "pult_vorlage_seite_speichern" in s for s in f.sql)
    assert not any("WHERE name IN" in s for s in f.sql)


def test_bildauftrag_mit_fehler_vorlage_nicht_gespeichert():
    f = Fake(bilder_kaputt=1)
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any("Bildauftrag endete mit Fehler" in z for z in aus)
    assert not any("pult_vorlage_seite_speichern" in s for s in f.sql)
    assert f.pfade()[-1].endswith("/loeschen")


def test_abbruch_nennt_offene_proben_trotzdem():
    f = Fake(loeschen_ok=False)
    echt = f.warten

    def abbrechen(s):
        if s == 30:
            raise KeyboardInterrupt
        echt(s)
    f.warten = abbrechen
    ausgabe = []
    with pytest.raises(KeyboardInterrupt):
        k.lauf(f, nur="split", ausgabe=ausgabe.append)
    assert any(IID in z and "von Hand" in z for z in ausgabe)


def test_ohne_pult_schluessel_frueh_abbrechen(monkeypatch, capsys):
    monkeypatch.delenv("MARKETING_PULT_KEY", raising=False)
    monkeypatch.setattr(k, "lauf", lambda *a, **kw: pytest.fail("lauf darf nicht starten"))
    assert k.main(["--nur", "split"]) == 2
    assert "MARKETING_PULT_KEY fehlt" in capsys.readouterr().out
    monkeypatch.setenv("MARKETING_PULT_KEY", "   ")
    assert k.main([]) == 2


def test_trocken_braucht_keinen_schluessel(monkeypatch, capsys):
    monkeypatch.delenv("MARKETING_PULT_KEY", raising=False)
    assert k.main(["--trocken", "--nur", "split"]) == 0
    assert "[split]" in capsys.readouterr().out
