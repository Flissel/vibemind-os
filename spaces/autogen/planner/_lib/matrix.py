"""SoM Planner — Execution-Matrix Builder.

Baut die Execution-Matrix aus plan/exec/verdict.yaml. Die TOPOLOGIE ist
deterministisch (nodes aus plan.steps, edges aus depends_on, approval aus
verdict.approval_gates) — kein LLM nötig, kein Nicht-Determinismus.

Diese deterministische Funktion ist die Wahrheit. Der Aggregator-Agent (AutoGen)
ruft sie als TOOL auf — er muss die Matrix nicht "erfinden", sondern lässt sie
bauen und kann sie optional kommentieren/anreichern (Datenfluss-Labels).

Datenfluss-Labels (welche konkreten Daten über eine Kante fließen) sind die
einzige unscharfe Komponente — via difflib (stdlib) als Annotation auf
bestehenden depends_on-Kanten, nie als neue Kante.
"""

from __future__ import annotations

import difflib
import importlib.util
from pathlib import Path
from typing import Any

_PLANNER = Path(__file__).resolve().parents[1]


def _load(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("som_state_mx", _PLANNER / "_lib" / "state.py")
_schemas = _load("som_schemas_mx", _PLANNER / "_lib" / "schemas.py")


def _best_dataflow_label(from_step: dict, to_step: dict) -> str:
    """Fuzzy-Match: welches liefert_daten von A passt am besten zu einem
    braucht_daten von B? Gibt das gematchte Label oder '' zurück. Schmückt nur
    eine BEREITS existierende depends_on-Kante, erzeugt nie eine neue."""
    liefert = from_step.get("liefert_daten") or []
    braucht = to_step.get("braucht_daten") or []
    best_score = 0.0
    best_label = ""
    for l in liefert:
        for b in braucht:
            score = difflib.SequenceMatcher(None, str(l).lower(), str(b).lower()).ratio()
            if score > best_score:
                best_score = score
                best_label = str(l)
    return best_label if best_score >= 0.45 else ""


def build_matrix(run_id: str, plan: dict, exec_plan: dict, verdict: dict) -> dict:
    """Deterministischer Matrix-Build. Validiert gegen MatrixSchema vor Rückgabe."""
    steps = plan.get("steps", []) or []
    step_by_id = {s.get("id"): s for s in steps if s.get("id")}

    approval_ids = {
        g.get("plan_step_id")
        for g in (verdict.get("approval_gates") or [])
        if g.get("plan_step_id")
    }

    # exec-Infos pro plan_step_id (execution_target, reihenfolge, parallel_ok)
    exec_by_id: dict[str, dict] = {}
    for es in (exec_plan.get("steps") or []):
        pid = es.get("plan_step_id")
        if pid:
            exec_by_id[pid] = es

    # ── Nodes (1:1 aus plan.steps) ──────────────────────────────────────────
    nodes = []
    for s in steps:
        sid = s.get("id")
        if not sid:
            continue
        es = exec_by_id.get(sid, {})
        nodes.append({
            "id": sid,
            "quelle": "planner",
            "beschreibung": s.get("beschreibung", ""),
            "braucht_daten": s.get("braucht_daten") or [],
            "liefert_daten": s.get("liefert_daten") or [],
            "approval_noetig": sid in approval_ids,
            # zusätzliche Ausführungs-Annotationen (nicht im MatrixNode-Schema,
            # aber nützlich — landen in einem separaten exec_meta-Block)
        })

    # ── Edges (aus depends_on, deterministisch) + Datenfluss-Label ──────────
    edges = []
    for s in steps:
        sid = s.get("id")
        for dep in (s.get("depends_on") or []):
            if dep not in step_by_id:
                continue  # depends_on auf nicht-existenten Schritt → skip (Plan-Bug)
            label = _best_dataflow_label(step_by_id[dep], s)
            edges.append({"from": dep, "to": sid, "daten": label})

    # ── exec_meta: Reihenfolge + Parallelität pro Node (Scheduling-Info) ─────
    exec_meta = {
        sid: {
            "execution_target": es.get("execution_target"),
            "reihenfolge": es.get("reihenfolge"),
            "parallel_ok": es.get("parallel_ok", False),
        }
        for sid, es in exec_by_id.items()
    }

    # ── Status ableiten ──────────────────────────────────────────────────────
    v = (verdict.get("verdict") or "").upper()
    status = "ready" if v == "PASS" else ("validated" if v == "WARN" else "draft")

    matrix = {
        "run_id": run_id,
        "intent": plan.get("intent", ""),
        "nodes": nodes,
        "edges": edges,
        "status": status,
    }

    # ── Pydantic-Validierung (fail-fast statt stiller Fehlstruktur) ──────────
    _schemas.MatrixSchema(**matrix)  # wirft bei Schema-Verletzung

    # exec_meta + approval_gates als Zusatz-Blöcke (nicht im Pydantic-Kern,
    # aber für den Executor/die Visualisierung wertvoll)
    matrix["exec_meta"] = exec_meta
    matrix["approval_gates"] = [
        {"node": g.get("plan_step_id"), "grund": g.get("grund", "")}
        for g in (verdict.get("approval_gates") or [])
    ]
    return matrix


def analyze_matrix(matrix: dict) -> dict:
    """Deterministische Qualitäts-Befunde über eine gebaute Matrix — als
    Faktenbasis für den Reviewer (damit er über echte Befunde urteilt, nicht rät).
    KEIN LLM. Findet: verwaiste Nodes, Zyklen, Wurzeln/Blätter, kritischer Pfad."""
    nodes = matrix.get("nodes", [])
    edges = matrix.get("edges", [])
    node_ids = [n["id"] for n in nodes]
    id_set = set(node_ids)

    incoming: dict[str, int] = {n: 0 for n in node_ids}
    outgoing: dict[str, int] = {n: 0 for n in node_ids}
    adj: dict[str, list[str]] = {n: [] for n in node_ids}
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f in id_set and t in id_set:
            outgoing[f] += 1
            incoming[t] += 1
            adj[f].append(t)

    # Verwaiste Nodes: kein eingehend UND kein ausgehend (außer bei nur-1-Node)
    isolated = [n for n in node_ids if incoming[n] == 0 and outgoing[n] == 0 and len(node_ids) > 1]
    roots = [n for n in node_ids if incoming[n] == 0]        # Startpunkte
    leaves = [n for n in node_ids if outgoing[n] == 0]       # Endpunkte

    # Zyklus-Check (Kahn): wenn nicht alle Nodes topologisch sortierbar → Zyklus
    indeg = dict(incoming)
    queue = [n for n in node_ids if indeg[n] == 0]
    visited = 0
    order = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        visited += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    has_cycle = visited < len(node_ids)

    # Längster Pfad (kritischer Pfad) über die topo-Ordnung
    longest = {n: 1 for n in node_ids}
    for n in order:
        for m in adj[n]:
            longest[m] = max(longest[m], longest[n] + 1)
    critical_len = max(longest.values()) if longest else 0

    gates = [g.get("node") for g in matrix.get("approval_gates", [])]
    gates_valid = [g for g in gates if g in id_set]
    gates_invalid = [g for g in gates if g not in id_set]

    return {
        "n_nodes": len(node_ids),
        "n_edges": len(edges),
        "isolated_nodes": isolated,
        "roots": roots,
        "leaves": leaves,
        "has_cycle": has_cycle,
        "critical_path_length": critical_len,
        "approval_gates_valid": gates_valid,
        "approval_gates_invalid": gates_invalid,
    }


def build_and_write(run_id: str) -> dict:
    """Liest plan/exec/verdict aus dem State, baut Matrix, schreibt matrix.yaml."""
    plan = _state.plan_read(run_id, "plan")
    exec_plan = _state.plan_read(run_id, "exec")
    verdict = _state.plan_read(run_id, "verdict")
    if not plan or not plan.get("steps"):
        return {"error": "kein plan.yaml mit steps gefunden", "run_id": run_id}
    matrix = build_matrix(run_id, plan, exec_plan, verdict)
    _state.plan_write(run_id, "matrix", matrix)
    return matrix


if __name__ == "__main__":
    import io
    import sys
    import os
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    os.environ.setdefault(
        "SOM_STATE_ROOT",
        str(_PLANNER / "state"),
    )
    # Selbsttest gegen einen echten Run
    rid = sys.argv[1] if len(sys.argv) > 1 else "test_active_full"
    m = build_and_write(rid)
    if "error" in m:
        print("FEHLER:", m["error"])
    else:
        print(f"Matrix für {rid}: {len(m['nodes'])} nodes, {len(m['edges'])} edges, "
              f"{len(m['approval_gates'])} gates, status={m['status']}")
        labeled = sum(1 for e in m["edges"] if e["daten"])
        print(f"  edges mit Datenfluss-Label: {labeled}/{len(m['edges'])}")
        for e in m["edges"][:6]:
            print(f"    {e['from']} → {e['to']}  [{e['daten'][:40]}]")
