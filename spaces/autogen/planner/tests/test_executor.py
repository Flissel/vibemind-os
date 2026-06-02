"""Regression-Tests für Phase 6 — Execution-Rückkanal (deterministisch, kein LLM,
kein echter OpenFang-Call: run_step bekommt einen caller-Mock).

Deckt:
- topo_order: Schritte nach depends_on/edges geordnet
- resolve_args: input_X / {{state.X}} aus prior outputs gefüllt
- needs_approval: Matrix approval_noetig ODER verdict approval_gate
- run_execute: nur ready ausführbar; Gate pausiert; alle Schritte → done
- continue_run: approved läuft weiter; nein → cancelled (kein Schreiben)
- Output-Weitergabe end-to-end (Schritt B sieht Output von A)

Aufruf:
    python spaces/autogen/planner/tests/test_executor.py
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
os.environ["SOM_STATE_ROOT"] = tempfile.mkdtemp(prefix="som_exec_test_")
os.environ["SOM_NOTIFY"] = "0"


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("te_state", _PLANNER / "_lib" / "state.py")
_executor = _load("te_executor", _PLANNER / "_lib" / "executor.py")
_somx = _load("te_somx", _PLANNER / "runner" / "som_execute.py")

_passed, _failed = [], []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


# ── Test 1: topo_order ───────────────────────────────────────────────────────
def test_topo():
    print("Test 1: topo_order (depends_on Reihenfolge)")
    steps = [{"plan_step_id": "c", "reihenfolge": 3}, {"plan_step_id": "a", "reihenfolge": 1},
             {"plan_step_id": "b", "reihenfolge": 2}]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
    order = _executor.topo_order(steps, edges)
    check("a vor b vor c", order == ["a", "b", "c"])
    # parallele Start-Schritte nach reihenfolge
    steps2 = [{"plan_step_id": "x", "reihenfolge": 1}, {"plan_step_id": "y", "reihenfolge": 1},
              {"plan_step_id": "z", "reihenfolge": 2}]
    edges2 = [{"from": "x", "to": "z"}, {"from": "y", "to": "z"}]
    check("z kommt zuletzt (haengt von x,y)", _executor.topo_order(steps2, edges2)[-1] == "z")


# ── Test 2: resolve_args ─────────────────────────────────────────────────────
def test_resolve():
    print("Test 2: resolve_args (Output-Weitergabe)")
    state = {"cv_basis": "MARKDOWN-CV", "profil": {"sem": 6}}
    args = {"query": "fix", "input_cv_basis": "cv_basis", "templ": "{{state.profil}}"}
    out = _executor.resolve_args(args, state)
    check("input_cv_basis → state-Wert", out["input_cv_basis"] == "MARKDOWN-CV")
    check("{{state.profil}} aufgelöst", out["templ"] == {"sem": 6})
    check("normale args unveraendert", out["query"] == "fix")


# ── Test 3: needs_approval ───────────────────────────────────────────────────
def test_approval_detection():
    print("Test 3: needs_approval (Matrix ODER verdict)")
    matrix = {"nodes": [{"id": "write", "approval_noetig": True}, {"id": "read", "approval_noetig": False}]}
    verdict = {"approval_gates": [{"plan_step_id": "send", "grund": "Versand"}]}
    check("Matrix approval_noetig erkannt", _executor.needs_approval("write", matrix, verdict))
    check("verdict-gate erkannt", _executor.needs_approval("send", matrix, verdict))
    check("read braucht keine Freigabe", not _executor.needs_approval("read", matrix, verdict))


# ── Setup-Helper: einen ready-Run mit Artefakten anlegen ─────────────────────
def _make_run(run_id, *, with_gate=True):
    _state.run_create(run_id, "Test: CV aktualisieren")
    _state.run_meta_update(run_id, status="ready")
    # lesen + delta = read/analyse (rowboat-knowledge, kein Write-Target),
    # schreiben = openclaude-coder (Write → Defense-in-depth + Matrix-Gate).
    _state.plan_write(run_id, "exec", {"steps": [
        {"plan_step_id": "lesen", "execution_target": "openfang:rowboat-knowledge",
         "reihenfolge": 1, "konkrete_args": {"query": "cv"}},
        {"plan_step_id": "delta", "execution_target": "openfang:rowboat-knowledge",
         "reihenfolge": 2, "konkrete_args": {"query": "delta", "input_lesen": "lesen"}},
        {"plan_step_id": "schreiben", "execution_target": "openfang:openclaude-coder",
         "reihenfolge": 3, "konkrete_args": {"input_delta": "delta"}},
    ]})
    _state.plan_write(run_id, "matrix", {"nodes": [
        {"id": "lesen", "approval_noetig": False},
        {"id": "delta", "approval_noetig": False},
        {"id": "schreiben", "approval_noetig": with_gate},
    ], "edges": [{"from": "lesen", "to": "delta"}, {"from": "delta", "to": "schreiben"}]})
    _state.plan_write(run_id, "verdict", {"verdict": "WARN",
        "approval_gates": ([{"plan_step_id": "schreiben", "grund": "Dokument speichern"}] if with_gate else [])})


# WICHTIG: som_execute hält seine EIGENE executor-Modulinstanz (somx_executor).
# Der Mock muss auf _somx._executor.run_step patchen (nicht auf das hier geladene
# te_executor — das sind verschiedene Modul-Objekte). Wir injizieren einen
# caller, indem wir run_step ersetzen und an die echte Impl mit caller weiterleiten.
_REAL_RUN_STEP = _somx._executor.run_step


def _patch_caller(calls):
    def caller(target, message):
        calls.append((target, message))
        return f"OUT[{target}]"
    def patched(step, state, intent="", caller_unused=None):
        return _REAL_RUN_STEP(step, state, intent=intent, caller=caller)
    _somx._executor.run_step = patched


def _unpatch():
    _somx._executor.run_step = _REAL_RUN_STEP


# ── Test 4: run_execute pausiert am Gate ─────────────────────────────────────
def test_gate_pause():
    print("Test 4: run_execute pausiert vor Approval-Schritt")
    _make_run("exec_gate", with_gate=True)
    calls = []
    _patch_caller(calls)
    try:
        r = _somx.run_execute("exec_gate")
    finally:
        _unpatch()
    check("Status awaiting_approval", r.get("status") == "awaiting_approval")
    check("pausiert bei 'schreiben'", r.get("pending_step") == "schreiben")
    check("lesen+delta liefen (2 caller-calls)", len(calls) == 2)
    es = _state.exec_state_read("exec_gate")["steps"]
    check("lesen done", es["lesen"]["status"] == "done")
    check("schreiben awaiting_approval", es["schreiben"]["status"] == "awaiting_approval")


# ── Test 5: continue_run(approved) läuft weiter + Output-Weitergabe ──────────
def test_continue_approved():
    print("Test 5: continue_run(ja) → Schritt läuft → done")
    calls = []
    _patch_caller(calls)
    try:
        r = _somx.continue_run("exec_gate", approved=True)
    finally:
        _unpatch()
    check("Endstatus done", r.get("status") == "done")
    es = _state.exec_state_read("exec_gate")["steps"]
    check("schreiben jetzt done", es["schreiben"]["status"] == "done")
    check("alle 3 Schritte done", all(es[s]["status"] == "done" for s in ("lesen", "delta", "schreiben")))


# ── Test 6: continue_run(nein) → cancelled, nichts geschrieben ───────────────
def test_continue_rejected():
    print("Test 6: continue_run(nein) → cancelled, Schritt skipped")
    _make_run("exec_reject", with_gate=True)
    calls = []
    _patch_caller(calls)
    try:
        _somx.run_execute("exec_reject")          # pausiert
        r = _somx.continue_run("exec_reject", approved=False)
    finally:
        _unpatch()
    check("Status cancelled", r.get("status") == "cancelled")
    es = _state.exec_state_read("exec_reject")["steps"]
    check("schreiben skipped (nicht done)", es["schreiben"]["status"] == "skipped")


# ── Test 7b: Agent-Level-Fehler (success:false) → failed, nicht done ─────────
def test_agent_failure_detection():
    print("Test 7b: success:false / Fehler-Response → failed")
    check("dict success:false → failed", _executor._looks_failed({"success": False, "message": "x"}))
    check("dict success:true → ok", not _executor._looks_failed({"success": True, "response": "ok"}))
    check("'Fehler: ...' string → failed", _executor._looks_failed("Fehler: Remote end closed"))
    check("normale response → ok", not _executor._looks_failed("Hier ist dein CV-Delta"))


# ── Test 8: Defense-in-depth — Write-Target gated auch ohne Matrix-Flag ──────
def test_write_defense_in_depth():
    print("Test 8: schreibendes Target gated auch ohne Matrix-Gate")
    m = {"nodes": [{"id": "w", "approval_noetig": False}]}
    v = {"approval_gates": []}
    write_step = {"plan_step_id": "w", "execution_target": "openfang:openclaude-coder"}
    read_step = {"plan_step_id": "w", "execution_target": "openfang:rowboat-knowledge"}
    check("openclaude-coder gated (Defense-in-depth)", _executor.needs_approval("w", m, v, step=write_step))
    check("rowboat-knowledge laeuft frei", not _executor.needs_approval("w", m, v, step=read_step))


# ── Test 9: nur ready ausführbar ─────────────────────────────────────────────
def test_status_gating():
    print("Test 7: nur ready-Pläne ausführbar")
    _make_run("exec_block", with_gate=False)
    _state.run_meta_update("exec_block", status="needs_input")
    r = _somx.run_execute("exec_block")
    check("needs_input wird blockiert", r.get("status") == "blocked")


if __name__ == "__main__":
    test_topo()
    test_resolve()
    test_approval_detection()
    test_gate_pause()
    test_continue_approved()
    test_continue_rejected()
    test_agent_failure_detection()
    test_write_defense_in_depth()
    test_status_gating()
    print()
    print(f"=== {len(_passed)} PASSED, {len(_failed)} FAILED ===")
    if _failed:
        print("FEHLGESCHLAGEN:", _failed)
        sys.exit(1)
