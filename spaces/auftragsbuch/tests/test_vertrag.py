"""Tests fuer das Laden und Pruefen der Auftragsarten aus den Space-Vertraegen."""
from pathlib import Path

import pytest
import yaml

from spaces.auftragsbuch.vertrag import (
    FormFehler,
    VertragsFehler,
    art_hash,
    lade_auftragsarten,
    pruefe_eingabe,
    pruefe_ergebnis,
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


def _offen(innen: dict) -> dict:
    """Ergebnis-Schema mit einem verschachtelten Teil-Schema `innen` unter properties/faellig."""
    return {"type": "object", "additionalProperties": False, "properties": {"faellig": innen}}


_PUNKT_OFFEN = {"type": "object", "properties": {"titel": {"type": "string"}}}


@pytest.mark.parametrize("ergebnis, pfad", [
    (_offen({"type": "array", "items": _PUNKT_OFFEN}), "properties/faellig/items"),
    (_offen({"type": "array", "prefixItems": [{"type": "string"}, _PUNKT_OFFEN]}),
     "properties/faellig/prefixItems/1"),
    (_offen({"anyOf": [{"type": "null"}, _PUNKT_OFFEN]}), "properties/faellig/anyOf/1"),
    (_offen({"oneOf": [_PUNKT_OFFEN]}), "properties/faellig/oneOf/0"),
    (_offen({"allOf": [_PUNKT_OFFEN]}), "properties/faellig/allOf/0"),
    (_offen({"type": ["object", "null"], "properties": {"titel": {"type": "string"}}}),
     "properties/faellig"),
    (_offen({"type": "object", "additionalProperties": True}), "properties/faellig"),
    (_offen({"type": "object", "additionalProperties": False, "properties": {"tief": _PUNKT_OFFEN}}),
     "properties/faellig/properties/tief"),
])
def test_verschachtelt_offenes_objekt_scheitert_mit_datei_art_und_pfad(tmp_path, ergebnis, pfad):
    schreibe(tmp_path, "demo", [{**GUELTIG, "ergebnis": ergebnis}])
    with pytest.raises(VertragsFehler) as e:
        lade_auftragsarten(tmp_path)
    text = str(e.value)
    assert "demo.contract.yaml" in text and "post_pruefen" in text and "ergebnis" in text
    assert "additionalProperties" in text and f"'{pfad}'" in text


def test_offenes_objekt_unter_not_ist_erlaubt(tmp_path):
    ergebnis = _offen({"type": "string", "not": {"type": "object"}})
    schreibe(tmp_path, "demo", [{**GUELTIG, "ergebnis": ergebnis}])
    assert ("demo", "post_pruefen") in lade_auftragsarten(tmp_path)


def test_verschachtelt_geschlossen_laedt_und_haelt_den_mailtext_auf(tmp_path):
    punkt = {"type": "object", "additionalProperties": False, "properties": {"titel": {"type": "string"}}}
    ergebnis = _offen({"type": "array", "items": punkt})
    schreibe(tmp_path, "demo", [{**GUELTIG, "ergebnis": ergebnis}])
    a = lade_auftragsarten(tmp_path)[("demo", "post_pruefen")]
    pruefe_ergebnis(a, {"faellig": [{"titel": "t"}]})
    with pytest.raises(FormFehler) as e:
        pruefe_ergebnis(a, {"faellig": [{"titel": "t", "text": "ganzer Mail-Text"}]})
    assert "ganzer Mail-Text" not in str(e.value) and "'faellig/0'" in str(e.value)


def test_feldpfad_nennt_nur_schluessel_aus_dem_schema(tmp_path):
    eingabe = {"type": "object", "additionalProperties": False,
               "patternProperties": {"^x": {"type": "integer"}}}
    schreibe(tmp_path, "demo", [{**GUELTIG, "eingabe": eingabe}])
    a = lade_auftragsarten(tmp_path)[("demo", "post_pruefen")]
    with pytest.raises(FormFehler) as e:
        pruefe_eingabe(a, {"x-GEHEIMER-SCHLUESSEL": "kein int"})
    assert "GEHEIMER" not in str(e.value) and "Regel 'type'" in str(e.value)


def test_hash_ist_stabil_und_aendert_sich_mit_der_form(tmp_path):
    schreibe(tmp_path, "demo", [GUELTIG])
    h1 = vertrag_hash(lade_auftragsarten(tmp_path))
    assert h1 == vertrag_hash(lade_auftragsarten(tmp_path))
    schreibe(tmp_path, "demo", [{**GUELTIG, "version": 2}])
    assert vertrag_hash(lade_auftragsarten(tmp_path)) != h1


def test_lader_lehnt_array_ohne_items_ab(tmp_path):
    schreibe(tmp_path, "demo", [{**GUELTIG, "ergebnis": _offen({"type": "array"})}])
    with pytest.raises(VertragsFehler, match="array braucht items") as e:
        lade_auftragsarten(tmp_path)
    assert "demo.contract.yaml" in str(e.value) and "'properties/faellig'" in str(e.value)


def test_lader_lehnt_items_ohne_type_ab(tmp_path):
    schreibe(tmp_path, "demo", [{**GUELTIG, "ergebnis": _offen({"type": "array", "items": {"enum": ["a"]}})}])
    with pytest.raises(VertragsFehler, match="items braucht type") as e:
        lade_auftragsarten(tmp_path)
    assert "'properties/faellig/items'" in str(e.value)


def test_lader_lehnt_items_true_ab(tmp_path):
    schreibe(tmp_path, "demo", [{**GUELTIG, "ergebnis": _offen({"type": "array", "items": True})}])
    with pytest.raises(VertragsFehler, match="items darf nicht true sein"):
        lade_auftragsarten(tmp_path)


def test_lader_nimmt_array_mit_getyptem_items_an(tmp_path):
    ergebnis = _offen({"type": "array", "items": {"type": "string"}})
    schreibe(tmp_path, "demo", [{**GUELTIG, "ergebnis": ergebnis}])
    assert ("demo", "post_pruefen") in lade_auftragsarten(tmp_path)


def test_art_hash_ist_stabil_und_haengt_an_der_form(tmp_path):
    schreibe(tmp_path, "demo", [GUELTIG])
    a = lade_auftragsarten(tmp_path)[("demo", "post_pruefen")]
    assert art_hash(a) == art_hash(a) and len(art_hash(a)) == 64
    schreibe(tmp_path, "demo", [{**GUELTIG, "version": 2}])
    assert art_hash(lade_auftragsarten(tmp_path)[("demo", "post_pruefen")]) != art_hash(a)
