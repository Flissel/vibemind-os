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

    def __init__(self, chat=("fertig",), bilder_offen=0, loeschen_ok=True, aendert_fassung=False):
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
        if "FROM marketing.chat_auftraege" in sql:
            return [{"status": self.chat.pop(0) if len(self.chat) > 1 else self.chat[0]}]
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
    assert f.t >= k.BILDER_MAX and f.pfade()[-1].endswith("/loeschen")


def test_fassung_aendert_sich_dauernd_laeuft_ins_limit():
    f = Fake(aendert_fassung=True)
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any("Zeitlimit für die Bilder" in z for z in aus)


def test_chat_fehler_raeumt_auf():
    f = Fake(chat=("in_arbeit", "fehler"))
    code, aus = _lauf(f, nur="split")
    assert code == 1 and any("Status fehler" in z for z in aus) and f.pfade()[-1].endswith("/loeschen")


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
