"""Phase D — Tests für den LangGraph-Resume-Loop (som_graph).

Der StateGraph modelliert plan→validate→(needs_input? interrupt)→resume→re-plan
über den vorhandenen YAML-State als durable Checkpoint-Backing. Diese Tests
prüfen den *Vertrag* (gleiche Beobachtbarkeit wie der imperative resume) PLUS
die neue durable-Eigenschaft: ein Checkpoint überlebt einen Prozess-/Modul-
Neustart und kann von dort fortgesetzt werden.

Deterministisch, kein LLM (fake _call_agent wie in test_resume.py). Aufruf:
    voice/.venv312/Scripts/python spaces/autogen/planner/tests/test_graph_resume.py
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
os.environ["SOM_STATE_ROOT"] = tempfile.mkdtemp(prefix="som_graph_test_")
os.environ["SOM_NOTIFY"] = "0"      # NIE echtes Telegram im Test
os.environ["SOM_LANGGRAPH"] = "1"   # Graph-Pfad aktiv


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("g_state", _PLANNER / "_lib" / "state.py")

_passed = []
_failed = []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


# Fake-Agent wie in test_resume.py: solange das Zielprofil unbekannt ist meldet
# der Executor fehlende Daten (needs_input); sobald die Antwort im Kontext steht,
# ist alles vollständig (ready).
def _make_agent():
    def fake_agent(role, user_input, run_id=""):
        beantwortet = "BEANTWORTETE RÜCKFRAGEN" in user_input
        if role == "planner":
            return {"intent": "x", "rationale": "r",
                    "steps": [{"id": "s1", "beschreibung": "cv entwurf", "capability": "content_creation",
                               "braucht_daten": [], "liefert_daten": ["cv"], "depends_on": []}],
                    "offene_fragen": []}
        if role == "executor":
            fehlen = [] if beantwortet else ["Zielstellen-Profil unbekannt?"]
            return {"steps": [{"plan_step_id": "s1", "execution_target": "openfang:writer",
                               "konkrete_args": {}, "reihenfolge": 1, "parallel_ok": False}],
                    "benoetigte_daten_fehlen": fehlen}
        if role == "validator":
            return {"verdict": "PASS" if beantwortet else "WARN",
                    "findings": [], "approval_gates": [], "feedback_fuer_planner": None}
        return {}
    return fake_agent


def _fresh_graph_module():
    """Lädt som_graph FRISCH (simuliert einen neuen Prozess/Worker) + injiziert
    den Fake-Agent in das von ihm genutzte som_core."""
    mod = _load("g_graph", _PLANNER / "runner" / "som_graph.py")
    mod._core._call_agent = _make_agent()  # Fake in das vom Graph genutzte core
    return mod


# ── Test 1: erster Lauf pausiert bei needs_input (interrupt) ─────────────────
def test_graph_pauses_on_needs_input():
    print("Test 1: Graph pausiert bei needs_input + persistiert answers_needed.yaml")
    g = _fresh_graph_module()
    r = g.run("Plane Bewerbung", run_id="g_pause")
    check("Status needs_input", r.get("status") == "needs_input")
    check("offene_fragen im Result", bool(r.get("offene_fragen")))
    data = _state.answers_read("g_pause")
    check("answers_needed.yaml (awaiting_answers)", data.get("status") == "awaiting_answers")
    check("Checkpoint auf Platte", (_state.run_dir("g_pause") / "langgraph_checkpoint.json").exists())


# ── Test 2: resume(answers) setzt am interrupt fort → ready, run_id stabil ───
def test_graph_resume_to_ready():
    print("Test 2: resume(answers) → re-plan ab Checkpoint → ready, run_id stabil")
    g = _fresh_graph_module()
    r1 = g.run("Plane Bewerbung", run_id="g_go")
    v_before = _state.run_meta("g_go").get("versions", {}).get("plan", 0)
    r2 = g.resume("g_go", answers={"1": "AI-Vollzeit Konzern"})
    check("Vor Resume needs_input", r1.get("status") == "needs_input")
    check("Nach Resume ready", r2.get("status") == "ready")
    check("run_id stabil", r2.get("run_id") == "g_go")
    v_after = _state.run_meta("g_go").get("versions", {}).get("plan", 0)
    check("Plan-Version gebumpt (re-plan lief)", v_after > v_before)
    ans = _state.answers_read("g_go")
    check("Antwort eingetragen", any((q.get("antwort") or "").strip() for q in ans.get("questions") or []))


# ── Test 3: DURABLE — resume aus FRISCHEM Modul (simulierter Prozess-Neustart) ─
def test_graph_resume_survives_restart():
    print("Test 3: Checkpoint überlebt Prozess-Neustart (frisches Modul resumed)")
    g1 = _fresh_graph_module()
    r1 = g1.run("Plane Bewerbung", run_id="g_restart")
    check("1. Prozess: needs_input", r1.get("status") == "needs_input")
    del g1  # alter "Prozess" weg — In-Memory-State verloren, nur Platte bleibt
    g2 = _fresh_graph_module()  # NEUER "Prozess" — lädt Checkpoint von Platte
    r2 = g2.resume("g_restart", answers={"1": "Werkstudent Startup"})
    check("2. Prozess resumed → ready", r2.get("status") == "ready")
    check("run_id stabil über Neustart", r2.get("run_id") == "g_restart")


# ── Test 4: Kill-Switch SOM_LANGGRAPH=0 → som_core.resume nutzt NICHT den Graph ─
def test_killswitch_falls_back_to_imperative():
    print("Test 4: SOM_LANGGRAPH=0 → som_core.resume bleibt imperativer Pfad")
    _core = _load("g_core_ks", _PLANNER / "runner" / "som_core.py")
    _core._call_agent = _make_agent()
    old = os.environ.get("SOM_LANGGRAPH")
    os.environ["SOM_LANGGRAPH"] = "0"
    try:
        _core.run("Plane Bewerbung", run_id="g_ks")
        r = _core.resume("g_ks", answers={"1": "AI-Vollzeit"})
    finally:
        if old is None:
            os.environ.pop("SOM_LANGGRAPH", None)
        else:
            os.environ["SOM_LANGGRAPH"] = old
    check("Imperativer Pfad erreicht ready", r.get("status") == "ready")
    check("run_id stabil", r.get("run_id") == "g_ks")


if __name__ == "__main__":
    test_graph_pauses_on_needs_input()
    test_graph_resume_to_ready()
    test_graph_resume_survives_restart()
    test_killswitch_falls_back_to_imperative()
    print()
    print(f"=== {len(_passed)} PASSED, {len(_failed)} FAILED ===")
    if _failed:
        print("FEHLGESCHLAGEN:", _failed)
        sys.exit(1)
