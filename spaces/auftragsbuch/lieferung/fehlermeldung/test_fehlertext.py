"""Tests fuer fehlertext.py. Eigenstaendig lauffaehig: pytest <diese Datei>."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fehlertext import fehlertext  # noqa: E402


def _ergebnis(text: str) -> dict:
    return {"isError": True, "content": [{"type": "text", "text": text}]}


def test_fehlertext_nennt_dienst():
    f = lambda t: fehlertext("auftragsbuch", "auftrag_probe", _ergebnis(t))  # noqa: E731
    assert f("unerwartete Antwort (502)") == "auftragsbuch: unerwartete Antwort (502)"
    assert f("auftragsbuch: unerwartete Antwort (502)") == "auftragsbuch: unerwartete Antwort (502)"
    assert f("auftragsbuch nicht erreichbar") == "auftragsbuch nicht erreichbar"
    assert f("auftragsbuch: Schl\u00fcssel abgelehnt") == "auftragsbuch: Schl\u00fcssel abgelehnt"
    assert f("auftragsbuch: Zertifikat nicht pruefbar") == "auftragsbuch: Zertifikat nicht pruefbar"
    assert f("Unbekannte Auftragsart") == "auftragsbuch: Unbekannte Auftragsart"
    assert f("Ergebnis in falscher Form") == "auftragsbuch: Ergebnis in falscher Form"


@pytest.mark.parametrize("text", [
    "Unbekannte Auftragsart", "Auftrag braucht Laden und Mandant", "Frist ausserhalb 1 bis 120 Minuten",
    "Plan-Kennung ungueltig", "Tagesgrenze erreicht: hoechstens 50 Proben pro Tag",
    "Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag",
])
def test_fehlertext_ablehnungen_der_migration(text):
    assert fehlertext("auftragsbuch", "x", _ergebnis(text)) == f"auftragsbuch: {text}"


def test_fehlertext_fremder_text_bleibt_verborgen():
    for text in ("Mail an Anna Beispiel fehlgeschlagen", "interner Fehler", "nicht gefunden",
                 "Unbekannte Auftragsart; DROP", "unerwartete Antwort (5023)"):
        assert (fehlertext("auftragsbuch", "auftrag_probe", _ergebnis(text))
                == "auftragsbuch/auftrag_probe: Werkzeug meldet einen Fehler")


def test_fehlertext_form_der_jsonrpc_fehlers():
    # so kommt ein Fehler der Bruecke an (error-Objekt der JSON-RPC-Antwort)
    fehler = {"code": -32000, "message": "auftragsbuch nicht erreichbar"}
    assert fehlertext("auftragsbuch", "auftrag_lesen", fehler) == "auftragsbuch nicht erreichbar"
    umhuellt = {"error": fehler}
    assert fehlertext("auftragsbuch", "auftrag_lesen", umhuellt) == "auftragsbuch nicht erreichbar"


def test_fehlertext_mehrere_textteile_nimmt_den_ersten():
    e = {"isError": True, "content": [{"type": "image"}, {"type": "text", "text": "Plan-Kennung ungueltig"}]}
    assert fehlertext("auftragsbuch", "t", e) == "auftragsbuch: Plan-Kennung ungueltig"


@pytest.mark.parametrize("kaputt", [None, 5, "text", [], {}, {"content": None}, {"content": [None]},
                                    {"content": [{"text": 7}]}, {"error": 3}, {"message": ["x"]}])
def test_fehlertext_wirft_nie(kaputt):
    assert fehlertext("auftragsbuch", "auftrag_lesen", kaputt) == (
        "auftragsbuch/auftrag_lesen: Werkzeug meldet einen Fehler")
