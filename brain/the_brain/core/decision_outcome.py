"""Ausgang eines Plans aus verifizierten Schritten (Brain T1).

`HopResult.contract_pass` ist True/False nur, wenn eine unabhaengige Nachfrage
an der Quelle lief (plan_schema.contract_pass_from). None heisst: niemand hat
nachgesehen. Ein Plan ist deshalb nur `success`, wenn JEDER Schritt verifiziert
gelang. Ein ungeprueftes "ok" ist kein Erfolg.

Testlaeufe erkennt `is_test_run`: neu ueber eine trace_id mit Praefix `test_`
(Testwerkzeuge setzen sie beim Aufruf von /api/multihop/execute), fuer den
Altbestand ueber die Namensmuster, die am 2026-09-23 in brain-decisions
gefunden wurden.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

TEST_TRACE_PREFIX = "test_"

# Gemessen in brain-decisions am 2026-09-23 (166 Eintraege).
_TEST_MUSTER = re.compile(
    r"__probe_capacity__"
    r"|\bITER\d\w*"
    r"|\bSWEEP_G\d\w*"
    r"|\bGTTEST\w*"
    r"|\bEXECTEST\w*"
    r"|\bIntentValidation\w*"
)


def _cp(hr: Any):
    if isinstance(hr, dict):
        return hr.get("contract_pass")
    return getattr(hr, "contract_pass", None)


def _verdict(hr: Any):
    if isinstance(hr, dict):
        return hr.get("validator_verdict")
    return getattr(hr, "validator_verdict", None)


def wirksamer_befund(hr: Any) -> Optional[bool]:
    """Der fuer Zaehlung/Lernen wirksame Befund eines Hops.

    F1 (Schlusspruefung): ein `truth:`-Pruefer, der selbst ausfaellt, liefert
    `validator_verdict={"verified": None, ...}`. Bei `on_fail: block` setzt
    der Executor `ok=False` und damit `contract_pass=False` — das ist eine
    Policy-Entscheidung fuer den Nutzer (blockieren), keine verifizierte
    Aussage. Fuer Zaehlung/Lernen gilt so ein Hop als unverifiziert (None),
    unabhaengig von `ok`/`contract_pass`. Ein Hop ohne Validator-Urteil oder
    mit `verified` True/False behaelt sein `contract_pass`.
    """
    verdict = _verdict(hr)
    if isinstance(verdict, dict) and "verified" in verdict and verdict.get("verified") is None:
        return None
    return _cp(hr)


def counts(hop_results: List[Any]) -> Dict[str, int]:
    werte = [wirksamer_befund(h) for h in hop_results or []]
    return {
        "verified_success": sum(1 for v in werte if v is True),
        "verified_failure": sum(1 for v in werte if v is False),
        "unverified": sum(1 for v in werte if v is None),
    }


def verified_outcome(hop_results: List[Any]) -> str:
    c = counts(hop_results)
    if not hop_results:
        return "failure"
    if c["verified_failure"]:
        return "partial" if c["verified_success"] else "failure"
    if c["unverified"]:
        return "unverified"
    return "success"


def is_test_run(intent: str, trace_id: str = "") -> bool:
    if (trace_id or "").startswith(TEST_TRACE_PREFIX):
        return True
    return bool(_TEST_MUSTER.search(intent or ""))
