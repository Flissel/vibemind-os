"""Befördern: Prüfung vor der Rückfrage, "Ja" mit Schlüssel, Ziel Software-Design (24.11.2026).

Nur erfundene Testdaten (Stadtfest). Die Regeln kommen aus spaces/_contract/kartenregeln.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import befoerdern_bestaetigung as bf  # noqa: E402
from core import ideen_abkuerzung as abk  # noqa: E402

BUBBLE = {"id": "b0b1e001", "title": "Stadtfest", "score": 70, "metadata": {}, "promoted_to_project_id": None}


def _karte(kennung, inhalt, **mehr):
    return {"id": kennung, "node_type": mehr.pop("node_type", "note"), "title": kennung, "content": inhalt,
            "metadata": mehr.pop("metadata", {}), "updated_at": mehr.pop("am", "2026-11-20T10:00:00+00:00")}


GUT = [
    _karte("a0000001", "", metadata={"gruppen_kopf": True}),
    _karte("a0000002", "Die Druckerei liefert Plakate bis Freitag."),
    _karte("a0000003", "Der Bühnenplan steht für Samstag fest."),
    _karte("a0000004", "Das Stadtfest braucht einen Bühnenplan.", node_type="dokument",
           metadata={"art": "white_paper"}, am="2026-11-22T08:30:00+00:00"),
]
KANTEN = [{"from_node_id": "a0000001", "to_node_id": "a0000002"},
          {"from_node_id": "a0000002", "to_node_id": "a0000003"}]


@pytest.fixture(autouse=True)
def _leer():
    bf.verwerfen()
    yield
    bf.verwerfen()


def test_regeln_aus_der_gemeinsamen_datei():
    assert Path(bf.regeln().__file__).as_posix().endswith("spaces/_contract/kartenregeln.py")


def test_gute_bubble_bekommt_die_rueckfrage_mit_zahl_und_datum():
    geprueft = bf.pruefen(BUBBLE, GUT, KANTEN)
    assert geprueft["bestaetigbar"] is True
    assert geprueft["satz"] == ("Daraus wird ein Projekt mit 2 Karten und dem White Paper vom 22.11.2026. "
                                "Befördern? Sag „Ja“.")


def test_durchgefallen_sagt_was_fehlt():
    karten = [_karte("a0000002", "Plakate"), _karte("a0000003", "Der Bühnenplan steht für Samstag fest.")]
    geprueft = bf.pruefen({**BUBBLE, "score": 0.0}, karten, [])
    assert geprueft["bestaetigbar"] is False
    assert geprueft["satz"] == ("fehlt: eine leere Karte, 2 lose Karten, keine Bewertung, kein White Paper. "
                                "Befördert ist nichts.")


def test_bewertung_null_mit_begruendung_zaehlt():
    assert bf.bewertung({"score": 0.0, "metadata": {}}) is None
    assert bf.bewertung({"score": 0, "metadata": {"bewertungen": [{"id": "x"}]}}) == {"score": 0, "begruendungen": 1}


def test_schon_befoerdert():
    geprueft = bf.pruefen({**BUBBLE, "promoted_to_project_id": "c0000001"}, GUT, KANTEN)
    assert geprueft["bestaetigbar"] is False and "schon ein Projekt" in geprueft["satz"]


def test_lesen_und_pruefen_liest_nur_und_nimmt_den_lebenden_text():
    gelesen = []
    formatiert = {**GUT[2], "content": "", "content_json": {"text": "Der Bühnenplan steht für Samstag fest."}}

    def lesen(tabelle, params):
        gelesen.append(tabelle)
        if tabelle == "ideas":
            return [BUBBLE]
        if tabelle == "canvas_nodes":
            return [GUT[0], GUT[1], formatiert, GUT[3]]
        return [k for k in KANTEN if k["from_node_id"] in params["from_node_id"][4:-1].split(",")]

    geprueft = bf.lesen_und_pruefen("b0b1e001", lesen=lesen)
    assert geprueft["bestaetigbar"] is True, geprueft
    assert gelesen == ["ideas", "canvas_nodes", "canvas_edges"]
    assert bf.lesen_und_pruefen("b0b1e001", lesen=lambda t, p: None)["satz"] == bf.NICHT_LESBAR


def test_ja_gilt_nur_direkt_nach_der_rueckfrage_und_einmal():
    bf.fragen({"bubble_id": "b0b1e001"}, "Bubble Stadtfest ist jetzt ein Projekt.")
    bestaetigt = bf.satz_pruefen("Ja")
    assert bestaetigt["ereignis"] == "bubble.promote"
    schluessel = bestaetigt["argumente"]["bestaetigung"]
    assert bf.einloesen(schluessel, "b0b1e002") is False           # andere Bubble
    bf.fragen({"bubble_id": "b0b1e001"}, "")
    schluessel = bf.satz_pruefen("ja, befördern")["argumente"]["bestaetigung"]
    assert bf.einloesen(schluessel, "b0b1e001") is True
    assert bf.einloesen(schluessel, "b0b1e001") is False           # nur einmal

    bf.fragen({"bubble_id": "b0b1e001"}, "")
    assert bf.satz_pruefen("Zeig die Notizen") is None             # anderer Satz verwirft
    assert bf.satz_pruefen("Ja") is None                           # blosses Ja: Hoeflichkeit, nicht hier
    assert bf.satz_pruefen("Ja, befördern") == {"satz": bf.OHNE_FRAGE}


def test_abkuerzung_markiert_befoerdern_statt_direkt_aufzurufen():
    kontext = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"}, "cards": []}
    treffer = abk.loese("Befördere diese Bubble", kontext)
    assert treffer["befoerdern"] is True
    assert (treffer["ereignis"], treffer["argumente"]) == ("bubble.promote", {"bubble_id": "b0b1e001"})
    assert "Software-Design" in treffer["antwort"] and "Zurück" in treffer["antwort"]


def test_ziel_nach_dem_befoerdern_ist_software_design():
    quittung = {"content": [{"type": "text", "text": json.dumps(
        {"id": "c0000001", "from_idea_id": "b0b1e001", "ziel": {"space": "swedesign", "projekt_id": "c0000001"}})}]}
    ziel = abk.ziel_fuer_schritt("bubble.promote", True, {"bubble_id": "b0b1e001"}, quittung)
    assert ziel == {"space": "swedesign", "projekt_id": "c0000001", "bubble_id": "b0b1e001", "node_id": None}
    assert abk.ziel_fuer_schritt("bubble.promote", False, {"bubble_id": "b0b1e001"}, quittung) is None


@pytest.mark.parametrize("fehler, teil", [
    ("Ideen-Werkzeug meldet einen Fehler (befoerdern_nicht_bereit)", "seit der Rückfrage geändert"),
    ("Ideen-Werkzeug meldet einen Fehler (bubble_already_promoted)", "schon ein Projekt"),
    ("Ideen-Werkzeug meldet einen Fehler (befoerdern_zu_gross)", "zu groß"),
    (bf.OHNE_SCHLUESSEL, "nur nach Rückfrage"),
])
def test_fehler_werden_zu_saetzen(fehler, teil):
    assert teil in abk.fehlersatz(fehler, "bubble.promote")


def test_hoeflichkeit_sieht_die_offene_rueckfrage():
    from core import hoeflichkeit
    bf.fragen({"bubble_id": "b0b1e001"}, "")
    assert hoeflichkeit.etwas_offen() is True
