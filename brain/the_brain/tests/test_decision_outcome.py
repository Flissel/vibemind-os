"""Der Ausgang eines Plans kommt aus dem verifizierten Ergebnis, nicht aus `ok`."""
from types import SimpleNamespace

import pytest

from core.decision_outcome import counts, is_test_run, verified_outcome


def hr(cp, ok=True, cap="bubble_create"):
    return SimpleNamespace(ok=ok, contract_pass=cp, capability=cap, step_id="s")


@pytest.mark.parametrize("werte,erwartet", [
    ([True, True], "success"),
    ([False, False], "failure"),
    ([True, False], "partial"),
    ([True, None], "unverified"),
    ([None], "unverified"),
    ([None, False], "failure"),
    ([], "failure"),
])
def test_verified_outcome(werte, erwartet):
    assert verified_outcome([hr(v) for v in werte]) == erwartet


def test_ok_ohne_pruefung_ist_kein_erfolg():
    # Werkzeug meldet ok, aber keine Pruefung lief: contract_pass None.
    assert verified_outcome([hr(None, ok=True)]) == "unverified"


def test_dicts_werden_auch_gelesen():
    assert verified_outcome([{"contract_pass": True}]) == "success"


def test_counts():
    c = counts([hr(True), hr(False), hr(None), hr(None)])
    assert c == {"verified_success": 1, "verified_failure": 1, "unverified": 2}


@pytest.mark.parametrize("intent,trace,erwartet", [
    ("__probe_capacity__", "", True),
    ("wie viele Ideen gibt es ITER3A", "", True),
    ("delete the bubble SWEEP_G5_EXEC_DEST", "", True),
    ("create a bubble named GTTEST2026", "", True),
    ("create a bubble named EXECTESTLOOP2", "", True),
    ("erstelle eine Bubble namens IntentValidationTest", "", True),
    ("erstelle eine Bubble Marketing Q4", "test_t1_abc", True),
    ("erstelle eine Bubble Marketing Q4", "tr_1a2b3c", False),
    ("Summarize the latest research on vector DBs", "", False),
    ("teste bitte, ob die Bubble Marketing existiert", "", False),
])
def test_is_test_run(intent, trace, erwartet):
    assert is_test_run(intent, trace) is erwartet


from unittest.mock import MagicMock

from core import decision_recall


def test_record_schreibt_verifizierten_ausgang_und_testmarke():
    kg = MagicMock()
    kg.client = object()
    decision_recall.record(
        plan_id="plan_x", intent="create a bubble named SWEEP_G1_EXEC",
        plan=SimpleNamespace(rationale="r", hops=[1, 2], trace_id="tr_1"),
        hop_results=[hr(True), hr(None)], outcome="success", reward=None, kg=kg)
    payload = kg._upsert_point.call_args.kwargs["payload_extra"]
    assert payload["outcome"] == "unverified"
    assert payload["verified_success"] == 1 and payload["unverified"] == 1
    assert payload["is_test"] is True
    # die alten Zaehler bleiben fuer Rueckwaertskompatibilitaet
    assert payload["success_count"] == 2


# ── Selbstbild lernt nur aus verifizierten Schritten (Controller-Ruling zu
# Brief-Step 8: statt Quelltext-Grep wird der extrahierte Helfer direkt
# getestet) ────────────────────────────────────────────────────────────────
from core.plan_executor import _self_prior_beobachtungen


def test_beobachtungen_none_wird_uebersprungen():
    plan = SimpleNamespace(intent="etwas tun", trace_id="tr_1")
    assert _self_prior_beobachtungen(plan, [hr(None)]) == []


def test_beobachtungen_true_false_werden_gemappt():
    plan = SimpleNamespace(intent="etwas tun", trace_id="tr_1")
    ergebnis = _self_prior_beobachtungen(
        plan, [hr(True, cap="a"), hr(False, cap="b")])
    assert ergebnis == [("a", True), ("b", False)]


def test_beobachtungen_testlauf_liefert_nichts():
    plan = SimpleNamespace(intent="irrelevant", trace_id="test_t1_abc")
    assert _self_prior_beobachtungen(plan, [hr(True, cap="a")]) == []


def test_beobachtungen_duplikat_capability_nur_einmal():
    plan = SimpleNamespace(intent="etwas tun", trace_id="tr_1")
    ergebnis = _self_prior_beobachtungen(
        plan, [hr(True, cap="a"), hr(False, cap="a")])
    assert ergebnis == [("a", True)]


def test_beobachtungen_leere_capability_wird_uebersprungen():
    plan = SimpleNamespace(intent="etwas tun", trace_id="tr_1")
    assert _self_prior_beobachtungen(plan, [hr(True, cap="")]) == []
