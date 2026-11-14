"""Tests fuer den sichtbaren Text von HTML-Konzepten (Titel + Text ohne Skripte, Stile, Kommentare)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from wissen_suche import html_text  # noqa: E402


def test_sichtbarer_text():
    html = "<title>Sommer &amp; Sonne</title><style>.a{}</style><script>var zebra=1</script><!-- x --><h1>Hallo</h1><p>Welt</p>"
    assert html_text.titel(html) == "Sommer & Sonne"
    assert html_text.sichtbarer_text(html) == "Sommer & Sonne Hallo Welt"


def test_daten_block_und_skript_bleiben_draussen():
    html = ('<script type="application/json" id="rowboat-konzept">{"geheim": "zebra"}</script>'
            "<p>Sichtbar\n\n  und   eng</p><script>var x = '<p>nein</p>'</script>")
    text = html_text.sichtbarer_text(html)
    assert text == "Sichtbar und eng"
    assert html_text.titel(html) is None


def test_leere_und_kaputte_eingaben():
    assert html_text.sichtbarer_text("") == ""
    assert html_text.titel("<p>kein titel</p>") is None
    assert html_text.sichtbarer_text("<p>offen <b>fett") == "offen fett"


def test_nur_die_ersten_1048576_zeichen_werden_gelesen():
    html = "<p>" + "a" * html_text.MAX_ZEICHEN + "</p><p>ende</p>"
    assert "ende" not in html_text.sichtbarer_text(html)


@pytest.mark.parametrize("muster,anzahl", [("<a ", 300_000), ("<!--", 250_000), ("<script ", 100_000), ("<", 1_000_000),
                                           ("<a b='", 150_000), ("<a b=\"x\" ", 100_000), ("<a b='x' <", 100_000)])
def test_giftige_eingaben_sind_linear(muster, anzahl):
    start = time.monotonic()
    html_text.lesen(muster * anzahl)
    assert time.monotonic() - start < 2


def test_gross_klein_entities_und_unvollstaendiges():
    html = "<TITLE>A &amp; B</TITLE><SCRIPT>x</SCRIPT><STYLE>y</STYLE>3 < 5 <b title=\"a>b\">fett</b><!-- c -->&lt;ok&gt;"
    assert html_text.lesen(html) == ("A & B", "A & B 3 < 5 fett <ok>")
    assert html_text.sichtbarer_text("<p>vor</p><script>nie fertig") == "vor"
    assert html_text.sichtbarer_text("<p>vor</p><!-- nie fertig") == "vor"


def test_zeichen_mit_laengerer_kleinschreibung_verschieben_nichts():
    # "İ".lower() (I mit Punkt) hat zwei Zeichen: Skriptinhalt darf trotzdem nicht in den Text geraten
    html = "İ" * 3 + "<script>geheim</script><p>sichtbar</p>"
    text = html_text.sichtbarer_text(html)
    assert "sichtbar" in text
    assert "geheim" not in text
    html = "İ" * 40 + "<title>T</title><style>.geheim{}</style><SCRIPT>geheim2</SCRIPT>ende"
    assert html_text.lesen(html) == ("T", "İ" * 40 + "T ende")


def test_schluss_tag_nur_in_ascii():
    # "</ſcript" (langes s) schliesst im Browser kein Skript
    text = html_text.sichtbarer_text("<script>a</ſcript> geheim</script><p>sichtbar</p>")
    assert text == "sichtbar"


def test_offener_titel_wird_gekappt():
    html = "<title>Anfang " + "wort " * 2000
    t = html_text.titel(html)
    assert t is not None and t.startswith("Anfang wort")
    assert len(t) <= html_text.TITEL_MAX


def test_einzelnes_kleiner_ohne_schluss_ist_text():
    # "it's" oeffnet im Browser keinen Attributwert; ein "<" ohne ">" verschluckt den Rest nicht
    assert html_text.sichtbarer_text("<p>Preis <b it's teuer</b> und mehr</p>") == "Preis und mehr"
    assert html_text.sichtbarer_text("<p>a <b x='offen ohne ende</p><p>danach</p>") == "a <b x='offen ohne ende danach"
    assert html_text.sichtbarer_text("<p>x <wort ohne ende") == "x <wort ohne ende"


def test_lange_data_bilder_im_attribut_bleiben_tag():
    bild = "data:image/png;base64," + "A" * 50_000
    html = f'<p>vor</p><img alt="b" src="{bild}"><p>nach</p>'
    assert html_text.sichtbarer_text(html) == "vor nach"


# --- Videokonzept: der Datenblock traegt den Text, die Seite wird per Skript gezeichnet -------------------

def _block(daten, attrs='type="application/json" id="rowboat-konzept"'):
    import json
    return f"<script {attrs}>{json.dumps(daten, ensure_ascii=False)}</script>"


_VIDEO = {
    "thema": "Quittenernte", "zielgruppe": "Hobbygaertner", "minuten": 4, "hook": "Hakenwort",
    "kernaussage": "Kernwort",
    "stufen": [
        {"name": "Stufeeins", "szenen": [{"kurz": "Kurzeins", "bildart": "slide", "notiz": "Quittenprobe"},
                                         {"kurz": "Kurzzwei", "bildart": "clip"}]},
        {"name": "Stufezwei", "szenen": [{"kurz": "Kurzdrei", "bildart": "avatar_gross", "notiz": "Notizdrei"}]},
    ],
    "abschluss": "Schlusswort", "auftrag": None,
}


def test_videokonzept_felder_stehen_in_reihenfolge_nach_dem_sichtbaren_text():
    html = "<p>Statisch</p>" + _block({"art": "videokonzept", "bezuege": ["[[Bezugwort]]"], "video": _VIDEO})
    zeilen = html_text.sichtbarer_text(html).split("\n")
    assert zeilen == ["Statisch", "Quittenernte", "Hobbygaertner", "Hakenwort", "Kernwort", "Stufeeins",
                      "Kurzeins", "Quittenprobe", "Kurzzwei", "Stufezwei", "Kurzdrei", "Notizdrei", "Schlusswort"]


def test_videokonzept_ohne_statischen_text():
    zeilen = html_text.sichtbarer_text(_block({"art": "videokonzept", "bezuege": [], "video": _VIDEO})).split("\n")
    assert zeilen[0] == "Quittenernte" and zeilen[-1] == "Schlusswort"


def test_videokonzept_bezuege_bildart_minuten_sind_kein_text():
    text = html_text.sichtbarer_text(_block({"art": "videokonzept", "bezuege": ["[[Bezugwort]]"], "video": _VIDEO}))
    for nein in ("Bezugwort", "avatar_gross", "slide", "clip", "videokonzept"):
        assert nein not in text
    assert "4" not in text.split()


def test_videokonzept_block_ueber_20000_zeichen_fuegt_nichts_hinzu():
    video = dict(_VIDEO, hook="x" * 19_990)
    import json
    zu_lang = json.dumps({"art": "videokonzept", "bezuege": [], "video": video})
    assert len(zu_lang) > 20_000
    assert html_text.sichtbarer_text("<p>A</p><script type=\"application/json\" id=\"rowboat-konzept\">" + zu_lang
                                     + "</script>") == "A"
    knapp = {"art": "videokonzept", "bezuege": [], "video": {"hook": "y" * 100}}
    assert "y" * 100 in html_text.sichtbarer_text(_block(knapp))


def test_videokonzept_genau_20000_zeichen_zaehlen():
    import json
    leer = json.dumps({"art": "videokonzept", "bezuege": [], "video": {"hook": ""}})
    fuell = 20_000 - len(leer)
    block = json.dumps({"art": "videokonzept", "bezuege": [], "video": {"hook": "z" * fuell}})
    assert len(block) == 20_000
    html = '<script type="application/json" id="rowboat-konzept">' + block + "</script>"
    assert html_text.sichtbarer_text(html) == "z" * fuell
    html2 = '<script type="application/json" id="rowboat-konzept">' + block + " </script>"
    assert html_text.sichtbarer_text(html2) == ""


def test_videokonzept_kaputtes_json_und_fehlendes_video_fuegt_nichts_hinzu():
    kopf = '<p>A</p><script type="application/json" id="rowboat-konzept">'
    for roh in ('{"art": "videokonzept", "video": {"hook": "Zebra"', "nicht json", "", "[1,2]", "null",
                '{"art": "konzept", "bezuege": []}', '{"video": "Zebra"}', '{"video": ["Zebra"]}', '{"video": null}'):
        assert html_text.sichtbarer_text(kopf + roh + "</script>") == "A"


def test_videokonzept_nur_der_erste_block_zaehlt():
    zwei = (_block({"art": "videokonzept", "bezuege": [], "video": {"hook": "Erster"}})
            + _block({"art": "videokonzept", "bezuege": [], "video": {"hook": "Zweiter"}}))
    assert html_text.sichtbarer_text(zwei) == "Erster"
    # ein erster, unbrauchbarer Block verbraucht den Platz: der zweite zaehlt nicht
    kaputt_dann_gut = ('<script type="application/json" id="rowboat-konzept">{kaputt</script>'
                       + _block({"art": "videokonzept", "bezuege": [], "video": {"hook": "Zweiter"}}))
    assert html_text.sichtbarer_text(kaputt_dann_gut) == ""


def test_videokonzept_andere_skripte_zaehlen_nicht():
    andere_id = _block({"video": {"hook": "Nein"}}, 'type="application/json" id="anderer"')
    anderer_typ = _block({"video": {"hook": "Nein"}}, 'type="text/javascript" id="rowboat-konzept"')
    ohne_typ = _block({"video": {"hook": "Nein"}}, 'id="rowboat-konzept"')
    assert html_text.sichtbarer_text(andere_id + anderer_typ + ohne_typ) == ""
    mit_parametern = _block({"video": {"hook": "Ja"}}, "ID='rowboat-konzept' TYPE=' Application/JSON ; charset=utf-8'")
    assert html_text.sichtbarer_text(mit_parametern) == "Ja"


def test_videokonzept_script_ende_im_wert_beendet_den_block():
    html = ('<script type="application/json" id="rowboat-konzept">{"video": {"hook": "Vorn</script>'
            '<p>Danach</p>", "kurz": "Hinten"}}</p>')
    text = html_text.sichtbarer_text(html)
    assert text == 'Danach ", "kurz": "Hinten"}}'      # Rest ist gewoehnlicher sichtbarer Text, kein Blocktext
    assert "Vorn" not in text


def test_videokonzept_nicht_text_werte_werden_uebersprungen():
    video = {"thema": 5, "zielgruppe": None, "hook": ["Liste"], "kernaussage": {"a": "Objekt"}, "stufen": "x",
             "abschluss": "Ende"}
    assert html_text.sichtbarer_text(_block({"video": video})) == "Ende"
    video = {"stufen": [7, {"name": 3, "szenen": "kaputt"}, {"name": "Gut", "szenen": [None, "s",
             {"kurz": 1, "notiz": True}, {"kurz": "Kurz", "notiz": None}]}], "hook": "H"}
    assert html_text.sichtbarer_text(_block({"video": video})) == "H\nGut\nKurz"


def test_videokonzept_felder_getrimmt_leere_uebersprungen_markup_bleibt_woertlich():
    video = {"thema": "  <b>fett</b> &amp;\n zwei  ", "zielgruppe": "   ", "hook": "", "abschluss": "Ende"}
    assert html_text.sichtbarer_text(_block({"video": video})) == "<b>fett</b> &amp; zwei\nEnde"


def test_videokonzept_hostile_eingabe_bleibt_linear():
    boese = '<script type="application/json" id="rowboat-konzept">' + '{"video":' * 2000 + "</script>"
    start = time.perf_counter()
    assert html_text.sichtbarer_text(boese * 20) == ""
    assert time.perf_counter() - start < 2
    tief = '<script id="rowboat-konzept" type="application/json">' + "[" * 19_000 + "</script>"
    assert html_text.sichtbarer_text(tief) == ""


def test_videokonzept_video_entscheidet_auch_ohne_passende_art():
    # Bewusst weicher als readKonzept (das art/bezuege verlangt): ein Objekt "video" genuegt fuer die Suche.
    assert html_text.sichtbarer_text(_block({"art": "konzept", "video": {"hook": "Hakenwort"}})) == "Hakenwort"
    assert html_text.sichtbarer_text(_block({"video": {"hook": "Hakenwort"}})) == "Hakenwort"


def test_videokonzept_offener_erster_block_fuegt_nichts_hinzu():
    offen = '<p>A</p><script type="application/json" id="rowboat-konzept">{"video": {"hook": "Zebra"}}'
    assert html_text.sichtbarer_text(offen) == "A"
    assert html_text.sichtbarer_text(offen + _block({"video": {"hook": "Nein"}})) == "A"


def test_einzelne_surrogate_kommen_nie_in_den_text():
    # ensure_ascii=True schreibt "\\ud800" als Escape in den Block; json.loads macht daraus ein einzelnes Surrogat
    roh = ('{"video": {"thema": "a\\ud800b", "hook": "\\udfffx", "stufen": [{"name": "n\\udc00", "szenen": '
           '[{"kurz": "k\\ud800", "notiz": "\\ud83d\\ude00 Paar"}]}]}}')
    text = html_text.sichtbarer_text('<script type="application/json" id="rowboat-konzept">' + roh + "</script>")
    text.encode("utf-8")                                        # wirft bei einem einzelnen Surrogat
    assert "a\ufffdb" in text and "\U0001F600 Paar" in text     # ein gueltiges Paar bleibt ein Zeichen


def test_zahlenverweis_auf_surrogat_im_statischen_text_ist_kein_surrogat():
    for html in ("<p>a&#xD800;b</p>", "<p>a&#55296;b</p>", "<title>t&#xDFFF;</title>"):
        html_text.sichtbarer_text(html).encode("utf-8")
        (html_text.titel(html) or "").encode("utf-8")
