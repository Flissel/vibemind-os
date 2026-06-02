"""Regression-Tests für den SoM-Planner — deterministisch, kein echter LLM-Call.

Deckt die in der adversarialen Review gefundenen kritischen Punkte ab:
- Feedback-Loop: FAIL+feedback → Korrektur-Runde → PASS
- Feedback-Limit: dauerhaftes FAIL → needs_human nach max Runden (kein Endlos-Loop)
- ready-Ehrlichkeit: WARN+fehlende Daten → needs_input (nicht ready)
- Race-Fix: paralleler plan_write → kein lost-update

Aufruf:
    voice/.venv312/Scripts/python spaces/autogen/planner/tests/test_feedback_loop.py
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PLANNER = Path(__file__).resolve().parents[1]
os.environ["SOM_STATE_ROOT"] = tempfile.mkdtemp(prefix="som_test_")
os.environ["SOM_NOTIFY"] = "0"  # Tests dürfen NIE echtes Telegram senden


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("t_state", _PLANNER / "_lib" / "state.py")
_core = _load("t_core", _PLANNER / "runner" / "som_core.py")

_passed = []
_failed = []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


# ── Test 1: Feedback-Loop FAIL → Korrektur → PASS ───────────────────────────
def test_feedback_corrects():
    print("Test 1: Feedback-Loop FAIL→Korrektur→PASS")
    calls = {"validator": 0}

    def fake_agent(role, user_input, run_id=""):
        if role == "planner":
            # 2. Runde erkennt man am Korrektur-Auftrag im Input
            korrektur = "KORREKTUR-AUFTRAG" in user_input
            return {"intent": "x", "rationale": "r",
                    "steps": [{"id": "s1", "beschreibung": "tu was", "capability": "code_search",
                               "braucht_daten": [], "liefert_daten": ["out"], "depends_on": []}],
                    "offene_fragen": []}
        if role == "executor":
            return {"steps": [{"plan_step_id": "s1", "execution_target": "openfang:fungus-search",
                               "konkrete_args": {}, "reihenfolge": 1, "parallel_ok": False}],
                    "benoetigte_daten_fehlen": []}
        if role == "validator":
            calls["validator"] += 1
            if calls["validator"] == 1:
                return {"verdict": "FAIL", "findings": [{"severity": "FAIL", "plan_step_id": "s1", "message": "x"}],
                        "approval_gates": [], "feedback_fuer_planner": "Schritt s1 braucht eine Datengrundlage."}
            return {"verdict": "PASS", "findings": [], "approval_gates": [], "feedback_fuer_planner": None}
        return {}

    orig = _core._call_agent
    _core._call_agent = fake_agent
    try:
        r = _core.run("test-intent", run_id="fb_corr")
    finally:
        _core._call_agent = orig

    check("Validator 2x aufgerufen (1 FAIL + 1 Korrektur)", calls["validator"] == 2)
    check("Korrektur-Runde lief (feedback_1 step)", "feedback_1" in r.get("steps", {}))
    check("Endstatus ready (nach Korrektur PASS)", r.get("status") == "ready")


# ── Test 2: dauerhaftes FAIL → needs_human nach max Runden (kein Endlos) ─────
def test_feedback_limit():
    print("Test 2: Dauerhaftes FAIL → needs_human nach Limit")
    calls = {"validator": 0}

    def fake_agent(role, user_input, run_id=""):
        if role == "planner":
            return {"intent": "x", "rationale": "r",
                    "steps": [{"id": "s1", "beschreibung": "x", "capability": None,
                               "braucht_daten": [], "liefert_daten": [], "depends_on": []}],
                    "offene_fragen": []}
        if role == "executor":
            return {"steps": [{"plan_step_id": "s1", "execution_target": "", "konkrete_args": {},
                               "reihenfolge": 1, "parallel_ok": False}], "benoetigte_daten_fehlen": []}
        if role == "validator":
            calls["validator"] += 1
            return {"verdict": "FAIL", "findings": [], "approval_gates": [],
                    "feedback_fuer_planner": "immer noch kaputt"}
        return {}

    os.environ["SOM_MAX_FEEDBACK"] = "2"
    orig = _core._call_agent
    _core._call_agent = fake_agent
    try:
        r = _core.run("test-intent", run_id="fb_limit")
    finally:
        _core._call_agent = orig

    # max 2 feedback → validator läuft 3x (round 0,1,2), dann needs_human
    check("Validator 3x (round 0,1,2 = max 2 Korrekturen)", calls["validator"] == 3)
    check("Endstatus needs_human", r.get("status") == "needs_human")
    check("feedback_exhausted Flag gesetzt", r.get("feedback_exhausted") is True)


# ── Test 3: ready-Ehrlichkeit — WARN + fehlende Daten → needs_input ─────────
def test_ready_honesty():
    print("Test 3: WARN + fehlende Daten → needs_input (nicht ready)")

    def fake_agent(role, user_input, run_id=""):
        if role == "planner":
            return {"intent": "x", "rationale": "r",
                    "steps": [{"id": "s1", "beschreibung": "x", "capability": "email_action",
                               "braucht_daten": [], "liefert_daten": [], "depends_on": []}],
                    "offene_fragen": []}
        if role == "executor":
            return {"steps": [{"plan_step_id": "s1", "execution_target": "skill:email_action",
                               "konkrete_args": {}, "reihenfolge": 1, "parallel_ok": False}],
                    "benoetigte_daten_fehlen": ["Email-Adresse fehlt"]}  # ← Pflichtdaten fehlen
        if role == "validator":
            return {"verdict": "WARN", "findings": [], "approval_gates": [], "feedback_fuer_planner": None}
        return {}

    orig = _core._call_agent
    _core._call_agent = fake_agent
    try:
        r = _core.run("test-intent", run_id="ready_test")
    finally:
        _core._call_agent = orig

    check("WARN+fehlende Daten → needs_input (NICHT ready)", r.get("status") == "needs_input")


# ── Test 4: Race-Fix — parallele plan_write, kein lost-update ───────────────
def test_race_fix():
    print("Test 4: Race-Fix — 8 parallele plan_write, kein lost-update")
    _state.run_create("race_reg", "x")
    errs = []

    def worker(kind, n):
        try:
            for i in range(n):
                _state.plan_write("race_reg", kind, {"i": i})
        except Exception as e:  # noqa: BLE001
            errs.append(str(e))

    threads = [threading.Thread(target=worker, args=(k, 4))
               for k in ["plan", "exec", "verdict", "matrix"] * 2]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    v = _state.run_meta("race_reg").get("versions", {})
    check("Keine Fehler bei parallelem Schreiben", not errs)
    check("Versionen korrekt (2 threads × 4 = 8 je kind)",
          v == {"plan": 8, "exec": 8, "verdict": 8, "matrix": 8})


# ── Test 5: Topologie-Gegencheck — verwaister Node löst Korrektur aus ───────
def test_topo_triggers_feedback():
    print("Test 5: Verwaister Node (Validator WARN) → Topo-Check löst Korrektur aus")
    calls = {"planner": 0}

    def fake_agent(role, user_input, run_id=""):
        if role == "planner":
            calls["planner"] += 1
            if calls["planner"] == 1:
                # Runde 1: s2 ist VERWAIST (kein depends_on, niemand hängt dran)
                return {"intent": "x", "rationale": "r", "steps": [
                    {"id": "s1", "beschreibung": "start", "capability": "code_search",
                     "braucht_daten": [], "liefert_daten": ["a"], "depends_on": []},
                    {"id": "s2", "beschreibung": "verwaist", "capability": "code_search",
                     "braucht_daten": [], "liefert_daten": [], "depends_on": []},
                ], "offene_fragen": []}
            # Runde 2 (Korrektur): s2 jetzt verbunden
            return {"intent": "x", "rationale": "r", "steps": [
                {"id": "s1", "beschreibung": "start", "capability": "code_search",
                 "braucht_daten": [], "liefert_daten": ["a"], "depends_on": []},
                {"id": "s2", "beschreibung": "verbunden", "capability": "code_search",
                 "braucht_daten": ["a"], "liefert_daten": [], "depends_on": ["s1"]},
            ], "offene_fragen": []}
        if role == "executor":
            return {"steps": [], "benoetigte_daten_fehlen": []}
        if role == "validator":
            # Validator gibt IMMER WARN (ist blind für Topologie) — der Topo-Check
            # muss den verwaisten Node trotzdem fangen
            return {"verdict": "WARN", "findings": [], "approval_gates": [], "feedback_fuer_planner": None}
        return {}

    os.environ["SOM_MAX_FEEDBACK"] = "2"
    orig = _core._call_agent
    _core._call_agent = fake_agent
    try:
        r = _core.run("test-intent", run_id="topo_test")
    finally:
        _core._call_agent = orig

    check("Topo-Mangel löste Korrektur aus (planner 2x trotz Validator-WARN)", calls["planner"] == 2)
    check("feedback_1 mit Topo-Mangel-Eintrag", "feedback_1" in r.get("steps", {}))
    check("Endstatus nicht needs_human (Korrektur erfolgreich)", r.get("status") != "needs_human")


if __name__ == "__main__":
    test_feedback_corrects()
    test_feedback_limit()
    test_ready_honesty()
    test_race_fix()
    test_topo_triggers_feedback()
    print()
    print(f"=== {len(_passed)} PASSED, {len(_failed)} FAILED ===")
    if _failed:
        print("FEHLGESCHLAGEN:", _failed)
        sys.exit(1)
