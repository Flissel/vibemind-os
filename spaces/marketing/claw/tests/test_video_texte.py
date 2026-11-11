"""Schreiben der Video-Werkbank: Gliederung, Szenen, Quellenpruefung, Kopf, Brief."""
import json
from pathlib import Path

from spaces.marketing.claw import video_texte
from spaces.marketing.claw.video_kontext import Dokument, Kontext
from spaces.marketing.claw.video_texte import (BLOCK, gliederung_lesen, gliederung_prompt, kopf, szene_pruefen,
                                               szenen_liste, szenen_lesen, szenen_prompt)

AUFTRAG = "Schulungsvideo. Wir haben 12 Teilnehmer pro Gruppe und 3,5 Stunden Dauer."
KONTEXT = Kontext([Dokument("Marke.md", "Die Schulung kostet 1.500 Euro und spart 30 % Zeit.", "marke"),
                   Dokument("Schulungen/Angebot 2026", "Preis 2.400 EUR, 4 Termine.", "rowboat")], [])
LANG = ("Das ist ein erster Satz über die Schulung. Der zweite Satz erklärt den Ablauf in einfachen Worten. "
        "Der dritte Satz zeigt, was am Ende herauskommt. Der vierte Satz nennt den Nutzen für das Team im Alltag. "
        "Der fünfte Satz ist noch eine kurze Überleitung zur nächsten Stufe.")
BILD = {"schritte": [{"vorlage": "titel", "ab_satz": 0, "zeile": "Die Schulung", "unterzeile": "Kurz erklärt"},
                     {"vorlage": "stichpunkte", "ab_satz": 2, "ueberschrift": "Ablauf", "punkte": ["Eins", "Zwei"]}]}


def SZENE(**kw):
    s = {"nr": 1, "sprechtext": LANG, "bildart": "slide", "bild": BILD, "uebergang": "schnitt",
         "belege": [], "ohne_sachaussage": True, "aendert_text": True}
    s.update(kw)
    return s


def GLIEDERUNG_MIT(quelle, szenen=8):
    return json.dumps({"titel": "T", "hook": "H", "fehlt": ["dazu fehlt mir Material: ROI"],
                       "stufen": [{"name": "Einstieg", "szenen": [
                           {"kurz": f"Szene {i}", "bildart": "slide", "quellen": [quelle]} for i in range(szenen)]}]})


def test_gliederung_mit_fremder_quelle_faellt_durch():
    g, fehler = gliederung_lesen(GLIEDERUNG_MIT("Erfunden.pdf"), {"Marke.md"})
    assert g is None and "Erfunden.pdf" in fehler[0]


def test_gliederung_fehlt_material_bleibt_erhalten():
    g, fehler = gliederung_lesen(GLIEDERUNG_MIT("Marke.md"), {"Marke.md"})
    assert fehler == [] and g["fehlt"] == ["dazu fehlt mir Material: ROI"] and g["stufen"][0]["szenen"][0]["kurz"]


def test_gliederung_szenenzahl_bildart_und_kurz():
    assert "7 Szenen" in gliederung_lesen(GLIEDERUNG_MIT("auftrag", 7), set())[1][0]
    assert "16 Szenen" in gliederung_lesen(GLIEDERUNG_MIT("auftrag", 16), set())[1][0]
    roh = json.loads(GLIEDERUNG_MIT("auftrag"))
    roh["stufen"][0]["szenen"][0]["bildart"] = "hologramm"
    roh["stufen"][0]["szenen"][1] = "nur Text"
    roh["stufen"][0]["szenen"][2]["kurz"] = " "
    fehler = gliederung_lesen("```json\n" + json.dumps(roh) + "\n```", set())[1]
    assert any("hologramm" in f for f in fehler) and sum("kurz" in f for f in fehler) == 2


def test_zahl_ohne_beleg_faellt_durch():
    s = SZENE(sprechtext=LANG + " Das spart 40 % Zeit.", belege=[], ohne_sachaussage=False)
    assert any("40" in f for f in szene_pruefen(s, AUFTRAG, KONTEXT))


def test_quelle_nicht_geholt_faellt_durch():
    s = SZENE(belege=[{"aussage": "Es gibt eine Schulung.", "quelle": "Fremd.pdf"}], ohne_sachaussage=False)
    assert any("Fremd.pdf" in f for f in szene_pruefen(s, AUFTRAG, KONTEXT))


def test_zahl_steht_nicht_in_der_quelle_faellt_durch():
    s = SZENE(sprechtext=LANG + " Das spart 40 % Zeit.", ohne_sachaussage=False,
              belege=[{"aussage": "Die Schulung spart 40 % Zeit.", "quelle": "Marke.md"}])   # Dokument sagt 30 %
    f = szene_pruefen(s, AUFTRAG, KONTEXT)
    assert any("40" in x and "nicht in der Quelle" in x for x in f)


def test_zahl_aus_dem_beleg_besteht_mit_normalisierung():
    s = SZENE(sprechtext=LANG + " Sie kostet 1500 Euro und dauert 3,5 Stunden.", ohne_sachaussage=False,
              belege=[{"aussage": "Die Schulung kostet 1.500 Euro.", "quelle": "Marke.md"},
                      {"aussage": "Dauer 3.5 Stunden.", "quelle": "auftrag"}])
    assert szene_pruefen(s, AUFTRAG, KONTEXT) == []


def test_zahl_aus_dem_auftrag_besteht():
    s = SZENE(sprechtext=LANG + " Jede Gruppe hat 12 Teilnehmer.", ohne_sachaussage=False,
              belege=[{"aussage": "12 Teilnehmer pro Gruppe", "quelle": "auftrag"}])
    assert szene_pruefen(s, AUFTRAG, KONTEXT) == []


def test_einheit_muss_zum_beleg_passen():
    s = SZENE(sprechtext=LANG + " Das sind 30 Euro.", ohne_sachaussage=False,
              belege=[{"aussage": "Spart 30 % Zeit.", "quelle": "Marke.md"}])
    assert any("30 €" in f for f in szene_pruefen(s, AUFTRAG, KONTEXT))


def test_zahl_auf_der_folie_braucht_beleg():
    bild = {"schritte": [dict(BILD["schritte"][0]),
                         {"vorlage": "zahl", "ab_satz": 2, "zahl": "77", "einheit": "%", "erklaerung": "mehr"}]}
    assert any("77" in f for f in szene_pruefen(SZENE(bild=bild), AUFTRAG, KONTEXT))


def test_szene_ohne_sachaussage_besteht_ohne_beleg():
    assert szene_pruefen(SZENE(), AUFTRAG, KONTEXT) == []
    assert any("keinen Beleg" in f for f in szene_pruefen(SZENE(ohne_sachaussage=False), AUFTRAG, KONTEXT))


def test_zu_kurzer_text_faellt_durch():
    f = szene_pruefen(SZENE(sprechtext="Kurz. Zu kurz.", bild=BILD), AUFTRAG, KONTEXT)
    assert any("Wörter" in x for x in f)


def test_bildplan_fehler_werden_gemeldet():
    f = szene_pruefen(SZENE(bild={"schritte": [BILD["schritte"][0]]}), AUFTRAG, KONTEXT)
    assert any("Schritte" in x for x in f)
    assert any("Bildart" in x for x in szene_pruefen(SZENE(bildart="x"), AUFTRAG, KONTEXT))


def test_kaputtes_json_none():
    assert szenen_lesen("kein json") is None
    assert szenen_lesen('{"szenen": [{"nr": 1}]}') is None


def test_szenen_lesen_form():
    roh = {"szenen": [{"nr": 3, "sprechtext": LANG, "bildart": "slide", "bild": BILD,
                       "belege": [{"aussage": "a", "quelle": "auftrag"}, "kaputt"]}]}
    s = szenen_lesen("Hier:\n" + json.dumps(roh))[0]
    assert s["nr"] == 3 and s["uebergang"] == "schnitt" and s["aendert_text"] is True
    assert s["belege"] == [{"aussage": "a", "quelle": "auftrag"}] and s["ohne_sachaussage"] is False


def test_zeitmarken_je_stufe():
    gl = {"titel": "T", "hook": "Der Hook.", "stufen": [
        {"name": "Einstieg", "szenen": [{"kurz": "a"}, {"kurz": "b"}]},
        {"name": "Stufe 1", "szenen": [{"kurz": "c"}]}]}
    k = kopf(gl, [{"dauer_s": 20.2}, {"dauer_s": 21.9}, {"dauer_s": 30}])
    assert k["zeitmarken"][:2] == [{"zeit": "0:00", "name": "Einstieg"}, {"zeit": "0:42", "name": "Stufe 1"}]
    assert k["beschreibung"].endswith("Dieses Video wurde mit KI erstellt.") and "0:42 Stufe 1" in k["beschreibung"]


def test_brief_nennt_die_pflichtregeln():
    brief = (Path(video_texte.__file__).parent / "video_brief.md").read_text(encoding="utf-8")
    for wort in ("5 Sekunden", "Einwand", "Agenda", "Stufe", "ehrliche Grenzen", "190 bis 230"):
        assert wort in brief
    assert "HeyGen" not in brief and "Gewinnspiel" not in brief and len(brief.splitlines()) < 120


def test_prompts_tragen_regeln_brief_und_form():
    ein = {"thema": "Schulung", "beschreibung": "Beschreibung X", "minuten": 4, "zielgruppe": "Chefs"}
    system, nutzer = gliederung_prompt(ein, KONTEXT, "BRIEFTEXT", wunsch="kürzer", vorige={"titel": "Alt"})
    assert "BRIEFTEXT" in system and "dazu fehlt mir Material" in system and "Kundennamen" in system
    assert "### Marke.md" in nutzer and "kürzer" in nutzer and '"Alt"' in nutzer and "Beschreibung X" in nutzer
    gl, _ = gliederung_lesen(GLIEDERUNG_MIT("auftrag", 12), set())
    liste = szenen_liste(gl)
    assert liste[0]["nr"] == 1 and len(liste) == 12
    liste[0].update(wunsch="Bild anders", rueckgabe_von="Bild-Tor", sprechtext="ALTER TEXT", bild=BILD)
    system, nutzer = szenen_prompt(ein, gl, liste, KONTEXT, "BRIEFTEXT")
    assert "ALTER TEXT" in nutzer and "Bild-Tor" in nutzer and "Bild anders" in nutzer
    assert "Szene 10:" in nutzer and "Szene 11:" not in nutzer and BLOCK == 10
    assert "höchstens 60" in nutzer and "höchstens 80" in nutzer and "agenda" in nutzer and "aktiv" in nutzer
