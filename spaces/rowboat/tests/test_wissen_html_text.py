"""Tests fuer den sichtbaren Text von HTML-Konzepten (Titel + Text ohne Skripte, Stile, Kommentare)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
