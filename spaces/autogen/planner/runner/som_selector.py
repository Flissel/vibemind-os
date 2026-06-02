"""SoM Planner — Phase 2: Matrix-Aggregation via AutoGen SelectorGroupChat.

Nach der RoundRobin-Planung (plan/exec/verdict liegen vor) baut hier ein echtes
AutoGen-Team die Execution-Matrix:

  - Aggregator: ruft das matrix-build-Tool auf (deterministische Topologie) und
    meldet das Ergebnis.
  - Reviewer:  prüft die gebaute Matrix gegen die Pläne (alle Schritte als Nodes?
    depends_on als edges? approval_gates übernommen?) und gibt Feedback oder OK.

Der SelectorGroupChat wählt dynamisch zwischen beiden — das ist die echte
Multi-Agent-Interaktion (bauen ↔ prüfen) für die SelectorGroupChat gemacht ist.
Die Matrix-TOPOLOGIE bleibt deterministisch (matrix.py); das Team validiert +
reichert an, statt sie zu erfinden.

Aufruf:
    python spaces/autogen/planner/runner/som_selector.py --run-id <id>
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import json
import sys
from pathlib import Path

_PLANNER = Path(__file__).resolve().parents[1]


def _load(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("som_state_sel", _PLANNER / "_lib" / "state.py")
_matrix = _load("som_matrix_sel", _PLANNER / "_lib" / "matrix.py")
_team = _load("som_team_sel", _PLANNER / "_lib" / "som_team.py")


async def build_matrix_via_team(run_id: str, max_messages: int = 10) -> dict:
    """Baut die Matrix über einen AutoGen SelectorGroupChat (Aggregator+Reviewer)."""
    from autogen_agentchat.agents import AssistantAgent
    from autogen_agentchat.teams import SelectorGroupChat

    # ── Tools (sync python-funcs, AutoGen wrapped sie auto + läuft sie im executor) ──
    def matrix_build(run_id: str) -> dict:
        """Baut die Execution-Matrix deterministisch aus plan/exec/verdict und
        schreibt sie als matrix.yaml. Gibt eine Zusammenfassung zurück."""
        m = _matrix.build_and_write(run_id)
        if "error" in m:
            return m
        return {
            "ok": True,
            "n_nodes": len(m["nodes"]),
            "n_edges": len(m["edges"]),
            "n_gates": len(m.get("approval_gates", [])),
            "status": m["status"],
            "node_ids": [n["id"] for n in m["nodes"]],
            "edges": [f"{e['from']}->{e['to']}" for e in m["edges"]],
        }

    def matrix_read(run_id: str) -> dict:
        """Liest die gebaute matrix.yaml zur Prüfung."""
        return _state.plan_read(run_id, "matrix")

    def matrix_analyze(run_id: str) -> dict:
        """Deterministische Qualitäts-Befunde über die Matrix: verwaiste Nodes,
        Zyklen, Wurzeln/Blätter, kritischer Pfad, ungültige Approval-Gates.
        Das sind FAKTEN — nutze sie, erfinde keine eigenen Topologie-Mängel."""
        m = _state.plan_read(run_id, "matrix")
        if not m:
            return {"error": "keine matrix.yaml"}
        return _matrix.analyze_matrix(m)

    def plan_read(run_id: str, kind: str) -> dict:
        """Liest plan/exec/verdict für die inhaltliche Plan-Qualitätsprüfung."""
        return _state.plan_read(run_id, kind)

    mc = _team.make_model_client()

    aggregator = AssistantAgent(
        name="Aggregator",
        model_client=mc,
        tools=[matrix_build, matrix_read],
        description="Baut die Execution-Matrix aus den Plänen via matrix_build-Tool.",
        system_message=(
            "Du bist der Aggregator. Aufgabe (EINMAL): rufe matrix_build(run_id) auf "
            "um die Execution-Matrix deterministisch zu bauen. Berichte dann in EINEM "
            "Satz das Ergebnis (Anzahl Nodes/Edges/Gates, status) und übergib an den "
            "Struktur-Prüfer. Kein weiteres Bauen, keine Diskussion über Plan-Inhalt. "
            "Antworte knapp."
        ),
    )

    reviewer = AssistantAgent(
        name="Reviewer",
        model_client=mc,
        tools=[matrix_analyze, plan_read],
        description="Führt eine feste Struktur-Checkliste über die Matrix aus.",
        system_message=(
            "Du bist der Struktur-Prüfer. Führe GENAU diesen Algorithmus aus, nichts "
            "anderes. Du fällst KEIN eigenes Urteil über Vollständigkeit, du PRÜFST "
            "nur diese 4 mechanischen Bedingungen via matrix_analyze(run_id):\n\n"
            "  C1 = (isolated_nodes ist leer)\n"
            "  C2 = (has_cycle ist false)\n"
            "  C3 = (approval_gates_invalid ist leer)\n"
            "  C4 = (n_nodes > 0)\n\n"
            "Rufe matrix_analyze EINMAL auf. Dann:\n"
            "- Wenn C1 UND C2 UND C3 UND C4 alle wahr sind → die Matrix-Struktur ist "
            "gültig. Antworte EXAKT in diesem Format und sonst nichts:\n"
            "  'Struktur gültig: <n_nodes> Nodes, <n_edges> Edges, kritischer Pfad "
            "<critical_path_length>, alle Gates gültig, keine verwaisten Nodes, kein "
            "Zyklus. MATRIX_OK'\n"
            "- Wenn eine Bedingung falsch ist → nenne welche (C1/C2/C3) und die "
            "betroffenen Nodes. KEIN MATRIX_OK.\n\n"
            "VERBOTEN: über fehlende Daten, Email-Adressen, Nutzer-Entscheidungen, "
            "Plan-Inhalt oder 'könnte man verbessern' zu reden. Das ist NICHT deine "
            "Aufgabe — du prüfst NUR die 4 Struktur-Bedingungen. Fehlende Eingabe-Daten "
            "sind per Definition KEIN Struktur-Mangel. Halte dich strikt an C1-C4."
        ),
    )

    team = SelectorGroupChat(
        participants=[aggregator, reviewer],
        model_client=mc,
        termination_condition=_team.make_termination("MATRIX_OK", max_messages),
        allow_repeated_speaker=False,
        selector_prompt=(
            "Wähle den nächsten Sprecher. Der Aggregator baut die Matrix zuerst. "
            "Danach prüft der Reviewer. Verfügbare Rollen:\n{roles}\n\n{history}\n\n"
            "Wähle aus {participants} — zuerst Aggregator (bauen), dann Reviewer (prüfen). "
            "Nur den Rollennamen zurückgeben."
        ),
    )

    task = (
        f"Baue und prüfe die Execution-Matrix für run_id='{run_id}'. "
        f"Aggregator: rufe matrix_build('{run_id}') auf. Reviewer: prüfe das Ergebnis."
    )
    out = await _team.run_team(team, task)

    # Die Matrix wurde vom Tool in matrix.yaml geschrieben — lese sie als Wahrheit zurück
    matrix = _state.plan_read(run_id, "matrix")
    return {
        "team": out,
        "matrix_built": bool(matrix and matrix.get("nodes")),
        "n_nodes": len(matrix.get("nodes", [])) if matrix else 0,
        "n_edges": len(matrix.get("edges", [])) if matrix else 0,
        "reviewer_ok": "MATRIX_OK" in (out.get("last_text") or ""),
    }


def run(run_id: str) -> dict:
    return asyncio.run(build_matrix_via_team(run_id))


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    import os
    os.environ.setdefault("SOM_STATE_ROOT", str(_PLANNER / "state"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = run(args.run_id)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
