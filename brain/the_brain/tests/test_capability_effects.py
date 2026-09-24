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
RESTLISTE_OBERGRENZE = 20  # 2026-09-24: einmalig angehoben - 8 Pruefungen konnten nie verifizieren (Schlusspruefung T1). Nur nach unten aendern.


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


import pytest

from core.capability_validator import CapabilityValidator

# (capability, ein realistischer Ergebnistext, erwartete gefuellte Felder)
FAELLE = [
    ("component_note_write",
     {"ok": True, "node_id": "3f2a9c1e-aaaa-bbbb-cccc-1234567890ab"},
     {"check": "supabase_row", "table": "canvas_nodes",
      "match": "id=eq.3f2a9c1e-aaaa-bbbb-cccc-1234567890ab"}),
    ("code_generate", "{'id': 42, 'status': 'pending'}",
     {"check": "http_ok", "path": "/api/v1/jobs/42/status"}),
    ("idea_count", "'Marketing' has 12 ideas.",
     {"check": "supabase_bubble_node_count", "bubble_title": "Marketing",
      "expect_count": "12"}),
]


@pytest.mark.parametrize("name,ergebnis,erwartet", FAELLE)
def test_postcondition_fuellt_sich_aus_dem_ergebnis(name, ergebnis, erwartet):
    val = _caps()[name]["validator"]
    assert str(val["kind"]).startswith("truth:")
    pc = CapabilityValidator._template_postcondition(val["postcondition"], "", ergebnis)
    for k, v in erwartet.items():
        assert pc[k] == v, (name, k, pc)


# M1 (Schlusspruefung T1): {result_count} darf nur aus "has N idea(s)" oder
# "N idea(s) in" (Textanfang) fuellen, nicht aus jeder "N idea(s)"-Stelle
# irgendwo im Fliesstext (die vorher gaengige Formulierung koennte sonst eine
# falsche Zahl aus einem unbeteiligten Satzteil ziehen).
@pytest.mark.parametrize("ergebnis,erwartet", [
    ("'Top 3 ideas' has 12 ideas.", "12"),
    ("12 ideas in 'X':\n- a", "12"),
])
def test_result_count_nur_aus_den_beiden_erlaubten_formen(ergebnis, erwartet):
    pc = CapabilityValidator._template_postcondition({"x": "{result_count}"}, "", ergebnis)
    assert pc["x"] == erwartet


def test_registry_quote():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pruefquote", Path(__file__).resolve().parents[1] / "scripts" / "pruefquote.py")
    pq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pq)
    caps = [{"capability": "a", "validator": {"kind": "truth:supabase_row"}},
            {"capability": "b", "validator": {"kind": "rule:x"}},
            {"capability": "c"}]
    effects = {"effects": {"a": "write", "b": "write", "c": "read"},
               "restliste": {"b": "Grund"}}
    q = pq.registry_quote(caps, effects)
    assert q == {"write_gesamt": 2, "write_mit_truth": 1, "quote_write": 0.5, "restliste": 1}
