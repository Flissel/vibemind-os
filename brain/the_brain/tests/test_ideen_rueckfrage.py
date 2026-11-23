"""Antwort auf eine offene Rueckfrage (Arbeitslauf 2: "die zweite" ging an den Chat).

Nur der Aufloeser im Gehirn, ohne Netz; Namen erfunden.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import ideen_abkuerzung as abk  # noqa: E402

KONTEXT = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"},
           "cards": [{"id": "a0000001", "title": "Sponsoren Vertrag"}, {"id": "a0000002", "title": "Sponsoren Liste"},
                     {"id": "a0000003", "title": "Bühnenplan"}, {"id": "a0000004", "title": "Bühnenplan"}]}


@pytest.fixture(autouse=True)
def _leer(monkeypatch):
    abk._OFFEN.clear()
    uhr = {"t": 1000.0}
    monkeypatch.setattr(abk, "_jetzt", lambda: uhr["t"])
    yield uhr
    abk._OFFEN.clear()


def _frage():
    antwort = abk.loese("Bewerte Sponsoren mit 80", KONTEXT)
    assert "Welche meinst du" in antwort["rueckfrage"]
    return antwort


@pytest.mark.parametrize("antwort, karte", [
    ("die zweite", "a0000002"), ("Die erste.", "a0000001"), ("2", "a0000002"), ("Nummer 1", "a0000001"),
    ("die letzte", "a0000002"), ("Liste", "a0000002"), ("die Sponsoren Vertrag", "a0000001"),
    ("ich meine die zweite Notiz", "a0000002"),
])
def test_antwort_loest_die_offene_rueckfrage(antwort, karte) -> None:
    _frage()
    treffer = abk.loese(antwort, KONTEXT)
    assert treffer["ereignis"] == "idea.rate" and treffer["argumente"] == {"id": karte, "score": 80}
    assert abk._OFFEN == {}                                                   # einmal benutzt, dann weg


def test_unpassende_antwort_fragt_noch_einmal_mit_nummern() -> None:
    _frage()
    antwort = abk.loese("die fünfte", KONTEXT)
    assert antwort["rueckfrage"].startswith("Das passt zu keiner. Welche meinst du? 1. „Sponsoren Vertrag“")
    assert abk.loese("die zweite", KONTEXT)["argumente"]["id"] == "a0000002"   # bleibt offen bis dahin


def test_neuer_fester_satz_geht_vor_und_schliesst_die_rueckfrage() -> None:
    _frage()
    treffer = abk.loese("Was steht hier?", KONTEXT)
    assert treffer["ereignis"] == "idea.list" and abk._OFFEN == {}


def test_lange_frage_ist_keine_antwort_und_geht_den_bisherigen_weg() -> None:
    _frage()
    assert abk.loese("Wie wird das Wetter am Wochenende in der Stadt?", KONTEXT) is None
    assert abk._OFFEN == {}


def test_rueckfrage_verfaellt(_leer) -> None:
    _frage()
    _leer["t"] += abk.OFFEN_GILT_S + 1
    assert abk.loese("die zweite", KONTEXT) is None


def test_rueckfrage_gilt_nur_in_derselben_bubble() -> None:
    _frage()
    andere = {**KONTEXT, "bubble": {"id": "b0b1e002", "title": "Laternenlauf"}}
    assert abk.loese("die zweite", andere) is None or "Welche" not in str(abk.loese("die zweite", andere))


def test_gleiche_titel_und_zweiter_name_im_selben_satz() -> None:
    antwort = abk.loese("Verbinde Sponsoren mit Bühnenplan", KONTEXT)
    assert "Welche meinst du" in antwort["rueckfrage"]
    zweite = abk.loese("die erste", KONTEXT)                                  # Sponsoren Vertrag
    assert "2 Notizen mit dem Titel „Bühnenplan“" in zweite["rueckfrage"]
    treffer = abk.loese("die zweite", KONTEXT)                                # zweiter Bühnenplan
    assert treffer["ereignis"] == "idea.connect"
    assert treffer["argumente"] == {"from_id": "a0000001", "to_id": "a0000004"}


def test_ohne_kandidaten_wird_nichts_gemerkt() -> None:
    abk.loese("Bewerte Urlaub mit 80", KONTEXT)
    assert abk._OFFEN == {}
