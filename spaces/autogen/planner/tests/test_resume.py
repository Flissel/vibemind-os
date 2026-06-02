"""Regression-Tests für den Resume-Loop (Phase 5) — deterministisch, kein LLM.

Deckt ab:
- needs_input schreibt answers_needed.yaml + result['offene_fragen']
- resume(answers) trägt Antworten ein, re-plant, erreicht ready (run_id stabil)
- resume(cancel) → status cancelled, KEIN Re-Run
- message_id->run_id Reply-Map roundtrip
- Detail-Output: format_run_summary listet die Fragen nummeriert + Anleitung

Aufruf:
    voice/.venv312/Scripts/python spaces/autogen/planner/tests/test_resume.py
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PLANNER = Path(__file__).resolve().parents[1]
os.environ["SOM_STATE_ROOT"] = tempfile.mkdtemp(prefix="som_resume_test_")
os.environ["SOM_NOTIFY"] = "0"  # NIE echtes Telegram im Test


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("r_state", _PLANNER / "_lib" / "state.py")
_notify = _load("r_notify", _PLANNER / "_lib" / "notify.py")
_core = _load("r_core", _PLANNER / "runner" / "som_core.py")

_passed = []
_failed = []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


# Ein Planner der je nach beantworteter Rückfrage anders plant: solange das
# Zielprofil unbekannt ist, meldet der Executor fehlende Daten (needs_input);
# sobald die Antwort im Kontext steht, ist alles vollständig (ready).
def _make_agent():
    def fake_agent(role, user_input, run_id=""):
        beantwortet = "BEANTWORTETE RÜCKFRAGEN" in user_input
        if role == "planner":
            return {"intent": "x", "rationale": "r",
                    "steps": [{"id": "s1", "beschreibung": "cv entwurf", "capability": "content_creation",
                               "braucht_daten": [], "liefert_daten": ["cv"], "depends_on": []}],
                    "offene_fragen": []}
        if role == "executor":
            fehlen = [] if beantwortet else ["Zielstellen-Profil unbekannt (AI-Vollzeit/Werkstudent/Startup?)"]
            return {"steps": [{"plan_step_id": "s1", "execution_target": "openfang:writer",
                               "konkrete_args": {}, "reihenfolge": 1, "parallel_ok": False}],
                    "benoetigte_daten_fehlen": fehlen}
        if role == "validator":
            return {"verdict": "PASS" if beantwortet else "WARN",
                    "findings": [], "approval_gates": [], "feedback_fuer_planner": None}
        return {}
    return fake_agent


# ── Test 1: needs_input schreibt answers_needed.yaml + offene_fragen ─────────
def test_needs_input_persists_questions():
    print("Test 1: needs_input → answers_needed.yaml + offene_fragen")
    orig = _core._call_agent
    _core._call_agent = _make_agent()
    try:
        r = _core.run("Plane Bewerbung", run_id="res_q")
    finally:
        _core._call_agent = orig
    check("Status needs_input", r.get("status") == "needs_input")
    check("offene_fragen im Result", bool(r.get("offene_fragen")))
    data = _state.answers_read("res_q")
    check("answers_needed.yaml geschrieben (awaiting_answers)", data.get("status") == "awaiting_answers")
    check("Fragen in der Datei", len(data.get("questions") or []) >= 1)


# ── Test 2: resume(answers) → ready, run_id stabil ──────────────────────────
def test_resume_with_answers():
    print("Test 2: resume(answers) → re-plan → ready, run_id stabil")
    orig = _core._call_agent
    _core._call_agent = _make_agent()
    try:
        r1 = _core.run("Plane Bewerbung", run_id="res_go")
        v_before = _state.run_meta("res_go").get("versions", {}).get("plan", 0)
        r2 = _core.resume("res_go", answers={"1": "AI-Vollzeit Konzern"})
    finally:
        _core._call_agent = orig
    check("Vor Resume needs_input", r1.get("status") == "needs_input")
    check("Nach Resume ready", r2.get("status") == "ready")
    check("run_id stabil", r2.get("run_id") == "res_go")
    v_after = _state.run_meta("res_go").get("versions", {}).get("plan", 0)
    check("Plan-Version gebumpt (re-plan)", v_after > v_before)
    ans = _state.answers_read("res_go")
    check("Antwort eingetragen", any((q.get("antwort") or "").strip() for q in ans.get("questions") or []))


# ── Test 3: resume(cancel) → cancelled, kein Re-Run ─────────────────────────
def test_resume_cancel():
    print("Test 3: resume(cancel) → cancelled, kein Re-Run")
    calls = {"planner": 0}
    orig = _core._call_agent

    def counting_agent(role, user_input, run_id=""):
        if role == "planner":
            calls["planner"] += 1
        return _make_agent()(role, user_input, run_id)

    _core._call_agent = counting_agent
    try:
        _core.run("Plane Bewerbung", run_id="res_cancel")
        before = calls["planner"]
        r = _core.resume("res_cancel", cancel=True)
    finally:
        _core._call_agent = orig
    check("Status cancelled", r.get("status") == "cancelled")
    check("Cancel löst KEINEN Re-Run aus (planner-calls unverändert)", calls["planner"] == before)
    check("run_meta cancelled", _state.run_meta("res_cancel").get("status") == "cancelled")


# ── Test 4: message_id -> run_id Reply-Map roundtrip ────────────────────────
def test_reply_map():
    print("Test 4: message_id → run_id Reply-Map")
    _state.answers_map_set(99812, "res_map")
    check("map lookup trifft", _state.answers_map_get(99812) == "res_map")
    check("unbekannte mid → None", _state.answers_map_get(11111) is None)


# ── Test 5: Detail-Output listet Fragen nummeriert + Anleitung ──────────────
def test_detail_output():
    print("Test 5: format_run_summary zeigt Entscheidungsgrundlage")
    result = {
        "run_id": "res_fmt", "status": "needs_input", "intent": "Plane Bewerbung",
        "steps": {"planner": {"n_steps": 3}, "validator": {"verdict": "WARN", "n_gates": 2}},
        "offene_fragen": [
            {"id": "daten_1", "frage": "Für welche Stelle?", "typ": "daten"},
            {"id": "gate_1", "frage": "Freigabe CV speichern", "typ": "approval"},
        ],
    }
    text = _notify.format_run_summary(result)
    check("Fragen nummeriert (1) ... 2) ...)", "1)" in text and "2)" in text)
    check("Antwort-Anleitung vorhanden", "Antworte als Reply" in text)
    check("Abbruch-Hinweis vorhanden", " x " in text or "x  zum Abbrechen" in text)
    check("Unter 4096 Zeichen", len(text) <= 4096)


if __name__ == "__main__":
    test_needs_input_persists_questions()
    test_resume_with_answers()
    test_resume_cancel()
    test_reply_map()
    test_detail_output()
    print()
    print(f"=== {len(_passed)} PASSED, {len(_failed)} FAILED ===")
    if _failed:
        print("FEHLGESCHLAGEN:", _failed)
        sys.exit(1)
