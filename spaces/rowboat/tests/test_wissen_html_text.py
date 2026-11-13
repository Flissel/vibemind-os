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
