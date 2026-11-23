"""Begruendung nennt Karten beim Titel -> idea_ids (White-Paper-Zusatz 2). Namen erfunden."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import ideen_abkuerzung as abk  # noqa: E402
from core.ideen_fest_contract import feste_argumente  # noqa: E402

KONTEXT = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"},
           "cards": [{"id": "a0000001", "title": "Sponsoren"}, {"id": "a0000002", "title": "Sponsoren Liste"},
                     {"id": "a0000003", "title": "Bühnenplan"}, {"id": "a0000004", "title": "Zelt"},
                     {"id": "a0000005", "title": "Zelt"}, {"id": "a0000006", "title": "Ab"}]}


@pytest.fixture(autouse=True)
def _leer():
    abk._OFFEN.clear()
    yield
    abk._OFFEN.clear()


def test_zwei_genannte_karten_landen_in_idea_ids() -> None:
    treffer = abk.loese("Begründung: Der Bühnenplan hängt an der Sponsoren Liste", KONTEXT)
    assert treffer["ereignis"] == "idea.rate_reason"
    assert treffer["argumente"]["idea_ids"] == ["a0000003", "a0000002"]          # Reihenfolge im Satz
    assert feste_argumente("idea.rate_reason", json.dumps(treffer["argumente"]))[0] is not None


def test_laengerer_titel_verdeckt_den_kuerzeren_und_beide_gehen_getrennt() -> None:
    assert abk.karten_im_text("Sponsoren Liste fehlt", KONTEXT) == ["a0000002"]
    assert abk.karten_im_text("Sponsoren zahlen, die Sponsoren Liste fehlt", KONTEXT) == ["a0000001", "a0000002"]


def test_doppelte_kurze_und_teilwort_titel_loesen_nichts_auf() -> None:
    assert abk.karten_im_text("Das Zelt ist zu klein", KONTEXT) == []          # zwei Karten "Zelt"
    assert abk.karten_im_text("Ab morgen", KONTEXT) == []                      # unter drei Zeichen
    assert abk.karten_im_text("Bühnenplanung läuft", KONTEXT) == []            # nur Wortteil


def test_auswahl_und_genannte_karten_ohne_doppel() -> None:
    kontext = {**KONTEXT, "selection": ["a0000003"]}
    treffer = abk.loese("Halte als Begründung fest, dass Bühnenplan und Sponsoren tragen", kontext)
    assert treffer["argumente"]["idea_ids"] == ["a0000003", "a0000001"]


def test_ohne_genannte_karte_bleibt_es_ohne_idea_ids() -> None:
    treffer = abk.loese("Begründung: trägt sich selbst", KONTEXT)
    assert "idea_ids" not in treffer["argumente"]
