"""Tests fuer das Laden und Pruefen der Auftragsarten aus den Space-Vertraegen."""
from pathlib import Path

import pytest
import yaml

from spaces.auftragsbuch.vertrag import (
    FormFehler,
    VertragsFehler,
    lade_auftragsarten,
    pruefe_eingabe,
    vertrag_hash,
    werkzeugname,
)

GUELTIG = {
    "art": "post_pruefen",
    "version": 1,
    "veraendert": True,
    "frist_min": 15,
    "nachpruefung": "beleg",
    "nachpruefung_grund": "",
    "beschreibung": "Prueft den Posteingang und legt auf Wunsch Entwuerfe an.",
    "eingabe": {
        "type": "object",
        "additionalProperties": False,
        "required": ["zeitraum", "entwuerfe_anlegen"],
        "properties": {
            "zeitraum": {"type": "string", "enum": ["heute"]},
            "entwuerfe_anlegen": {"type": "boolean"},
        },
    },
    "ergebnis": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"anzahl": {"type": "integer"}},
    },
}


def schreibe(ordner: Path, space: str, arten: list | None) -> None:
    vertrag: dict = {"id": space, "prefixes": [space], "capabilities": []}
    if arten is not None:
        vertrag["auftragsarten"] = arten
    (ordner / f"{space}.contract.yaml").write_text(
        yaml.safe_dump(vertrag, allow_unicode=True), encoding="utf-8"
    )


def test_laedt_eine_auftragsart(tmp_path):
    schreibe(tmp_path, "demo", [GUELTIG])
    arten = lade_auftragsarten(tmp_path)
    a = arten[("demo", "post_pruefen")]
    assert (a.version, a.veraendert, a.frist_min, a.nachpruefung) == (1, True, 15, "beleg")
    assert werkzeugname(a) == "demo_post_pruefen"


def test_vertrag_ohne_auftragsarten_ist_leer(tmp_path):
    schreibe(tmp_path, "demo", None)
    assert lade_auftragsarten(tmp_path) == {}


@pytest.mark.parametrize("aenderung, teil", [
    ({"art": "Post"}, "art"),
    ({"version": 0}, "version"),
    ({"frist_min": 121}, "frist_min"),
    ({"nachpruefung": "vertrauen"}, "nachpruefung"),
    ({"nachpruefung": "form", "nachpruefung_grund": ""}, "nachpruefung_grund"),
    ({"eingabe": {"type": "object"}}, "additionalProperties"),
    ({"ergebnis": {"type": "array"}}, "ergebnis"),
    ({"eingabe": {"type": "object", "additionalProperties": False,
                  "properties": {"x": {"type": "zahl"}}}}, "eingabe"),
])
def test_luecken_im_vertrag_scheitern_mit_datei_und_art(tmp_path, aenderung, teil):
    schreibe(tmp_path, "demo", [{**GUELTIG, **aenderung}])
    with pytest.raises(VertragsFehler) as e:
        lade_auftragsarten(tmp_path)
    assert "demo.contract.yaml" in str(e.value) and teil in str(e.value)


def test_doppelte_art_scheitert(tmp_path):
    schreibe(tmp_path, "demo", [GUELTIG, GUELTIG])
    with pytest.raises(VertragsFehler):
        lade_auftragsarten(tmp_path)


def test_bindestrich_im_space_wird_unterstrich(tmp_path):
    schreibe(tmp_path, "event-management", [GUELTIG])
    a = lade_auftragsarten(tmp_path)[("event-management", "post_pruefen")]
    assert werkzeugname(a) == "event_management_post_pruefen"


def test_form_pruefung_nennt_keine_werte(tmp_path):
    schreibe(tmp_path, "demo", [GUELTIG])
    a = lade_auftragsarten(tmp_path)[("demo", "post_pruefen")]
    pruefe_eingabe(a, {"zeitraum": "heute", "entwuerfe_anlegen": True})
    with pytest.raises(FormFehler) as e:
        pruefe_eingabe(a, {"zeitraum": "geheim-123", "entwuerfe_anlegen": True})
    assert "geheim-123" not in str(e.value)


def test_hash_ist_stabil_und_aendert_sich_mit_der_form(tmp_path):
    schreibe(tmp_path, "demo", [GUELTIG])
    h1 = vertrag_hash(lade_auftragsarten(tmp_path))
    assert h1 == vertrag_hash(lade_auftragsarten(tmp_path))
    schreibe(tmp_path, "demo", [{**GUELTIG, "version": 2}])
    assert vertrag_hash(lade_auftragsarten(tmp_path)) != h1
