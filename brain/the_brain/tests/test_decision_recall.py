"""F4 (Schlusspruefung T1): recall() lernt nicht aus Testlaeufen und bevorzugt
die verifizierte Erfolgsquote (verified_success/verified_failure) vor der
alten, unverifizierten (success_count/fail_count) - wenn beide verifizierten
Zaehler existieren. Fehlen sie (Altbestand), gilt weiter der alte Weg.
"""
from unittest.mock import MagicMock

from core.decision_recall import format_for_prompt, recall


def _kg(hits):
    kg = MagicMock()
    kg.client = object()
    kg.search.return_value = hits
    return kg


def _hit(payload, score=0.9):
    return {"payload": payload, "score": score}


def test_testlauf_fehlt_im_ergebnis():
    hits = [_hit({"plan_id": "p1", "intent": "x", "is_test": True,
                   "success_count": 5, "fail_count": 0})]
    ergebnis = recall("x", _kg(hits))
    assert ergebnis == []


def test_verifizierte_quote_gewinnt_gegen_die_alte():
    # alte Zaehler sagen 100% (1/1), die verifizierten Zaehler sagen 0% (0/2) -
    # die verifizierte Quote muss gewinnen, nicht die alte.
    hits = [_hit({"plan_id": "p1", "intent": "x",
                   "success_count": 1, "fail_count": 0,
                   "verified_success": 0, "verified_failure": 2})]
    ergebnis = recall("x", _kg(hits))
    assert len(ergebnis) == 1
    assert ergebnis[0]["success_rate"] == 0.0


def test_keine_verifizierten_zaehler_alter_weg():
    # verified_success/verified_failure fehlen komplett (Altbestand vor Task 3/4)
    # -> alter Weg (success_count/fail_count).
    hits = [_hit({"plan_id": "p1", "intent": "x",
                   "success_count": 3, "fail_count": 1})]
    ergebnis = recall("x", _kg(hits))
    assert len(ergebnis) == 1
    assert ergebnis[0]["success_rate"] == 0.75


def test_beide_verifizierten_zaehler_null_ist_unbekannt_nicht_null():
    # Felder sind vorhanden, aber beide 0 -> success_rate ist None (unbekannt),
    # nicht 0 und nicht der alte Weg.
    hits = [_hit({"plan_id": "p1", "intent": "x",
                   "success_count": 0, "fail_count": 0,
                   "verified_success": 0, "verified_failure": 0})]
    ergebnis = recall("x", _kg(hits))
    assert len(ergebnis) == 1
    assert ergebnis[0]["success_rate"] is None


def test_format_for_prompt_zeigt_unbekannte_quote_ohne_exception():
    text = format_for_prompt([{
        "plan_id": "p1", "intent": "irgendwas tun",
        "capability_chain": ["bubble_create"], "outcome": "unverified",
        "success_rate": None, "age_seconds": 3600,
    }])
    assert "sr=?" in text
    assert "None" not in text
