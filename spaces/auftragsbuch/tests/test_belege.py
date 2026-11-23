"""belege: Kennungs-Erkennung und Beleg-Entscheidung."""
from __future__ import annotations

from pathlib import Path

import pytest

from spaces.auftragsbuch.belege import _kennung_in, beleg
from spaces.auftragsbuch.vertrag import lade_auftragsarten

ARTEN = lade_auftragsarten(Path(__file__).resolve().parents[2] / "_contract")
ART = ARTEN[("rowboat", "vorschlaege_anlegen")]
P = "0123456789abcdef"


def _ein(*ids):
    return {"punkte": [{"punkt_id": i, "titel": "t", "frist": None, "link": ""} for i in ids]}


def _leser(todo):
    def leser(kanal, koerper):
        assert kanal == "todo:get" and koerper is None  # Rowboat: req ist z.null()
        return todo
    return leser


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
    assert beleg(ART, _ein(P, "b" * 16), erg, _leser(todo)) == "bestaetigt"


def test_schon_vorhanden_und_abgelehnt_gelten_als_bestaetigt():
    erg = {"punkte": [{"punkt_id": P, "stand": "schon_vorhanden"},
                      {"punkt_id": "a" * 16, "stand": "frueher_abgelehnt"}]}
    assert beleg(ART, _ein(P, "a" * 16), erg, lambda k, b: pytest.fail("kein Lesen noetig")) == "bestaetigt"


def test_unerwartete_form_ist_nicht_pruefbar():
    erg = {"punkte": [{"punkt_id": P, "stand": "neu"}]}
    assert beleg(ART, _ein(P), erg, _leser({"list": 1})) == "nicht_pruefbar"
    assert beleg(ART, _ein(P), erg, _leser([])) == "nicht_pruefbar"


@pytest.mark.parametrize("ids", [[], ["b" * 16], [P, "b" * 16], [P, P]])
def test_ids_muessen_genau_zur_eingabe_passen(ids):
    erg = {"punkte": [{"punkt_id": i, "stand": "schon_vorhanden"} for i in ids]}
    assert beleg(ART, _ein(P), erg, lambda k, b: pytest.fail("kein Lesen")) == "nicht_bestaetigt"


def test_fehler_des_lesers_wird_ohne_inhalt_geloggt(caplog):
    from spaces.auftragsbuch.rowboat.rpc import RowboatFehler

    def leser(k, b):
        raise RowboatFehler("Rowboat nicht erreichbar")

    erg = {"punkte": [{"punkt_id": P, "stand": "neu"}]}
    with caplog.at_level("INFO", logger="auftragsbuch"):
        assert beleg(ART, _ein(P), erg, leser, auftrag="abcd1234") == "nicht_pruefbar"
    assert "Rowboat nicht erreichbar" in caplog.text and "abcd1234" in caplog.text
