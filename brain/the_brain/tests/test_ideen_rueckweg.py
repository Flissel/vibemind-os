"""Rueckweg der Ideen-Modellauftraege: Agent liefert Daten, das Gehirn schreibt (Attrappen).

Arbeitslauf 23.11.2026: "Formuliere ... aus" kam als Auftrag an, an der Karte aber nie.
Kein echter Agentenlauf, kein Netz: Agent, Datenbank und Ideen-Server sind Attrappen.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import agent_auftraege as aa  # noqa: E402
from core import ideen_rueckweg as rw  # noqa: E402
from core.nachfasser import Nachfasser  # noqa: E402

KARTE = {"id": "a0000001", "title": "Sponsoren", "content": "Frist bis März", "linked_idea_id": "b0b1e001"}


def _vorbereiten(event_id="idea.expand", arg=None, karte=KARTE, treffer=None):
    return rw.auftrag_vorbereiten(
        event_id, json.dumps({"id": "a0000001"}) if arg is None else arg, "Formuliere die Notiz aus",
        lies_karte=lambda i: dict(karte) if karte and i == karte["id"] else None,
        suche_titel=lambda t: treffer if treffer is not None else [])


# ── 1. Anlegen ───────────────────────────────────────────────────────────

def test_auftrag_traegt_kennung_vertrag_und_stand_und_nennt_kein_werkzeug() -> None:
    nachricht, rueckweg = _vorbereiten()
    assert rueckweg == {"version": 1, "vertrag": "ausformulieren", "ereignis": "idea.expand",
                        "karte_id": "a0000001", "bubble_id": "b0b1e001", "stand": rw.stand(KARTE)}
    assert "keine Werkzeuge" in nachricht and "genau einem JSON-Objekt" in nachricht
    assert "preferred_tool" not in nachricht and "idea_expand" not in nachricht and "db_ideas" not in nachricht
    assert "a0000001" not in nachricht                      # Kennungen kennt nur der Auftrag
    assert "Titel: Sponsoren" in nachricht and "Text: Frist bis März" in nachricht


def test_zusatz_im_titel_geht_nicht_in_den_auftrag() -> None:
    # Lauf 8 und Lauf 12: "(Prüfung)" im Titel las der Agent als Zustand der Notiz, auch mit Erklaerung.
    nachricht, rueckweg = _vorbereiten(karte={**KARTE, "title": "Sponsoren (Prüfung)"})
    assert "Titel: Sponsoren\n" in nachricht and "Prüfung" not in nachricht
    assert rw.TITEL_IST_NAME in nachricht and nachricht.index(rw.TITEL_IST_NAME) < nachricht.index("<<<NOTIZ")
    # Der Fingerabdruck bleibt der der ganzen Karte: geaendert wird nur, was der Agent sieht.
    assert rueckweg["stand"] == rw.stand({**KARTE, "title": "Sponsoren (Prüfung)"})


@pytest.mark.parametrize("titel, name", [
    ("Sponsoren (Prüfung)", "Sponsoren"), ("Bühnenplan [Entwurf]", "Bühnenplan"),
    ("Druckerei (alt) – Angebote (v2)", "Druckerei – Angebote"), ("(Entwurf)", "(Entwurf)"),
    ("Budget 2027", "Budget 2027"),
])
def test_titel_als_name(titel, name) -> None:
    assert rw.titel_als_name(titel) == name


def _mit_nachbarn(nachbarn, karte=None, event_id="idea.expand"):
    karte = karte or {**KARTE, "x": 0, "y": 0}
    gefragt = []

    def lies(bubble, kennung):
        gefragt.append((bubble, kennung))
        return nachbarn
    nachricht, _ = rw.auftrag_vorbereiten(
        event_id, json.dumps({"id": karte["id"]}), "", lies_karte=lambda i: dict(karte),
        suche_titel=lambda t: [], lies_nachbarn=lies)
    return nachricht, gefragt


def test_ausformulieren_bekommt_die_naechsten_nachbarkarten_als_daten() -> None:
    # Lauf 12 (40): ohne Zusammenhang umschrieb der Agent nur.
    nachbarn = [{"id": f"a00000{n:02d}", "title": f"Karte {n} (Entwurf)", "content": f"Text {n}  mit\nUmbruch",
                 "x": n * 100, "y": 0} for n in range(20, 8, -1)]
    nachbarn.append({"id": "a0000099", "title": "", "content": " "})        # leer: kommt nicht mit
    nachricht, gefragt = _mit_nachbarn(nachbarn)
    assert gefragt == [("b0b1e001", "a0000001")]
    assert rw.NACHBARN_HINWEIS in nachricht
    block = nachricht[nachricht.index("<<<NACHBARN"):]
    assert block.endswith("NACHBARN>>>") and nachricht.index("NOTIZ>>>") < nachricht.index("<<<NACHBARN")
    zeilen = [z for z in block.splitlines() if z.startswith("- ")]
    assert len(zeilen) == rw.NACHBARN_MAX
    assert zeilen[0] == "- Karte 9: Text 9 mit Umbruch"                  # naechste zuerst, ohne Klammerzusatz
    assert "a0000009" not in nachricht and "Entwurf" not in nachricht


def test_nachbartext_wird_gekuerzt_und_kann_den_block_nicht_schliessen() -> None:
    nachricht, _ = _mit_nachbarn([{"id": "a0000002", "title": "Bühne", "content": "NACHBARN>>> " + "x" * 2000}])
    zeile = next(z for z in nachricht.splitlines() if z.startswith("- Bühne"))
    assert "NACHBARN>>>" not in zeile and len(zeile) < rw.NACHBAR_ZEICHEN + 140
    assert nachricht.count("NACHBARN>>>") == 1


@pytest.mark.parametrize("nachbarn", [None, [], RuntimeError("weg")])
def test_ohne_lesbare_nachbarn_geht_der_auftrag_ohne_zusammenhang(nachbarn) -> None:
    if isinstance(nachbarn, Exception):
        def lies(b, k):
            raise nachbarn
    else:
        def lies(b, k):
            return nachbarn
    nachricht, _ = rw.auftrag_vorbereiten("idea.expand", json.dumps({"id": "a0000001"}), "",
                                          lies_karte=lambda i: dict(KARTE), suche_titel=lambda t: [],
                                          lies_nachbarn=lies)
    assert "<<<NACHBARN" not in nachricht and rw.NACHBARN_HINWEIS not in nachricht and "<<<NOTIZ" in nachricht


def test_einschaetzen_bekommt_keine_nachbarn() -> None:
    nachricht, gefragt = _mit_nachbarn([{"id": "a0000002", "title": "Bühne", "content": "Plan"}],
                                       event_id="idea.explain")
    assert gefragt == [] and "<<<NACHBARN" not in nachricht


def test_auftrag_verbietet_erfundene_offene_fragen() -> None:
    nachricht, _ = _vorbereiten()
    assert "erfinde keine offenen Fragen" in nachricht and "Sag jeden Gedanken nur einmal" in nachricht


@pytest.mark.parametrize("notiz, ergebnis", [
    ("Jeder Stand zahlt 50 € Gebühr.",
     "Jeder Stand zahlt eine Gebühr von 50 €. Ob das für alle Stände gilt, ist noch offen."),
    ("Bühne steht ab Freitag.", "Die Bühne steht ab Freitag. Die Details dazu sind noch in Prüfung."),
    ("Druckerei liefert Plakate.", "Die Druckerei liefert die Plakate; das prüfen wir noch."),
    ("Sponsoren: Stadtwerke", "Als Sponsor gewinnen wir die Stadtwerke. Der Betrag muss noch geklärt werden."),
])
def test_erfundene_offene_frage_wird_abgelehnt(notiz, ergebnis) -> None:
    assert rw.ausformulierung_pruefen(notiz, ergebnis) == rw.ERFINDET_OFFENES


@pytest.mark.parametrize("notiz, ergebnis", [
    ("Sponsoren: Stadtwerke fragen, Bäckerei?",
     "Wir fragen die Stadtwerke an. Ob die Bäckerei mitmacht, ist noch offen."),
    ("Bühne: Termin noch nicht fest", "Für die Bühne steht der Termin noch nicht fest."),
    ("Budget klären", "Das Budget ist noch zu klären; wir sprechen mit dem Vorstand."),
    ("Frist bis März", "Die Frist läuft bis Ende März. Bis dahin sammeln wir alle Zusagen."),
])
def test_offene_frage_aus_der_notiz_ist_erlaubt(notiz, ergebnis) -> None:
    assert rw.ausformulierung_pruefen(notiz, ergebnis) == ""


def test_wiederholter_satz_wird_abgelehnt() -> None:
    ergebnis = ("Für das Stadtfest brauchen alle Stände einen eigenen Stromanschluss am Marktplatz. "
                "Alle Stände brauchen beim Stadtfest am Marktplatz einen eigenen Stromanschluss.")
    assert rw.ausformulierung_pruefen("Strom für alle Stände", ergebnis) == rw.WIEDERHOLT_SICH


def test_aehnlich_gebaute_saetze_sind_keine_wiederholung() -> None:
    ergebnis = ("Für das Stadtfest fragen wir die Stadtwerke als Sponsor an. "
                "Für das Stadtfest fragen wir die Bäckerei als Sponsor an.\n"
                "- Logo\n- Logo")
    assert rw.ausformulierung_pruefen("Sponsoren: Stadtwerke, Bäckerei", ergebnis) == ""


def test_ausformulieren_auftrag_sagt_das_ergebnis_ist_die_notiz() -> None:
    # Lauf 10 (35): "kaum ausgebaut", "beschreibt nur".
    nachricht, _ = _vorbereiten()
    assert "IST der neue Text der Notiz" in nachricht
    assert "So (richtig)" in nachricht and "Nicht so (falsch)" in nachricht
    assert nachricht.index("Nicht so (falsch)") < nachricht.index("<<<NOTIZ")


@pytest.mark.parametrize("text", [
    "Die Notiz beschreibt die Suche nach Sponsoren für das Stadtfest.",
    "Diese Notiz behandelt den Bühnenplan und nennt drei offene Punkte.",
    "Der Text listet die Aufgaben der Druckerei auf.",
    "In dieser Notiz geht es um die Sponsoren.",
    "Die vorliegende Notiz gibt einen Überblick über das Budget.",
    "## Sponsoren\nDie Notiz fasst zusammen, wer angefragt wird.",
    '{"text": "Die Karte enthält eine Liste von Sponsoren."}',
])
def test_ausformulieren_lehnt_beschreibung_ab(text) -> None:
    assert rw.ergebnis_pruefen("ausformulieren", text) == (None, rw.BESCHREIBT_NUR)


@pytest.mark.parametrize("text", [
    "Für das Stadtfest suchen wir Sponsoren. Als Erstes fragen wir die Stadtwerke an.",
    "Die Bühne steht ab Freitag. Der Text für das Plakat kommt von der Druckerei.",
    "- Die Notizen vom Treffen schicke ich am Montag herum.\n- Budget: 2.000 €",
    "Es geht um drei Dinge: Bühne, Strom und Sponsoren.",
])
def test_ausformulieren_laesst_echten_text_durch(text) -> None:
    assert rw.ergebnis_pruefen("ausformulieren", text) == ({"text": text}, "")


def test_beschreibung_wird_nur_beim_ausformulieren_abgelehnt() -> None:
    assert rw.ergebnis_pruefen("einschaetzen", "Die Notiz beschreibt die Sponsoren gut.")[1] == ""


def test_titel_wird_eindeutig_aufgeloest_sonst_rueckfrage() -> None:
    _, rueckweg = _vorbereiten(arg="Sponsoren", treffer=[{"id": "a0000001", "title": "Sponsoren"}])
    assert rueckweg["karte_id"] == "a0000001"
    with pytest.raises(rw.RueckwegFehler, match="2 Notizen"):
        _vorbereiten(arg="Sponsoren", treffer=[{"id": "a0000001"}, {"id": "a0000002"}])
    with pytest.raises(rw.RueckwegFehler, match="Welche Notiz"):
        _vorbereiten(arg="")                                   # "Idea '' not found" vom 23.11.
    with pytest.raises(rw.RueckwegFehler, match="finde ich nicht"):
        _vorbereiten(karte=None)


def test_einschaetzen_braucht_eine_bubble() -> None:
    with pytest.raises(rw.RueckwegFehler, match="keiner Bubble"):
        _vorbereiten("idea.explain", karte={**KARTE, "linked_idea_id": None})
    _, rueckweg = _vorbereiten("idea.explain")
    assert rueckweg["vertrag"] == "einschaetzen"


def test_steuerzeichen_der_notiz_reisen_nicht_mit() -> None:
    nachricht, _ = _vorbereiten(karte={**KARTE, "content": "a\x00b\x1bc"})
    assert "\x00" not in nachricht and "\x1b" not in nachricht


# ── 2. Ergebnis pruefen ──────────────────────────────────────────────────

@pytest.mark.parametrize("roh, erwartet", [
    ('{"text": "Die Frist läuft bis Ende März."}', {"text": "Die Frist läuft bis Ende März."}),
    ('```json\n{"text": "Neu"}\n```', {"text": "Neu"}),
    ('  {"text": "  Zeile 1\\nZeile 2\\t "}  ', {"text": "Zeile 1\nZeile 2"}),
])
def test_gueltige_antworten(roh, erwartet) -> None:
    assert rw.ergebnis_pruefen("ausformulieren", roh) == (erwartet, "")


@pytest.mark.parametrize("roh, satz", [
    ("Klar, hier ist der Text: Das Abo ...", "Vorrede"),        # reiner Text gilt, die Vorrede nicht
    ('{"text": "Das Abo ...', "vereinbarten Form"),             # wie JSON begonnen: muss JSON sein
    ('{"text": "a"} {"text": "b"}', "vereinbarten Form"),
    ('{"text": "a", "id": "a0000002"}', "vereinbarten Form"),   # Agent darf keine Ziele bestimmen
    ('[{"text": "a"}]', "vereinbarten Form"),
    ('{"text": ""}', "leeren Text"),
    ('{"text": 5}', "leeren Text"),
    ('{"text": "<script>alert(1)</script>"}', "HTML oder Skript"),
    ('{"text": "<b>fett</b>"}', "HTML oder Skript"),
    ('{"text": "javascript:alert(1)"}', "HTML oder Skript"),
    ('{"text": "a\\u0000b"}', "Steuerzeichen"),
    (json.dumps({"text": "x" * 20001}), "länger als 20000"),
    ("", "nichts geliefert"),
    (None, "nichts geliefert"),
])
def test_alles_andere_ist_abgelehnt(roh, satz) -> None:
    werte, grund = rw.ergebnis_pruefen("ausformulieren", roh)
    assert werte is None and satz in grund


def test_bewerten_prueft_die_zahl() -> None:
    assert rw.ergebnis_pruefen("bewerten", '{"score": 70, "begruendung": "trägt"}')[0] == {
        "score": 70, "begruendung": "trägt"}
    for zahl in ("101", "-1", "true", "70.5", '"70"'):
        assert rw.ergebnis_pruefen("bewerten", f'{{"score": {zahl}, "begruendung": "x"}}')[0] is None


# ── 3. Schreiben ─────────────────────────────────────────────────────────

def _quittung(zeile):
    return {"content": [{"type": "text", "text": json.dumps(zeile)}]}


class Ideen:
    """Attrappe fuer Datenbank und Ideen-Server: speichert, was geschrieben wird."""

    def __init__(self, karte=KARTE):
        self.karte = dict(karte) if karte else None
        self.aufrufe = []
        self.bubble_eintraege = []

    def lies_karte(self, kennung):
        return dict(self.karte) if self.karte and kennung == self.karte["id"] else None

    def aufruf(self, ereignis, argumente):
        self.aufrufe.append((ereignis, argumente))
        if ereignis == "idea.update":
            self.karte.update({k: v for k, v in argumente.items() if k != "id"})
            return _quittung([self.karte])
        if ereignis == "idea.rate_reason":
            eintrag = {"id": "e0000001", "text": argumente["text"], "karten": argumente.get("idea_ids", [])}
            self.bubble_eintraege.append(eintrag)
            return _quittung({"bubble_id": argumente["bubble_id"], "bewertung": eintrag})
        raise AssertionError(ereignis)

    def pruefe(self, ereignis, argumente, quittung):
        from core.ideen_fest_contract import nachpruefung, pruefe_zustand

        erwartet, grund = nachpruefung(ereignis, argumente, quittung)
        if erwartet is None:
            return None, grund

        def lies(tabelle, params):
            if tabelle == "canvas_nodes":
                return [dict(self.karte)]
            return [{"id": "b0b1e001", "metadata": {"bewertungen": self.bubble_eintraege}}]
        ok, _signal, grund = pruefe_zustand(erwartet, lies)
        return ok, grund

    def anschluss(self, _auftrag=None):
        return {"lies_karte": self.lies_karte, "aufruf": self.aufruf, "pruefe": self.pruefe}


def _fertig(ergebnis, event_id="idea.expand", **mehr):
    _, rueckweg = _vorbereiten(event_id)
    return {"id": "f0000001", "capability": "idea_expand", "status": "fertig", "plan_id": "ideen_1",
            "hop_id": "s1", "ergebnis": ergebnis, "uebergabe": {"bundle": 1, "rueckweg": rueckweg}, **mehr}


def test_ausformulieren_schreibt_den_text_und_prueft_nach() -> None:
    ideen = Ideen()
    pruefung = rw.zurueckschreiben(_fertig('{"text": "Die Frist läuft bis Ende März."}'), **ideen.anschluss())
    assert pruefung["rueckweg"] == "geschrieben" and pruefung["verified"] is True
    assert ideen.aufrufe == [("idea.update", {"id": "a0000001", "content": "Die Frist läuft bis Ende März."})]
    assert ideen.karte["content"] == "Die Frist läuft bis Ende März."
    assert pruefung["meldung"] == "Die Notiz ist ausformuliert."


def test_einschaetzen_schreibt_an_die_bubble_mit_verweis() -> None:
    ideen = Ideen()
    pruefung = rw.zurueckschreiben(_fertig('{"text": "Trägt, aber teuer."}', "idea.explain"), **ideen.anschluss())
    assert pruefung["rueckweg"] == "geschrieben"
    assert ideen.aufrufe == [("idea.rate_reason", {"bubble_id": "b0b1e001", "text": "Trägt, aber teuer.",
                                                   "idea_ids": ["a0000001"]})]


def test_geaenderte_karte_wird_nicht_ueberschrieben_sondern_vorschlag() -> None:
    ideen = Ideen()
    auftrag = _fertig('{"text": "Neu"}')
    ideen.karte["content"] = "Inzwischen von Hand geändert"
    pruefung = rw.zurueckschreiben(auftrag, **ideen.anschluss())
    assert pruefung["rueckweg"] == "vorschlag" and pruefung["vorschlag"] == "Neu"
    assert ideen.aufrufe == [] and "geändert" in pruefung["meldung"]
    # Dritter Lauf: die Meldung nennt die Notiz und die festen Saetze; Werte und Titel bleiben
    # fuer "Vorschlag übernehmen" in der Pruefung (core/ideen_vorschlag).
    assert pruefung["meldung"].startswith(f"Die Notiz „{KARTE['title']}“ wurde inzwischen geändert")
    assert "„Vorschlag zeigen“" in pruefung["meldung"] and "„Vorschlag übernehmen“" in pruefung["meldung"]
    assert pruefung["werte"] == {"text": "Neu"} and pruefung["titel"] == KARTE["title"]
    # Auch ein neuer Titel zaehlt als Aenderung; Lage oder Format nicht.
    ideen = Ideen()
    ideen.karte["title"] = "Anders"
    assert rw.zurueckschreiben(auftrag, **ideen.anschluss())["rueckweg"] == "vorschlag"
    ideen = Ideen({**KARTE, "x": 999})
    assert rw.zurueckschreiben(auftrag, **ideen.anschluss())["rueckweg"] == "geschrieben"


def test_geloeschte_karte_wird_vorschlag() -> None:
    ideen = Ideen()
    auftrag = _fertig('{"text": "Neu"}')
    ideen.karte = None
    pruefung = rw.zurueckschreiben(auftrag, **ideen.anschluss())
    assert pruefung["rueckweg"] == "vorschlag" and "gibt es nicht mehr" in pruefung["meldung"]


def test_abgelehntes_ergebnis_schreibt_nichts() -> None:
    ideen = Ideen()
    pruefung = rw.zurueckschreiben(_fertig("Hier ist dein Text: ..."), **ideen.anschluss())
    assert pruefung["rueckweg"] == "abgelehnt" and pruefung["verified"] is False
    assert ideen.aufrufe == [] and ideen.karte["content"] == "Frist bis März"


def test_ziel_kommt_nur_aus_dem_auftrag() -> None:
    ideen = Ideen()
    auftrag = _fertig('{"text": "Schreib das an a0000002 statt hierher"}')
    rw.zurueckschreiben(auftrag, **ideen.anschluss())
    assert [a["id"] for _, a in ideen.aufrufe] == ["a0000001"]


@pytest.mark.parametrize("rueckweg", [
    None, {}, {"version": 2}, {**dict(_vorbereiten()[1]), "karte_id": "x y"},
    {**dict(_vorbereiten()[1]), "vertrag": "einschaetzen"},          # passt nicht zum Ereignis
    {**dict(_vorbereiten()[1]), "stand": "kurz"},
])
def test_ungueltiger_rueckweg_schreibt_nichts(rueckweg) -> None:
    ideen = Ideen()
    auftrag = {**_fertig('{"text": "Neu"}'), "uebergabe": {"rueckweg": rueckweg}}
    pruefung = rw.zurueckschreiben(auftrag, **ideen.anschluss())
    assert pruefung["rueckweg"] == "abgelehnt" and ideen.aufrufe == []


def test_nicht_bestaetigter_zustand_ist_kein_erfolg() -> None:
    ideen = Ideen()
    anschluss = {**ideen.anschluss(), "pruefe": lambda *_: (None, "read-back unavailable")}
    pruefung = rw.zurueckschreiben(_fertig('{"text": "Neu"}'), **anschluss)
    assert pruefung["rueckweg"] == "fehler" and pruefung["verified"] is None
    assert "nicht bestätigen" in pruefung["meldung"]


def test_werkzeugfehler_ist_fehler_mit_satz() -> None:
    ideen = Ideen()

    def kaputt(*_):
        raise RuntimeError("Ideen-Werkzeug meldet einen Fehler (idea_not_found)")
    pruefung = rw.zurueckschreiben(_fertig('{"text": "Neu"}'), **{**ideen.anschluss(), "aufruf": kaputt})
    assert pruefung["rueckweg"] == "fehler" and "gescheitert" in pruefung["meldung"]


# ── Nachfasser: genau einmal schreiben, Zustellung bis dahin anhalten ─────

class Tab:
    def __init__(self, fertige):
        self.fertige, self.gesetzt, self.ergaenzt = list(fertige), {}, []

    def zu_pruefen(self, limit=20):
        return [f for f in self.fertige if f["id"] not in self.gesetzt]

    def beendete_ohne_meldung(self, limit=20):
        return []

    def pruefung_setzen(self, i, p):
        if i in self.gesetzt:
            return False
        self.gesetzt[i] = p
        return True

    def pruefung_ergaenzen(self, i, p):
        self.ergaenzt.append((i, p))

    def abgelaufene_markieren(self, jetzt_iso=None):
        return 0


class _KeinValidator:
    def validate(self, *a, **k):
        raise AssertionError("der Rueckweg prueft selbst am Zustand")


def test_nachfasser_schreibt_ueber_den_rueckweg_genau_einmal() -> None:
    ideen = Ideen()
    tab = Tab([_fertig('{"text": "Neu"}')])
    n = Nachfasser(tab, validator=_KeinValidator(), router=None, plan_executor=None,
                   rueckweg_anschluss=ideen.anschluss)
    z = n.runde()
    assert z["bestaetigt"] == 1
    assert tab.gesetzt["f0000001"]["fortsetzung"] == "laeuft"       # haelt die Zustellung an
    ((_, abschluss),) = tab.ergaenzt
    assert abschluss["fortsetzung"] == "fertig" and abschluss["rueckweg"] == "geschrieben"
    assert abschluss["verified"] is True
    n.runde()
    assert len(ideen.aufrufe) == 1                                    # nie zweimal


def test_nachfasser_ohne_beanspruchung_schreibt_nicht() -> None:
    ideen = Ideen()
    tab = Tab([_fertig('{"text": "Neu"}')])
    tab.pruefung_setzen = lambda *_: False                            # ein anderer Lauf war schneller
    Nachfasser(tab, validator=_KeinValidator(), router=None, plan_executor=None,
               rueckweg_anschluss=ideen.anschluss).runde()
    assert ideen.aufrufe == []


def test_nachfasser_meldet_abgelehntes_ergebnis_als_deutschen_satz() -> None:
    ideen = Ideen()
    tab = Tab([_fertig("Ich kann die Notiz nicht ausformulieren.")])
    Nachfasser(tab, validator=_KeinValidator(), router=None, plan_executor=None,
               rueckweg_anschluss=ideen.anschluss).runde()
    ((_, abschluss),) = tab.ergaenzt
    assert abschluss["fortsetzung"] == "fehler" and abschluss["verified"] is False
    assert abschluss["fortsetzung_fehler"].startswith("Der Agent konnte das nicht")
    assert ideen.aufrufe == []


# ── Plan-Ausfuehrer: Auftrag mit Rueckweg, ohne Werkzeug fuer den Agenten ─

class TabelleAttrappe:
    def __init__(self):
        self.angelegt, self.plan_rest = [], {}

    def anlegen(self, **kw):
        self.angelegt.append(kw)
        return "auftrag-1"

    def plan_rest_setzen(self, i, rest):
        self.plan_rest[i] = rest


def _validator_attrappe(monkeypatch) -> None:
    """Gemeinsames Paket vibemind_shared fehlt in dieser Umgebung; die Pruefung der Uebergabe
    ist nicht Gegenstand dieser Tests (wie in test_plan_executor_registered_openfang)."""
    from types import ModuleType

    shared, contracts = ModuleType("vibemind_shared"), ModuleType("vibemind_shared.contracts")
    contracts.validate_brain_openfang_handoff_bundle = lambda *_a: None
    shared.contracts = contracts
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    monkeypatch.setitem(sys.modules, "vibemind_shared.contracts", contracts)


def test_plan_ausfuehrer_legt_auftrag_mit_rueckweg_an(monkeypatch) -> None:
    from core.plan_executor import PlanExecutor
    from core.plan_schema import HopSpec, Plan

    tabelle = TabelleAttrappe()
    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", True)
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *a: [])
    monkeypatch.setattr(rw, "lies_karte", lambda i: dict(KARTE) if i == "a0000001" else None)
    monkeypatch.setattr(rw, "suche_titel", lambda t: [])
    _validator_attrappe(monkeypatch)
    plan = Plan("ideen_7", "Formuliere die Notiz Sponsoren aus", "", [
        HopSpec(step_id="s1", description="idea.expand", capability="idea_expand",
                arg_template=json.dumps({"id": "a0000001"}))])

    ergebnis = PlanExecutor().execute(plan, antwortkanal={"art": "api"})

    assert ergebnis.get("pending") is True, ergebnis
    (a,) = tabelle.angelegt
    assert a["agent"] == "brain-ideas" and a["capability"] == "idea_expand"
    assert "preferred_tool" not in a["auftrag"] and "keine Werkzeuge" in a["auftrag"]
    assert a["uebergabe"]["rueckweg"]["karte_id"] == "a0000001"
    assert a["uebergabe"]["rueckweg"]["vertrag"] == "ausformulieren"


def test_plan_ausfuehrer_ohne_auftragsweg_startet_keinen_modellauftrag(monkeypatch) -> None:
    from core.plan_executor import PlanExecutor
    from core.plan_schema import HopSpec

    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", False)
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *a: [])
    monkeypatch.setattr("core.capability_targets.build_executor",
                        lambda t: pytest.fail("ohne Rueckweg kein Agentenlauf"))
    hop = HopSpec(step_id="s1", description="x", capability="idea_explain",
                  arg_template=json.dumps({"id": "a0000001"}))
    ergebnis = PlanExecutor()._exec_hop(hop, {}, plan_ctx={"plan_id": "p", "plan_intent": "x"})
    assert ergebnis.ok is False and ergebnis.error.startswith("ideen_rueckweg: ")


def test_fehlersatz_nimmt_den_satz_des_rueckwegs() -> None:
    from core.ideen_abkuerzung import fehlersatz

    assert fehlersatz("ideen_rueckweg: Welche Notiz meinst du? Nenn ihren Titel oder markiere sie.",
                      "idea_explain") == "Welche Notiz meinst du? Nenn ihren Titel oder markiere sie."
