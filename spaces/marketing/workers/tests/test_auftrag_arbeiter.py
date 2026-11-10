"""Auftrags-Arbeiter ohne Netz: Fake-API und Fake-Claude."""
import json

import pytest

from spaces.marketing.workers import auftrag_arbeiter as aa
from spaces.marketing.workers.bild_worker import ApiFehler

AID = "0123abcd-4444-4444-4444-444444444444"
NEU = {"id": AID, "art": "reel", "mandant": "fin2gether", "eingabe": {"thema": "bAV in 30 Sekunden"},
       "ergebnis": {}, "versuch": 1}
LAUFEND = {**NEU, "ergebnis": {"video": "vid-1", "drehbuch": {
    "titel": "bAV", "thema": "bAV im Detail", "sprechertext": "Drei Fehler.", "hook": "Stopp!", "dauer_s": 30}}}
OHNE_DREHBUCH = {**NEU, "ergebnis": {"video": "vid-1"}}
DREHBUCH = json.dumps({"titel": "bAV", "sprechertext": "Drei Fehler.", "hook": "Stopp!", "dauer_s": 30})
BEITRAG = json.dumps({"titel": "bAV: drei Fehler", "text_lang": "Lang.", "text_kurz": "Kurz."})


class Api:
    def __init__(self, neu=None, laufend=(), reel=None, weiter=True, anlegen_fehler=None):
        self.neu, self.laufende, self.reel = neu, list(laufend), reel or {}
        self.weiter_ok, self.anlegen_fehler = weiter, anlegen_fehler
        self.aufrufe = []

    def naechster(self):
        return self.neu

    def laufend(self):
        return self.laufende

    def weiter(self, aid):
        self.aufrufe.append(("weiter", aid))
        return self.weiter_ok

    def reel_anlegen(self, aid, drehbuch):
        if self.anlegen_fehler:
            raise self.anlegen_fehler
        self.aufrufe.append(("reel", aid, drehbuch))
        return "vid-1"

    def reel_lesen(self, aid):
        return self.reel

    def erzeugt(self, aid, texte):
        self.aufrufe.append(("erzeugt", aid, texte))
        return True

    def fehler(self, aid, grund):
        self.aufrufe.append(("fehler", aid, grund))
        return True

    def namen(self):
        return [a[0] for a in self.aufrufe]


class Claude:
    def __init__(self, *antworten):
        self.antworten, self.fragen = list(antworten), []

    def __call__(self, system, nachricht):
        self.fragen.append((system, nachricht))
        return self.antworten.pop(0)


def lauf(api, claude, pausen=None):
    return aa.ein_durchlauf(api, claude, (pausen if pausen is not None else []).append, lambda: 1000.0,
                            marke=lambda m, t: "Marke: Fin2gether")


def test_leer_ohne_auftrag():
    assert lauf(Api(), Claude()) == "leer"


def test_erster_durchlauf_beauftragt_reel():
    api, claude = Api(neu=NEU), Claude(DREHBUCH)
    assert lauf(api, claude) == "reel_beauftragt"
    assert api.namen() == ["weiter", "weiter", "reel"]        # Lease vor und nach der Claude-Anfrage
    drehbuch = api.aufrufe[-1][2]
    assert drehbuch == {"titel": "bAV", "thema": "bAV in 30 Sekunden", "sprechertext": "Drei Fehler.",
                        "hook": "Stopp!", "dauer_s": 30}
    assert "Marke: Fin2gether" in claude.fragen[0][0]


def test_wartet_solange_video_laeuft():
    api = Api(laufend=[LAUFEND], reel={"status": "rendert", "datei": None, "befund": None})
    claude = Claude()
    assert lauf(api, claude) == "wartet"
    assert api.aufrufe == [] and claude.fragen == []


def test_fertiges_video_ergibt_erzeugt():
    api = Api(laufend=[LAUFEND], reel={"status": "fertig", "datei": "vid.mp4", "befund": None})
    claude = Claude(BEITRAG)
    assert lauf(api, claude) == "erzeugt"
    assert api.aufrufe == [("erzeugt", AID, {"titel": "bAV: drei Fehler", "text_lang": "Lang.", "text_kurz": "Kurz."})]
    assert "Drei Fehler." in claude.fragen[0][1] and "bAV im Detail" in claude.fragen[0][1]


def test_fertiges_video_ohne_drehbuch_nur_mit_thema():
    api = Api(laufend=[OHNE_DREHBUCH], reel={"status": "fertig", "datei": "vid.mp4", "befund": None})
    claude = Claude(BEITRAG)
    assert lauf(api, claude) == "erzeugt"
    assert "Sprechertext" not in claude.fragen[0][1] and "bAV in 30 Sekunden" in claude.fragen[0][1]


@pytest.mark.parametrize("status,befund,grund", [("abgebrochen", None, "Reel: abgebrochen"),
                                                   ("fehler", "Laura nicht erreichbar", "Reel: Laura nicht erreichbar")])
def test_abgebrochenes_video_macht_auftrag_fehler(status, befund, grund):
    api = Api(laufend=[LAUFEND], reel={"status": status, "datei": None, "befund": befund})
    assert lauf(api, Claude()) == "fehler"
    assert api.aufrufe == [("fehler", AID, grund)]


def test_zweimal_unbrauchbar_fehler():
    api, pausen = Api(neu=NEU), []
    claude = Claude("Kein JSON", "Immer noch nicht")
    assert lauf(api, claude, pausen) == "fehler"
    assert len(claude.fragen) == 2 and len(pausen) == 1
    assert api.aufrufe[-1] == ("fehler", AID, "Drehbuch unbrauchbar") and "reel" not in api.namen()


def test_einmal_unbrauchbar_dann_gut():
    api = Api(neu=NEU)
    assert lauf(api, Claude("Muell", DREHBUCH)) == "reel_beauftragt"


def test_beitragstext_zweimal_unbrauchbar_fehler():
    api = Api(laufend=[LAUFEND], reel={"status": "fertig", "datei": "vid.mp4", "befund": None})
    assert lauf(api, Claude("x", json.dumps({"titel": "t", "text_lang": "l", "text_kurz": "k" * 300}))) == "fehler"
    assert api.aufrufe == [("fehler", AID, "Beitragstext unbrauchbar")]


def test_laufende_vor_neuem_auftrag():
    api = Api(neu=NEU, laufend=[LAUFEND], reel={"status": "rendert"})
    assert lauf(api, Claude(DREHBUCH)) == "reel_beauftragt"      # Ergebnis des letzten Schritts


def test_lease_verloren_nichts_anlegen():
    api = Api(neu=NEU, weiter=False)
    claude = Claude()
    assert lauf(api, claude) == "leer" and claude.fragen == []


def test_reel_409_ist_kein_fehler():
    api = Api(neu=NEU, anlegen_fehler=ApiFehler(409, "Der Auftrag hat schon ein Video"))
    assert lauf(api, Claude(DREHBUCH)) == "leer"
    assert "fehler" not in api.namen()


class LaufendFehler(Api):
    """Das Lesen des Reels scheitert fuer den ersten Auftrag."""
    def __init__(self, fehler, **kw):
        super().__init__(**kw)
        self.lese_fehler = fehler

    def reel_lesen(self, aid):
        if aid == AID:
            raise self.lese_fehler
        return self.reel


ZWEITER = {**LAUFEND, "id": "0123abcd-4444-4444-4444-555555555555"}


def test_ein_kaputter_auftrag_haelt_die_anderen_nicht_auf():
    api = LaufendFehler(ApiFehler(404, "Kein Reel verknuepft"), neu=NEU, laufend=[LAUFEND, ZWEITER],
                        reel={"status": "fertig", "datei": "v.mp4", "befund": None})
    assert lauf(api, Claude(BEITRAG, DREHBUCH)) == "reel_beauftragt"      # naechster lief trotzdem
    namen = [(a[0], a[1]) for a in api.aufrufe]
    assert ("fehler", AID) in namen and ("erzeugt", ZWEITER["id"]) in namen
    assert api.aufrufe[0][2] == "Auftrag nicht verarbeitbar (API 404)"


def test_netzfehler_laesst_auftrag_liegen_und_geht_weiter():
    api = LaufendFehler(OSError("weg"), laufend=[LAUFEND, ZWEITER],
                        reel={"status": "fertig", "datei": "v.mp4", "befund": None})
    assert lauf(api, Claude(BEITRAG)) == "erzeugt"
    assert "fehler" not in api.namen()


def test_422_bei_reel_macht_auftrag_fehler():
    api = Api(neu=NEU, anlegen_fehler=ApiFehler(422, "thema"))
    assert lauf(api, Claude(DREHBUCH)) == "fehler"
    assert api.aufrufe[-1] == ("fehler", AID, "Auftrag nicht verarbeitbar (API 422)")


def test_shim_fehler_bei_neuem_auftrag_wirft_nicht():
    def kaputt(system, nachricht):
        raise RuntimeError("Shim weg")
    api = Api(neu=NEU)
    assert aa.ein_durchlauf(api, kaputt, lambda s: None, lambda: 0.0, marke=lambda m, t: "") == "fehler"
    assert "fehler" not in api.namen()


def test_ohne_thema_kein_claude_aufruf():
    api, claude = Api(neu={**NEU, "eingabe": {}}), Claude()
    assert lauf(api, claude) == "fehler" and claude.fragen == []
    assert api.aufrufe[-1] == ("fehler", AID, "Kein Thema im Auftrag")


def test_lease_zwischen_den_versuchen_erneuern():
    api = Api(neu=NEU)
    assert lauf(api, Claude("Muell", DREHBUCH)) == "reel_beauftragt"
    assert api.namen() == ["weiter", "weiter", "weiter", "reel"]


def test_lease_zwischen_den_versuchen_verloren():
    class Verliert(Api):
        def weiter(self, aid):
            super().weiter(aid)
            return len(self.aufrufe) < 2        # erster Aufruf ok, danach weg
    api = Verliert(neu=NEU)
    assert lauf(api, Claude("Muell")) == "leer" and "reel" not in api.namen()


def test_erzeugt_abgelehnt_ist_kein_erfolg():
    class Lehnt(Api):
        def erzeugt(self, aid, texte):
            return False
    api = Lehnt(laufend=[LAUFEND], reel={"status": "fertig", "datei": "v.mp4", "befund": None})
    assert lauf(api, Claude(BEITRAG)) == "leer"


def test_fehler_abgelehnt_ist_kein_fehler_ergebnis():
    class Lehnt(Api):
        def fehler(self, aid, grund):
            return False
    api = Lehnt(laufend=[LAUFEND], reel={"status": "abgebrochen"})
    assert lauf(api, Claude()) == "leer"
