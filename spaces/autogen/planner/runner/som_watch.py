"""SoM Planner — Phase 3: Matrix-Watch (langsamer Loop).

Der "langsame" Loop des Zwei-Geschwindigkeiten-Designs: prüft periodisch ob sich
in einem Run die Pläne (plan/exec/verdict) seit dem letzten Matrix-Bau geändert
haben, und baut die Matrix bei Bedarf neu. Wird als OpenFang-Cron-Job registriert
(som_register_cron) und periodisch getriggert.

Deterministisch (kein LLM) — vergleicht run_meta.versions gegen die im
matrix.yaml gespeicherte Quell-Version.

Aufruf (einmalig, vom Cron getriggert):
    python spaces/autogen/planner/runner/som_watch.py [--run-id X | --all]
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from pathlib import Path

_PLANNER = Path(__file__).resolve().parents[1]


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("som_state_w", _PLANNER / "_lib" / "state.py")
_matrix = _load("som_matrix_w", _PLANNER / "_lib" / "matrix.py")

# Quell-Kinds die eine Matrix-Neuberechnung auslösen wenn sie sich ändern
_SOURCE_KINDS = ("plan", "exec", "verdict")


def _matrix_source_signature(run_id: str) -> dict:
    """Aktuelle Versionen der Quell-Artefakte (plan/exec/verdict)."""
    versions = _state.run_meta(run_id).get("versions", {})
    return {k: versions.get(k, 0) for k in _SOURCE_KINDS}


def _matrix_built_signature(run_id: str) -> dict | None:
    """Die Quell-Signatur die im matrix.yaml gespeichert ist (None wenn keine Matrix)."""
    m = _state.plan_read(run_id, "matrix")
    if not m:
        return None
    return m.get("_built_from")  # wird beim Bauen gesetzt (s.u.)


def needs_rebuild(run_id: str) -> bool:
    """True wenn plan/exec/verdict sich seit dem letzten Matrix-Bau geändert haben."""
    meta = _state.run_meta(run_id)
    # Kein Sinn bei FAIL/needs_human (kein gültiger Plan) oder ohne plan
    if not _state.plan_read(run_id, "plan").get("steps"):
        return False
    current = _matrix_source_signature(run_id)
    built = _matrix_built_signature(run_id)
    return built != current


def watch_run(run_id: str) -> dict:
    """Prüft einen Run, baut Matrix neu falls nötig. Idempotent."""
    if not needs_rebuild(run_id):
        return {"run_id": run_id, "rebuilt": False, "reason": "matrix aktuell"}
    matrix = _matrix.build_and_write(run_id)
    if "error" in matrix:
        return {"run_id": run_id, "rebuilt": False, "error": matrix["error"]}
    # Quell-Signatur in der frisch gebauten Matrix verankern (für nächsten Vergleich)
    matrix["_built_from"] = _matrix_source_signature(run_id)
    _state.plan_write(run_id, "matrix", matrix)
    a = _matrix.analyze_matrix(matrix)
    return {
        "run_id": run_id,
        "rebuilt": True,
        "n_nodes": a["n_nodes"],
        "n_edges": a["n_edges"],
        "isolated": a["isolated_nodes"],
        "has_cycle": a["has_cycle"],
    }


def watch_all() -> dict:
    """Prüft alle Runs (vom Cron-Job aufgerufen)."""
    results = []
    for rid in _state.list_runs():
        try:
            results.append(watch_run(rid))
        except Exception as e:  # noqa: BLE001
            results.append({"run_id": rid, "error": str(e)[:120]})
    rebuilt = [r for r in results if r.get("rebuilt")]
    return {"checked": len(results), "rebuilt": len(rebuilt), "details": rebuilt}


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    import os
    os.environ.setdefault("SOM_STATE_ROOT", str(_PLANNER / "state"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.run_id:
        out = watch_run(args.run_id)
    else:
        out = watch_all()
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
