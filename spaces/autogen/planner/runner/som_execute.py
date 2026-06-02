"""SoM Planner — Phase 6: Execution-Entrypoint (führt einen ready-Plan aus).

run_execute(run_id): walkt die exec.yaml-Schritte in Topo-Reihenfolge, reicht
Outputs weiter, PAUSIERT vor Approval-Schritten (kein Auto-Versand) und pusht den
Status per Telegram. continue_run(run_id, approved): nimmt einen am Gate
pausierten Lauf wieder auf (von som_core.resume bei einer Exec-Freigabe gerufen).

SICHERHEIT: nur status==ready ausführbar; externe Schritte nur nach Approval;
sensible Werte nicht in den Telegram-Status (notify maskiert).

Aufruf:
    python runner/som_execute.py --run-id run_0022
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

_PLANNER = Path(__file__).resolve().parents[1]


def _load(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("somx_state", _PLANNER / "_lib" / "state.py")
_executor = _load("somx_executor", _PLANNER / "_lib" / "executor.py")
_notify = _load("somx_notify", _PLANNER / "_lib" / "notify.py")


def _ordered_steps(run_id: str):
    exec_plan = _state.plan_read(run_id, "exec")
    matrix = _state.plan_read(run_id, "matrix")
    verdict = _state.plan_read(run_id, "verdict")
    steps = exec_plan.get("steps") or []
    by_id = {(s.get("plan_step_id") or s.get("id")): s for s in steps}
    order = _executor.topo_order(steps, matrix.get("edges") or [])
    return [by_id[i] for i in order if i in by_id], matrix, verdict


def _build_state(run_id: str) -> dict:
    """state-dict aus bereits ausgeführten Schritten (für Output-Weitergabe)."""
    es = _state.exec_state_read(run_id).get("steps") or {}
    state: dict = {}
    for sid, st in es.items():
        if st.get("status") == "done" and st.get("output") is not None:
            state[sid] = st["output"]
    return state


def _notify_exec(run_id: str, status: str, detail: str = "") -> None:
    meta = _state.run_meta(run_id)
    result = {"run_id": run_id, "status": status, "intent": meta.get("intent", ""),
              "steps": {}, "_exec_detail": detail}
    try:
        _notify.send(_format_exec_msg(result, detail))
    except Exception:  # noqa: BLE001
        pass


def _format_exec_msg(result: dict, detail: str) -> str:
    intent = " ".join((result.get("intent") or "").split())[:90]
    status = result.get("status")
    emoji = {"executing": "⚙️", "awaiting_approval": "⏸️", "done": "✅",
             "partial": "⚠️", "failed": "❌", "cancelled": "🚫"}.get(status, "•")
    head = f"{emoji} {intent}"
    line = {"executing": "Ich führe den Plan jetzt aus…",
            "awaiting_approval": "Ich brauche deine Freigabe:",
            "done": "Plan ausgeführt.",
            "partial": "Teilweise ausgeführt (einige Schritte offen).",
            "failed": "Ausführung fehlgeschlagen.",
            "cancelled": "Ausführung abgebrochen."}.get(status, status)
    msg = f"{head}\n{line}"
    if detail:
        msg += f"\n{detail}"
    if status == "awaiting_approval":
        msg += "\n↩️ Antworte mit:  antwort: ja   (oder  nein)"
    return msg


def run_execute(run_id: str) -> dict:
    """Führt den ready-Plan aus, bis fertig ODER bis ein Approval-Gate pausiert."""
    meta = _state.run_meta(run_id)
    if meta.get("status") not in ("ready", "awaiting_approval", "executing"):
        return {"run_id": run_id, "status": "blocked",
                "error": f"Nur ready-Pläne ausführbar (Status: {meta.get('status')})."}

    steps, matrix, verdict = _ordered_steps(run_id)
    _state.exec_state_init(run_id, [s.get("plan_step_id") or s.get("id") for s in steps])
    _state.run_meta_update(run_id, status="executing", phase="executing")
    _notify_exec(run_id, "executing")

    intent = meta.get("intent", "")
    for step in steps:
        sid = step.get("plan_step_id") or step.get("id")
        es = _state.exec_state_read(run_id).get("steps", {}).get(sid, {})
        if es.get("status") == "done":
            continue  # Resume: schon erledigt

        # Approval-Gate? (inkl. Defense-in-depth für schreibende/externe Targets)
        if _executor.needs_approval(sid, matrix, verdict, step=step):
            if es.get("status") != "approved":
                _state.exec_step_update(run_id, sid, status="awaiting_approval")
                _state.run_meta_update(run_id, status="awaiting_approval",
                                       phase="executing", pending_step=sid)
                grund = next((g.get("grund") for g in (verdict.get("approval_gates") or [])
                              if isinstance(g, dict) and (g.get("plan_step_id") == sid)), "")
                _notify_exec(run_id, "awaiting_approval", _human_gate(sid, grund))
                return {"run_id": run_id, "status": "awaiting_approval", "pending_step": sid}

        # Ausführen
        state = _build_state(run_id)
        _state.exec_step_update(run_id, sid, status="running")
        res = _executor.run_step(step, state, intent=intent)
        _state.exec_step_update(run_id, sid, status=res["status"],
                                output=res.get("output"), error=res.get("error"),
                                note=res.get("note"))
        if res["status"] == "failed":
            _state.run_meta_update(run_id, status="partial", phase="executing")
            _notify_exec(run_id, "partial", f"Schritt '{sid}' fehlgeschlagen: {res.get('error','')[:120]}")
            return {"run_id": run_id, "status": "partial", "failed_step": sid}

    # alle durch
    es_all = _state.exec_state_read(run_id).get("steps", {})
    unsupported = [s for s, v in es_all.items() if v.get("status") == "unsupported"]
    final = "partial" if unsupported else "done"
    _state.run_meta_update(run_id, status=final, phase="done")
    detail = (f"{len(es_all)} Schritte; {len(unsupported)} noch nicht unterstützt: {unsupported}"
              if unsupported else f"{len(es_all)} Schritte erledigt.")
    _notify_exec(run_id, final, detail)
    return {"run_id": run_id, "status": final, "steps": es_all}


def _human_gate(sid: str, grund: str) -> str:
    if "speicher" in (grund or "").lower() or "schreib" in (grund or "").lower():
        return "Darf ich das Dokument speichern?"
    if "versand" in (grund or "").lower() or "senden" in (grund or "").lower():
        return "Darf ich das absenden?"
    return f"Schritt '{sid}' braucht deine Freigabe."


def continue_run(run_id: str, approved: bool) -> dict:
    """Nimmt einen am Approval-Gate pausierten Exec-Lauf wieder auf.
    Von som_core.resume gerufen wenn die Freigabe-Antwort (ja/nein) kommt."""
    meta = _state.run_meta(run_id)
    pending = meta.get("pending_step")
    if not approved:
        if pending:
            _state.exec_step_update(run_id, pending, status="skipped")
        _state.run_meta_update(run_id, status="cancelled", phase="done", pending_step=None)
        _notify_exec(run_id, "cancelled", f"Schritt '{pending}' nicht freigegeben.")
        return {"run_id": run_id, "status": "cancelled"}
    if pending:
        _state.exec_step_update(run_id, pending, status="approved")
    _state.run_meta_update(run_id, pending_step=None)
    return run_execute(run_id)  # läuft ab dem freigegebenen Schritt weiter


if __name__ == "__main__":
    try:
        sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--approve", choices=["ja", "nein"], help="Gate-Freigabe fortsetzen")
    args = p.parse_args()
    if args.approve:
        out = continue_run(args.run_id, approved=(args.approve == "ja"))
    else:
        out = run_execute(args.run_id)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
