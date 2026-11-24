"""Snapshot of a promoted project (spaces/shuttles/stand.py). All data invented."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("stand", Path(__file__).resolve().parents[1] / "stand.py")
stand = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stand)

STAND = {
    "version": 1, "bubble_id": "b-hof", "am": "2026-01-06T09:59:00Z", "titel": "Hofladen",
    "bewertung": {"score": 8, "begruendungen": 2},
    "white_paper": {"node_id": "w1", "titel": "Hofladen", "text": "# Ziel\nRegional einkaufen.", "quellen": ["a", "c"]},
    "karten": [
        {"id": "g", "titel": "Laden", "text": "", "art": "gruppe"},
        {"id": "a", "titel": "Sortiment", "text": "Gemuese, Eier und Kaese aus dem Dorf.", "art": "notiz", "gruppe": "Laden"},
        {"id": "b", "titel": "Abholung", "text": "Selbstbedienung mit Kasse des Vertrauens.", "art": "notiz", "gruppe": "Laden"},
        {"id": "c", "titel": "Zielgruppe", "text": "Familien im Dorf und Pendler auf dem Heimweg.", "art": "notiz"},
        {"id": "w0", "titel": "Altes White Paper", "text": "# alt", "art": "white_paper"},
    ],
    "kanten": [{"von": "g", "nach": "a", "art": "parent_child"}, {"von": "g", "nach": "b", "art": "parent_child"},
               {"von": "c", "nach": "a", "art": "related"}],
}


def mit_pruefsumme(s: dict) -> dict:
    s = copy.deepcopy(s)
    koerper = {k: v for k, v in s.items() if k not in ("am", "pruefsumme")}
    s["pruefsumme"] = "sha256:" + hashlib.sha256(json.dumps(
        koerper, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return s


def test_lesen_bestanden_mit_kopf_und_altem_white_paper() -> None:
    g = stand.stand_lesen({"uebergabe": mit_pruefsumme(STAND)})
    assert g["ok"] and g["pruefsumme"] == "stimmt"
    assert [n["id"] for n in g["notizen"]] == ["a", "b", "c"]
    assert g["notizen"][0]["gruppe"] == "Laden"
    assert g["pruefung"]["bestanden"], g["pruefung"]
    assert g["pruefung"]["zahlen"]["anteil_im_white_paper"] == 0.667
    assert g["white_paper"] == {"node_id": "w1", "titel": "Hofladen", "text": "# Ziel\nRegional einkaufen."}


def test_pruefsumme_falsch_und_fehlt() -> None:
    s = mit_pruefsumme(STAND)
    s["karten"][1]["text"] = "nachtraeglich geaendert"
    assert stand.pruefsumme_stand(s) == "falsch"
    assert stand.pruefsumme_stand(STAND) == "fehlt"
    assert stand.pruefsumme_stand({**mit_pruefsumme(STAND), "am": "spaeter"}) == "stimmt"


def test_gespeichertes_bestanden_zaehlt_nicht() -> None:
    s = copy.deepcopy(STAND)
    s["pruefung"] = {"bestanden": True, "gruende": []}
    s["karten"].append({"id": "d", "titel": "Leer", "text": "", "art": "notiz"})
    p = stand.stand_lesen({"uebergabe": s})["pruefung"]
    assert not p["bestanden"] and p["gruende"] == ["leere_karten:1", "lose_karten:1"]


def test_kein_stand_und_unbekannte_version() -> None:
    assert stand.stand_lesen({}) == {"ok": False, "fehler": "kein_stand"}
    assert stand.stand_lesen(None)["fehler"] == "kein_stand"
    g = stand.stand_lesen({"uebergabe": {**STAND, "version": 2}})
    assert (g["ok"], g["fehler"], g["version"]) == (False, "format_unbekannt", 2)


def test_ohne_white_paper_und_ohne_bewertung() -> None:
    s = {**STAND, "white_paper": None, "bewertung": None}
    g = stand.stand_lesen({"uebergabe": s})
    assert g["white_paper"] is None
    assert g["pruefung"]["gruende"] == ["keine_bewertung"]  # w0 is still a white paper card
    s["karten"] = [k for k in s["karten"] if k["art"] != "white_paper"]
    assert stand.stand_lesen({"uebergabe": s})["pruefung"]["gruende"] == ["keine_bewertung", "kein_white_paper"]
