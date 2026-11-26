"""Verlinkung der Videos: Folie mit drei Ausgaengen und Karte "Mehr dazu im Video"."""
import pytest

from spaces.marketing.claw import video_texte
from spaces.marketing.claw.tests.slide_hilfen import S, html_fuer
from spaces.marketing.claw.video_slides import bild_pruefen, komposition, zeitplan
from spaces.marketing.workers.video_stimme import Satz

SAETZE = [Satz("Eins.", 0.0, 2.0), Satz("Zwei.", 2.2, 2.0), Satz("Drei.", 4.4, 2.0), Satz("Vier.", 6.6, 2.0)]


def test_drei_karten_in_fester_reihenfolge_mit_zeitmarke():
    html = html_fuer([S("titel", 0), S("ausgaenge", 1, zurueck_bei="4:10")], saetze=SAETZE, dauer=9.0)
    assert "t-ausgaenge" in html and html.count('class="ausgang ') == 3
    assert html.index("ausgang tiefer") < html.index("ausgang seitlich") < html.index("ausgang zurueck")
    for text in ("Chunking am Beispiel", "Embeddings erklärt", "Der Roast", "Wie geht es weiter?", "ab 4:10",
                 "Tiefer", "Daneben", "Zurück"):
        assert text in html, text


def test_ohne_zeitmarke_keine_zeile():
    assert 'class="bei"' not in html_fuer([S("titel", 0), S("ausgaenge", 1)], saetze=SAETZE, dauer=9.0)
    assert 'class="bei"' not in html_fuer([S("titel", 0), S("ausgaenge", 1, zurueck_bei="")], saetze=SAETZE, dauer=9.0)


def test_karten_erscheinen_an_den_satzgrenzen():
    plan = zeitplan({"schritte": [S("titel", 0), S("ausgaenge", 1)]}, SAETZE, 9.0)
    assert plan[1].einblendungen == [4.4, 6.6]
    html = html_fuer([S("titel", 0), S("ausgaenge", 1)], saetze=SAETZE, dauer=9.0)
    assert "--d:0.45s" in html and "--d:2.20s" in html and "--d:4.40s" in html   # relativ zum Schritt (Start 2,2 s)


@pytest.mark.parametrize("felder, wort", [
    ({"tiefer": ""}, "tiefer fehlt"), ({"seitlich": None}, "seitlich fehlt"), ({"zurueck": "x" * 61}, "zu lang"),
    ({"ueberschrift": ""}, "ueberschrift fehlt"), ({"zurueck_bei": "bald"}, "Zeitmarke"), ({"zurueck_bei": "4:70"}, "Zeitmarke"),
    ({"zurueck_bei": 410}, "Zeitmarke"), ({"mehr": "Noch ein Video"}, "mehr gibt es")])
def test_falsche_ausgaenge(felder, wort):
    fehler = bild_pruefen({"schritte": [S("titel", 0), S("ausgaenge", 1, **felder)]}, 4)
    assert len(fehler) == 1 and wort in fehler[0] and fehler[0].startswith("Schritt 2,")


@pytest.mark.parametrize("marke", ["0:05", "4:10", "12:59", "1:02:03"])
def test_gueltige_zeitmarken(marke):
    assert bild_pruefen({"schritte": [S("titel", 0), S("ausgaenge", 1, zurueck_bei=marke)]}, 4) == []


def test_karte_mehr_dazu_im_video():
    html = html_fuer([S("titel", 0), S("stichpunkte", 1, mehr="Chunking erklärt <b>")], saetze=SAETZE, dauer=9.0)
    assert html.count('class="mehr"') == 1 and "Mehr dazu im Video" in html
    assert "Chunking erklärt &lt;b&gt;" in html and "erklärt <b>" not in html
    assert html.index('id="schritt2"') < html.index('class="mehr"')          # die Karte steht im zweiten Schritt
    assert 'class="mehr"' not in html_fuer([S("titel", 0), S("stichpunkte", 1)], saetze=SAETZE, dauer=9.0)


@pytest.mark.parametrize("wert, wort", [("", "mehr fehlt"), (None, "mehr fehlt"), (7, "mehr fehlt"), ("x" * 61, "zu lang")])
def test_falsches_mehr(wert, wort):
    fehler = bild_pruefen({"schritte": [S("titel", 0), S("zahl", 1, mehr=wert)]}, 4)
    assert len(fehler) == 1 and wort in fehler[0]


def test_figur_bleibt_klein_neben_den_karten():
    erklaer = {"firma": "Pexon", "titel": "Chunking", "stufe": "Ausblick", "stufe_nr": 4, "stufen": 4,
               "figur": "tux", "figur_name": "Tux", "figur_gross": True}
    def html(schritte):
        return komposition({"bildart": "slide", "variante": 0}, zeitplan({"schritte": schritte}, SAETZE, 9.0), {},
                           "ton.wav", 9.0, erklaer=erklaer)
    assert 'class="figur gross"' in html([S("titel", 0), S("stichpunkte", 1)])
    mit = html([S("titel", 0), S("ausgaenge", 1)])
    assert 'class="figur"' in mit and 'class="figur gross"' not in mit
    assert erklaer["figur_gross"] is True                                    # die Eingabe bleibt unveraendert


def test_zeitmarke_braucht_keinen_beleg_der_titel_schon():
    bild = {"schritte": [S("titel", 0), S("ausgaenge", 1, zurueck_bei="4:10", tiefer="In 5 Schritten")]}
    texte = video_texte._bild_texte(bild)
    assert "4:10" not in texte and "In 5 Schritten" in texte and "Der Roast" in texte


def test_modell_kennt_ausgaenge_und_mehr_nur_mit_konzept():
    regeln = video_texte._bildregeln(True)
    assert "- ausgaenge:" in regeln and "zurueck_bei" in regeln and "mehr (Titel" in regeln
    assert "Erfinde keine Videotitel" in regeln and "Alle anderen Felder sind Pflicht" in regeln
    ohne = video_texte._bildregeln()
    assert "ausgaenge" not in ohne and "mehr (Titel" not in ohne and "Alle Felder sind Pflicht" in ohne


def test_szenen_prompt_nennt_die_verlinkung_nur_bei_einem_konzept():
    gliederung = {"titel": "T", "hook": "H", "stufen": [{"name": "Einstieg", "szenen": [
        {"nr": 1, "kurz": "Erste", "bildart": "slide", "quellen": []}]}]}
    kontext = video_texte.Kontext([], [])
    ohne = video_texte.szenen_prompt({"thema": "Thema"}, gliederung, video_texte.szenen_liste(gliederung), kontext, "Brief")[1]
    konzept = {"thema": "Thema", "hook": "H", "kernaussage": "K", "abschluss": "A", "stufen": [
        {"name": "Einstieg", "szenen": [{"kurz": "Erste", "bildart": "slide", "notiz": ""}]}]}
    mit = video_texte.szenen_prompt({"thema": "Thema", "konzept": konzept}, gliederung,
                                    video_texte.szenen_liste(gliederung), kontext, "Brief")[1]
    assert "- ausgaenge:" not in ohne and "- ausgaenge:" in mit and "Mehr dazu im Video" in mit


def test_langes_wort_im_titel_wird_kleiner_statt_umzubrechen():
    html = html_fuer([S("titel", 0), S("ausgaenge", 1, zurueck="Roast: la_fungus_search")], saetze=SAETZE, dauer=9.0)
    assert 'style="--s:0.75">Roast: la_fungus_search' in html and 'style="--s:1.00">Embeddings erklärt' in html
