"""Sperrklinke: jede schreibende Capability hat eine echte Pruefung.

Faellt dieser Test, gibt es genau drei richtige Reaktionen:
  - neue Capability: in capability_effects.yaml eine Klasse eintragen;
  - write ohne truth:: eine truth:-Pruefung bauen ODER mit Begruendung auf die
    Restliste setzen;
  - Restlisten-Eintrag hat inzwischen truth:: von der Restliste STREICHEN.
Die Restliste darf nur schrumpfen - ihre Obergrenze steht hier im Test.
"""
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parents[1] / "data"
KLASSEN = {"write", "read", "external", "answer", "unrouted"}
RESTLISTE_OBERGRENZE = 12  # Stand 2026-09-23; nur nach unten aendern.


def _caps():
    return {c["capability"]: c for c in
            yaml.safe_load((DATA / "capabilities.yaml").read_text(encoding="utf-8"))}


def _effects():
    return yaml.safe_load((DATA / "capability_effects.yaml").read_text(encoding="utf-8"))


def _hat_truth(cap):
    return str((cap.get("validator") or {}).get("kind", "")).startswith("truth:")


def test_jede_capability_hat_eine_klasse():
    caps, eff = _caps(), _effects()["effects"]
    fehlt = sorted(set(caps) - set(eff))
    assert not fehlt, f"ohne Wirkungsklasse: {fehlt}"
    verwaist = sorted(set(eff) - set(caps))
    assert not verwaist, f"in capability_effects.yaml, aber nicht in capabilities.yaml: {verwaist}"
    falsch = {k: v for k, v in eff.items() if v not in KLASSEN}
    assert not falsch, f"unbekannte Klasse: {falsch}"


def test_write_hat_truth_oder_steht_begruendet_auf_der_restliste():
    caps, e = _caps(), _effects()
    rest = e.get("restliste") or {}
    ohne = sorted(n for n, k in e["effects"].items()
                  if k == "write" and not _hat_truth(caps[n]) and n not in rest)
    assert not ohne, f"write ohne truth: und ohne Restlisten-Eintrag: {ohne}"


def test_restliste_nur_mit_offenen_faellen():
    caps, e = _caps(), _effects()
    rest = e.get("restliste") or {}
    erledigt = sorted(n for n in rest if _hat_truth(caps[n]))
    assert not erledigt, f"hat inzwischen truth: - von der Restliste streichen: {erledigt}"
    kein_write = sorted(n for n in rest if e["effects"].get(n) != "write")
    assert not kein_write, f"Restliste ist nur fuer write: {kein_write}"
    leer = sorted(n for n, grund in rest.items() if not str(grund).strip())
    assert not leer, f"Restlisten-Eintrag ohne Begruendung: {leer}"


def test_restliste_schrumpft_nur():
    assert len(_effects().get("restliste") or {}) <= RESTLISTE_OBERGRENZE


def test_buergergeld_ist_raus():
    assert not [n for n in _caps() if "buergergeld" in n]
