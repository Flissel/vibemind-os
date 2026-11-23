"""belege: Kennungs-Erkennung und Beleg-Entscheidung."""
from __future__ import annotations

from pathlib import Path

import pytest

from spaces.auftragsbuch.belege import _kennung_in, beleg
from spaces.auftragsbuch.vertrag import lade_auftragsarten

ARTEN = lade_auftragsarten(Path(__file__).resolve().parents[2] / "_contract")
ART = ARTEN[("rowboat", "vorschlaege_anlegen")]
P = "0123456789abcdef"


def test_kennung_nur_am_textende_klein_und_zusammengefasst():
    assert _kennung_in(f"Text  \n #{P.upper()}  ") == P
    assert _kennung_in(f"Text #{P} danach") is None
    assert _kennung_in("Text #abc") is None
    assert _kennung_in(None) is None


def test_liste_und_vorschlaege_zaehlen_und_unterpunkte():
    todo = {"suggestions": [f"A #{P}"],
            "list": {"blocks": [{"kind": "item", "item": {"key": "1", "text": "o", "checked": True,
                                 "children": [{"key": "2", "text": f"B #{'b' * 16}", "checked": False}]}},
                                {"kind": "text"}]}}
    erg = {"punkte": [{"punkt_id": P, "stand": "neu"}, {"punkt_id": "b" * 16, "stand": "neu"}]}
    assert beleg(ART, {}, erg, lambda k, b: todo) == "bestaetigt"


def test_schon_vorhanden_und_abgelehnt_gelten_als_bestaetigt():
    erg = {"punkte": [{"punkt_id": P, "stand": "schon_vorhanden"},
                      {"punkt_id": "a" * 16, "stand": "frueher_abgelehnt"}]}
    assert beleg(ART, {}, erg, lambda k, b: pytest.fail("kein Lesen noetig")) == "bestaetigt"


def test_unerwartete_form_ist_nicht_pruefbar():
    erg = {"punkte": [{"punkt_id": P, "stand": "neu"}]}
    assert beleg(ART, {}, erg, lambda k, b: {"list": 1}) == "nicht_pruefbar"
    assert beleg(ART, {}, erg, lambda k, b: []) == "nicht_pruefbar"
