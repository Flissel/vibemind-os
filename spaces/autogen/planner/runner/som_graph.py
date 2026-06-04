"""Phase D — SoM-Resume-Loop als LangGraph StateGraph (durable interrupt).

Modelliert den Phase-5-Loop (needs_input → answers → re-plan) als Graph, der
ausschließlich die PAUSE/RESUME-Kontrolle übernimmt — die schwere Pipeline läuft
im HAUPTTHREAD, NICHT in den Graph-Knoten:

    run():     [Hauptthread] _run_inner → needs_input?
                                  │ ja → app.invoke(...) treibt den Graph an den
                                  │       interrupt() (Checkpoint persistiert „pausiert")
                                  └ nein → fertig (ready/awaiting_approval/needs_human)

    resume():  app.invoke(Command(resume=answers)) → löst den interrupt (Checkpoint
               markiert „resumed") → [Hauptthread] _run_inner mit den Antworten als
               Fakten → re-plant → ggf. erneut needs_input → erneut an den interrupt

WARUM die Pipeline NICHT im Graph-Knoten läuft (teuer erarbeitet, 2026-06-04):
_run_inner nutzt prozess-weite FileLocks (_run_lock) + intensives YAML-I/O. In
einem pregel-Worker-Thread über die interrupt/resume-Grenze hinweg gibt LangGraph
den Lock-/Thread-Unwind nicht sauber frei → reproduzierbarer Deadlock (open()/
FileLock hängt). Die Knoten machen daher NUR lock-freie, I/O-arme Signal-Arbeit;
die Pipeline bleibt im Hauptthread. Der Graph ist die reine durable-Pause-Schicht.

Durable: Der Checkpoint (RunDirCheckpointer → state/runs/<run_id>/) hält fest, dass
ein Run am interrupt pausiert. thread_id == run_id. Ein Prozess-Neustart mitten im
Warten verliert nichts — resume() lädt den Checkpoint und löst genau diesen
interrupt. answers_needed.yaml bleibt zusätzlich Quelle der Wahrheit (Telegram/UI).

Reuse: som_core._run_inner (komplette Pipeline, NICHT dupliziert) + _lib/state +
notify. Aktiv nur bei SOM_LANGGRAPH=1; som_core.run/resume delegieren dann hierher,
sonst imperativer Pfad. Läuft synchron im (detached) Worker-Prozess.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

_PLANNER = Path(__file__).resolve().parents[1]


def _load(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("graph_state", _PLANNER / "_lib" / "state.py")
_core = _load("graph_core", _PLANNER / "runner" / "som_core.py")
_ckpt = _load("graph_ckpt", _PLANNER / "_lib" / "checkpointer.py")


class GraphState(TypedDict, total=False):
    run_id: str
    answers: dict   # vom resume via Command(resume=...) geliefert


# ── Knoten (NUR lock-freie Signal-Arbeit, KEIN _run_inner) ────────────────────
def _gate_node(state: GraphState) -> dict:
    """Pause-Knoten: pausiert via interrupt(). Beim Erstlauf (run) treibt der
    app.invoke den Graphen hierher und friert ein (Checkpoint = „pausiert"). Beim
    resume liefert Command(resume=...) das answers-dict als Rückgabe von
    interrupt() — das landet im State, der Graph endet. KEINE schwere Arbeit hier."""
    answers = interrupt({"run_id": state["run_id"]})
    return {"answers": answers or {}}


def _build_pause_app():
    """Mini-Graph mit genau einem Pause-Knoten. Dient NUR als durable
    interrupt/resume-Maschine — die Pipeline läuft außerhalb im Hauptthread."""
    g = StateGraph(GraphState)
    g.add_node("gate", _gate_node)
    g.add_edge(START, "gate")
    g.add_edge("gate", END)
    return g.compile(checkpointer=_ckpt.RunDirCheckpointer(_state.run_dir))


def _cfg(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _is_paused(out: dict) -> bool:
    """True wenn der letzte invoke am interrupt stehengeblieben ist."""
    return bool(out and out.get("__interrupt__"))


def _notify(result: dict) -> None:
    """Telegram-Push + message_id-Korrelation wie som_core.run/resume."""
    try:
        n = _load("graph_notify", _PLANNER / "_lib" / "notify.py")
        mid = n.notify_run(result)
        if mid and result.get("offene_fragen"):
            _state.answers_message_id(result["run_id"], mid)
            _state.answers_map_set(mid, result["run_id"])
    except Exception:  # noqa: BLE001 — Notify bricht den Run nie ab
        pass


def _answers_context(run_id: str) -> str:
    """BEANTWORTETE-RÜCKFRAGEN-Block aus answers_needed.yaml (gleiche Formulierung
    wie som_core.resume → Planner behandelt sie als Fakten)."""
    data = _state.answers_read(run_id)
    beantwortet = [q for q in (data.get("questions") or []) if (q.get("antwort") or "").strip()]
    if not beantwortet:
        return ""
    lines = ["BEANTWORTETE RÜCKFRAGEN (vom Nutzer bestätigt — als Fakten behandeln):"]
    for q in beantwortet:
        lines.append(f"- {q.get('frage', q.get('id'))}\n  → ANTWORT: {q['antwort']}")
    return "\n".join(lines)


# ── Öffentliche API (Pipeline im Hauptthread, Graph nur für die Pause) ────────
def run(intent: str, run_id: str | None = None) -> dict:
    """Erststart. Pipeline läuft im Hauptthread; bei needs_input wird der Graph
    an den interrupt getrieben (durable Pause-Checkpoint). run_id stabil."""
    if run_id is None:
        existing = len(_state.list_runs())
        run_id = f"run_{existing + 1:04d}"
    result = _core._run_inner(intent, run_id=run_id)   # Hauptthread, kein pregel
    if result.get("status") == "needs_input":
        app = _build_pause_app()
        app.invoke({"run_id": run_id}, _cfg(run_id))    # → interrupt(), Checkpoint
    _notify(result)
    return result


def resume(run_id: str, answers: dict[str, str] | None = None) -> dict:
    """Nimmt einen pausierten Lauf wieder auf: löst den interrupt (Checkpoint von
    Platte, überlebt Prozess-Neustart), trägt die Antworten ein und re-plant im
    Hauptthread. Bleibt der Plan erneut needs_input, wird wieder pausiert."""
    # 1. Antworten eintragen (answers_needed.yaml = Quelle der Wahrheit)
    if answers:
        _state.answers_set(run_id, answers)
    # 2. interrupt lösen — Checkpoint von Platte laden, Pause beenden. Best-effort:
    #    fehlt der Checkpoint (z.B. Run startete imperativ), ist das ok — die
    #    Pipeline re-plant trotzdem unten.
    try:
        app = _build_pause_app()
        app.invoke(Command(resume=answers or {}), _cfg(run_id))
    except Exception:  # noqa: BLE001 — kein/kaputter Checkpoint darf Resume nicht killen
        pass
    # 3. Re-Plan im Hauptthread mit den Antworten als Fakten
    result = _core._run_inner(run_id and _state.run_meta(run_id).get("intent", "") or "",
                              run_id=run_id, answers_context=_answers_context(run_id))
    # 4. Bleibt es needs_input → erneut an den interrupt (Pause für die nächste Frage)
    if result.get("status") == "needs_input":
        try:
            app = _build_pause_app()
            app.invoke({"run_id": run_id}, _cfg(run_id))
        except Exception:  # noqa: BLE001
            pass
    _notify(result)
    return result
