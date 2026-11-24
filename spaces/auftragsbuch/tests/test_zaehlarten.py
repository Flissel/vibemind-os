"""Die drei Zaehlarten von Sales (nur lesen, nur Zahlen) gegen den echten Vertrag."""
from pathlib import Path

import pytest

from spaces.auftragsbuch.vertrag import FormFehler, art_hash, lade_auftragsarten, pruefe_eingabe, pruefe_ergebnis

ORDNER = Path(__file__).resolve().parents[2] / "_contract"
ARTEN = lade_auftragsarten(ORDNER)

PRUEFSUMMEN = {
    "termine_heute": "badeaa3eada323bdf0086ce88d86b0209b8e8e03e0be6154188e97828a9eaa2e",
    "freigaben_zaehlen": "74c669057e253527c633f03a2ed4c7d03010c7819a9e1f39fab9687a7998b98e",
    "post_offen": "90430f4e8be3989f2d4d3ec82518a5437f41601d37631d4139c6042a5a392771",
}
GUELTIG = {
    "termine_heute": {"anzahl": 3, "anzahl_kunden": 1, "quellen_stumm": 0},
    "freigaben_zaehlen": {"wartend": 2, "fehlgeschlagen": 0},
    "post_offen": {"chats_unbeantwortet": 4, "mails_offen": 9},
}


@pytest.mark.parametrize("name", sorted(PRUEFSUMMEN))
def test_art_hash_festgehalten(name):
    a = ARTEN[("sales", name)]
    assert a.version == 1
    assert art_hash(a) == PRUEFSUMMEN[name]


@pytest.mark.parametrize("name", sorted(PRUEFSUMMEN))
def test_nur_lesen_und_nur_zahlen(name):
    a = ARTEN[("sales", name)]
    assert a.veraendert is False and a.nachpruefung == "form"
    assert a.eingabe == {"type": "object", "additionalProperties": False, "properties": {}}
    assert a.ergebnis["additionalProperties"] is False
    felder = a.ergebnis["properties"]
    assert felder and set(a.ergebnis["required"]) == set(felder)
    for feld in felder.values():
        assert feld["type"] == "integer"
        assert feld["minimum"] == 0
        assert isinstance(feld["maximum"], int) and feld["maximum"] > 0


@pytest.mark.parametrize("name", sorted(PRUEFSUMMEN))
def test_eingabe_leer_ja_zusatzfeld_nein(name):
    a = ARTEN[("sales", name)]
    pruefe_eingabe(a, {})
    with pytest.raises(FormFehler):
        pruefe_eingabe(a, {"x": 1})


@pytest.mark.parametrize("name", sorted(PRUEFSUMMEN))
def test_ergebnis_form(name):
    a = ARTEN[("sales", name)]
    pruefe_ergebnis(a, GUELTIG[name])
    feld = next(iter(GUELTIG[name]))
    with pytest.raises(FormFehler):      # Text statt Zahl
        pruefe_ergebnis(a, {**GUELTIG[name], feld: "drei"})
    with pytest.raises(FormFehler):      # negativ
        pruefe_ergebnis(a, {**GUELTIG[name], feld: -1})
    with pytest.raises(FormFehler):      # ueber der Obergrenze
        pruefe_ergebnis(a, {**GUELTIG[name], feld: a.ergebnis["properties"][feld]["maximum"] + 1})
    with pytest.raises(FormFehler):      # Zusatzfeld
        pruefe_ergebnis(a, {**GUELTIG[name], "titel": "x"})
