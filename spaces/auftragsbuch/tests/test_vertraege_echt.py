"""Tests gegen die echten Vertraege unter spaces/_contract (nicht gegen Beispiele)."""
from pathlib import Path

import pytest
import yaml

from spaces.auftragsbuch.vertrag import FormFehler, lade_auftragsarten, pruefe_eingabe, pruefe_ergebnis

ORDNER = Path(__file__).resolve().parents[2] / "_contract"

PUNKT_OHNE_ENTWURF = {
    "punkt_id": "0123456789abcdef",
    "titel": "Rueckfrage zum Angebot beantworten",
    "frist": "2026-12-01",
    "link": "https://beispiel.invalid/sales/punkt/1",
    "entwurf_id": None,
}
PUNKT_MIT_ENTWURF = {
    **PUNKT_OHNE_ENTWURF,
    "punkt_id": "fedcba9876543210",
    "titel": "E-Mail an Beispiel Muster: Terminvorschlag",
    "frist": None,
    "entwurf_id": "entwurf-42",
}
EINGABE_PUNKT = {
    "punkt_id": "0123456789abcdef",
    "titel": "Rueckfrage zum Angebot beantworten",
    "frist": "2026-12-01",
    "link": "https://beispiel.invalid/sales/punkt/1",
}


def test_genau_die_zwei_ersten_auftragsarten():
    assert set(lade_auftragsarten(ORDNER)) == {("sales", "post_pruefen"), ("rowboat", "vorschlaege_anlegen")}


def test_werte_aus_dem_entwurf():
    arten = lade_auftragsarten(ORDNER)
    p, v = arten[("sales", "post_pruefen")], arten[("rowboat", "vorschlaege_anlegen")]
    assert (p.version, p.veraendert, p.frist_min, p.nachpruefung) == (1, True, 15, "beleg")
    assert (v.version, v.veraendert, v.frist_min, v.nachpruefung) == (1, True, 5, "beleg")


def test_post_pruefen_form():
    p = lade_auftragsarten(ORDNER)[("sales", "post_pruefen")]
    pruefe_eingabe(p, {"zeitraum": "heute", "entwuerfe_anlegen": True})
    pruefe_ergebnis(p, {"faellig": [PUNKT_MIT_ENTWURF, PUNKT_OHNE_ENTWURF], "anzahl_gelesen": 7, "kontakte_neu": 1})
    pruefe_ergebnis(p, {"faellig": [], "anzahl_gelesen": 0, "kontakte_neu": 0})
    for falsch in ({"zeitraum": "gestern", "entwuerfe_anlegen": True},
                   {"zeitraum": "heute"},
                   {"zeitraum": "heute", "entwuerfe_anlegen": True, "postfach": "x"}):
        with pytest.raises(FormFehler):
            pruefe_eingabe(p, falsch)
    with pytest.raises(FormFehler):      # Mail-Text ist kein Feld
        pruefe_ergebnis(p, {"faellig": [{**PUNKT_OHNE_ENTWURF, "text": "x"}], "anzahl_gelesen": 1, "kontakte_neu": 0})


def test_vorschlaege_anlegen_form():
    v = lade_auftragsarten(ORDNER)[("rowboat", "vorschlaege_anlegen")]
    pruefe_eingabe(v, {"punkte": [EINGABE_PUNKT]})
    pruefe_ergebnis(v, {"punkte": [{"punkt_id": "0123456789abcdef", "stand": "schon_vorhanden"}]})
    for falsch in ({"punkte": []},
                   {"punkte": [{**EINGABE_PUNKT, "link": "https://beispiel.invalid/freigaben#fg-1"}]},
                   {"punkte": [{**EINGABE_PUNKT, "punkt_id": "kurz"}]}):
        with pytest.raises(FormFehler):
            pruefe_eingabe(v, falsch)


def test_sales_vertrag_hat_noch_keine_capabilities():
    roh = yaml.safe_load((ORDNER / "sales.contract.yaml").read_text(encoding="utf-8"))
    assert roh["id"] == "sales" and roh["prefixes"] == ["sales."] and roh["capabilities"] == []
