"""Wurzel ausdruecklich setzbar (White-Paper-Zusatz 1). Ohne Netz; Namen erfunden."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import ideen_abkuerzung as abk  # noqa: E402
from core import ideen_dokument as dok  # noqa: E402
from core.ideen_fest_contract import feste_argumente, nachpruefung, pruefe_zustand  # noqa: E402

KONTEXT = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"},
           "cards": [{"id": "a0000001", "title": "Bühnenplan"}, {"id": "a0000002", "title": "Sponsoren"}]}


@pytest.fixture(autouse=True)
def _leer():
    abk._OFFEN.clear()
    yield
    abk._OFFEN.clear()


@pytest.mark.parametrize("satz", ["Mach Sponsoren zur Wurzel", "Mach die Notiz Sponsoren zur Wurzel dieser Bubble",
                                  "Setze Sponsoren als Wurzel fest"])
def test_satz_setzt_die_wurzel(satz) -> None:
    treffer = abk.loese(satz, KONTEXT)
    assert treffer["ereignis"] == "idea.root"
    assert treffer["argumente"] == {"bubble_id": "b0b1e001", "id": "a0000002"}
    assert feste_argumente("idea.root", json.dumps(treffer["argumente"]))[0] is not None


def test_satz_loescht_die_wurzel() -> None:
    treffer = abk.loese("Lösche die festgelegte Wurzel", KONTEXT)
    assert treffer["ereignis"] == "idea.root" and treffer["argumente"] == {"bubble_id": "b0b1e001"}


def _quittung(inhalt):
    return {"content": [{"type": "text", "text": json.dumps(inhalt)}], "isError": False}


def test_nachpruefung_liest_metadata_wurzel() -> None:
    argumente = {"bubble_id": "b0b1e001", "id": "a0000002"}
    erwartet, grund = nachpruefung("idea.root", argumente, _quittung({"bubble_id": "b0b1e001", "wurzel": "a0000002"}))
    assert grund == "" and erwartet["felder"] == {"metadata.wurzel": "a0000002"}
    gut = pruefe_zustand(erwartet, lambda t, f: [{"id": "b0b1e001", "metadata": {"wurzel": "a0000002"}}])
    schlecht = pruefe_zustand(erwartet, lambda t, f: [{"id": "b0b1e001", "metadata": {}}])
    assert gut[0] is True and schlecht[0] is False
    falsch, _ = nachpruefung("idea.root", argumente, _quittung({"bubble_id": "b0b1e001", "wurzel": "a0000001"}))
    assert falsch is None
    leer, _ = nachpruefung("idea.root", {"bubble_id": "b0b1e001"}, _quittung({"bubble_id": "b0b1e001", "wurzel": None}))
    assert pruefe_zustand(leer, lambda t, f: [{"id": "b0b1e001", "metadata": {}}])[0] is True


def _karte(i, titel, y):
    return {"id": i, "title": titel, "x": 0, "y": y}


KARTEN = [_karte("a0000001", "Bühnenplan", 0), _karte("a0000002", "Sponsoren", 100), _karte("a0000003", "Zelt", 200)]
KANTEN = [{"from_node_id": "a0000001", "to_node_id": "a0000002", "edge_type": "parent_child"},
          {"from_node_id": "a0000002", "to_node_id": "a0000003", "edge_type": "parent_child"}]


def test_gesetzte_wurzel_geht_der_berechneten_vor() -> None:
    berechnet = dok.struktur(KARTEN, KANTEN)
    assert berechnet["wurzel"] == "a0000001" and berechnet["art"] == "berechnet"
    gesetzt = dok.struktur(KARTEN, KANTEN, wurzel_gesetzt="a0000002")
    assert gesetzt["wurzel"] == "a0000002" and gesetzt["art"] == "gesetzt"
    assert sorted(i for z in gesetzt["zweige"] for i in z) == ["a0000001", "a0000003"]
    fremd = dok.struktur(KARTEN, KANTEN, wurzel_gesetzt="a0000009")      # nicht (mehr) in der Bubble
    assert fremd["wurzel"] == "a0000001" and fremd["art"] == "berechnet"


def test_gesetzte_wurzel_ohne_verbindungen_ordnet_den_rest_nach_lage() -> None:
    bau = dok.struktur(KARTEN, [], wurzel_gesetzt="a0000003")
    assert bau["wurzel"] == "a0000003" and bau["zweige"] == [["a0000001", "a0000002"]] and bau["anhang"] == []


def test_vorspann_sagt_von_wo_und_ob_gesetzt() -> None:
    assert dok.wurzel_satz({"wurzel_id": "a0000002", "wurzel_titel": "Sponsoren", "wurzel_art": "gesetzt"}) == (
        "Es beginnt bei der Karte „Sponsoren“, die du als Wurzel festgelegt hast.")
    assert dok.wurzel_satz({"wurzel_id": "a0000001", "wurzel_titel": "Bühnenplan", "wurzel_art": "berechnet"}) == (
        "Es beginnt bei der Karte „Bühnenplan“, die ich berechnet habe.")
    assert "liegt nicht mehr" in dok.wurzel_satz({"wurzel_id": "a0000001", "wurzel_titel": "B",
                                                   "wurzel_art": "berechnet", "wurzel_fehlt": True})
    assert dok.wurzel_satz({"wurzel_id": None}) == ""
    assert dok.gesetzte_wurzel({"metadata": {"wurzel": "a0000002"}}) == "a0000002"
    assert dok.gesetzte_wurzel({"metadata": {"wurzel": "x"}}) is None
