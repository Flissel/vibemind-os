"""Vorsicht bei "Zurück" (Arbeitslauf 2, Punkt 7). Ohne Netz; Gruppen und Titel erfunden."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import zurueck_vorsicht as zv  # noqa: E402

JETZT = 1_800_000_000.0


def _iso(vor_s: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(JETZT - vor_s, tz=timezone.utc).isoformat()


def _zeile(gruppe, vor_s, titel="Bühnenplan", vorgang="UPDATE"):
    return {"gruppe": gruppe, "erstellt_am": _iso(vor_s), "vorgang": vorgang, "nachher_titel": titel}


@pytest.fixture(autouse=True)
def _uhr(monkeypatch):
    zv._offen.clear()
    uhr = {"t": JETZT}
    monkeypatch.setattr(zv, "_jetzt", lambda: uhr["t"])
    yield uhr
    zv._offen.clear()


def _nie(_g):
    return False


def test_frische_aenderung_geht_sofort_und_mit_gruppe() -> None:
    wahl = zv.entscheiden("", "zurück", ist_abgelehnt=_nie, lesen=lambda: [_zeile("ideen_2", 30)])
    assert wahl == {"aktion": "weiter", "gruppe": "ideen_2"}


def test_genannte_gruppe_geht_unveraendert_weiter() -> None:
    wahl = zv.entscheiden("hand-4711", "nimm hand-4711 zurück", ist_abgelehnt=_nie, lesen=lambda: None)
    assert wahl == {"aktion": "weiter", "gruppe": "hand-4711"}


def test_aeltere_aenderung_wird_erst_benannt_und_dann_bestaetigt(_uhr) -> None:
    zeilen = [_zeile("ideen_1", 600, "Sponsoren", "INSERT")]
    wahl = zv.entscheiden("", "zurück", ist_abgelehnt=_nie, lesen=lambda: zeilen)
    assert wahl["aktion"] == "fragen"
    assert "„Sponsoren“ angelegt vor 10 Minuten" in wahl["satz"] and "„Ja, zurück“" in wahl["satz"]
    _uhr["t"] += 20
    assert zv.entscheiden("", "Ja, zurück", ist_abgelehnt=_nie, lesen=lambda: zeilen) == {
        "aktion": "weiter", "gruppe": "ideen_1"}


def test_bestaetigung_verfaellt(_uhr) -> None:
    zeilen = [_zeile("ideen_1", 600)]
    zv.entscheiden("", "zurück", ist_abgelehnt=_nie, lesen=lambda: zeilen)
    _uhr["t"] += zv.BESTAETIGUNG_GILT_S + 1
    assert zv.entscheiden("", "Ja, zurück", ist_abgelehnt=_nie, lesen=lambda: zeilen)["aktion"] == "fragen"


def test_abgelehnter_schritt_ist_nie_ziel() -> None:
    zeilen = [_zeile("ideen_9", 10, "Preise"), _zeile("ideen_8", 40, "Aufbau")]
    wahl = zv.entscheiden("", "zurück", ist_abgelehnt=lambda g: g == "ideen_9", lesen=lambda: zeilen)
    assert wahl["aktion"] == "fragen"                    # auch die frische ältere fragt nach
    assert wahl["satz"].startswith("Der letzte Schritt wurde abgelehnt")
    assert "„Aufbau“ geändert" in wahl["satz"] and "Preise" not in wahl["satz"]
    nur_abgelehnt = zv.entscheiden("", "zurück", ist_abgelehnt=lambda g: True, lesen=lambda: zeilen)
    assert nur_abgelehnt["aktion"] == "fragen" and "nichts zurückzunehmen" in nur_abgelehnt["satz"]


def test_neue_aenderung_nach_der_frage_wird_nicht_blind_genommen(_uhr) -> None:
    zv.entscheiden("", "zurück", ist_abgelehnt=_nie, lesen=lambda: [_zeile("ideen_1", 600)])
    neu = [_zeile("ideen_3", 700), _zeile("ideen_1", 600)]   # Uhr steht, ideen_3 ist älter
    assert zv.entscheiden("", "ja, zurück", ist_abgelehnt=_nie, lesen=lambda: neu)["aktion"] == "fragen"


def test_unlesbares_protokoll_nimmt_nichts_zurueck_und_leeres_geht_ans_werkzeug() -> None:
    assert zv.entscheiden("", "zurück", ist_abgelehnt=_nie, lesen=lambda: None)["aktion"] == "fragen"
    assert zv.entscheiden("", "zurück", ist_abgelehnt=_nie, lesen=lambda: []) == {"aktion": "weiter", "gruppe": ""}


def test_mehrere_eintraege_und_satz_aus_fehler() -> None:
    gruppe = {"zeilen": [_zeile("p", 1, "Zelt"), _zeile("p", 2, "Bühnenplan", "DELETE")]}
    assert zv.beschreibung(gruppe) == "2 Einträge, darunter „Bühnenplan“ gelöscht"
    from core.ideen_abkuerzung import antwort_fuer_schritt, fehlersatz
    assert fehlersatz("RuntimeError: " + zv.FRAGE_MARKE + "Satz.", "aenderung_zurueck") == "Satz."
    assert antwort_fuer_schritt("aenderung_zurueck", False, None, zv.FRAGE_MARKE + "Satz.") == "Satz."
