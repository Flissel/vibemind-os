"""Brain Background-Loops Worker (Contention-Architektur-Fix 2026-06-08).

Faehrt die CPU-gebundenen Hintergrund-Loops (ContinuousThinking, MemoryConsolidator,
MCMPGardener, ClusterEngine, SelfSteerer, DiscourseEngine + die is_learner-gated
Writer + Log-Retrainer) in einem EIGENEN Prozess — OHNE uvicorn/HTTP-Server. So
konkurrieren die GIL-haltenden ML-/Mining-Loops nicht mehr mit dem async HTTP-Server
von brain-core (der mit BRAIN_BACKGROUND_LOOPS=0 läuft → loop-frei → responsiv).

Sauberer Split (kein create_app, keine Router): baut den State über die BESTEHENDEN
Builder _init_brain_state + _init_production_modules (dieselben wie die brain-core-
Lifespan), erzwingt BRAIN_BACKGROUND_LOOPS=1, und haelt den Prozess am Leben, damit
die Daemon-Loop-Threads laufen. KEIN Port-Bind, keine Routen.

Deploy: gleiches Image vibemind-brain-core:latest, command "python brain_loops_worker.py"
(Muster wie brain-autotrain-drain). Single-Writer: brain-core hat die Loops AUS,
dieser Worker AN → genau ein Owner der Writer/Consolidation.

Run:  python brain_loops_worker.py
Env:  BRAIN_BACKGROUND_LOOPS=1 (hier erzwungen), BRAIN_ROLE=learner (Writer an),
      QDRANT_URL, LLM-keys wie brain-core, BRAIN_THINK_INTERVAL_MS/CONSOLIDATION_INTERVAL_S.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# .env laden (wie start_server.py) — die Loops nutzen LLM-Keys via subagent_dispatcher.
try:
    from dotenv import load_dotenv
    _here = os.path.dirname(os.path.abspath(__file__))
    for _envp in (
        os.path.join(_here, ".env"),
        os.path.normpath(os.path.join(_here, "..", "..", "..", ".env")),
        os.path.normpath(os.path.join(_here, "..", "..", ".env")),
    ):
        if os.path.isfile(_envp):
            load_dotenv(_envp)
            print(f"[brain-loops] loaded env from {_envp}", flush=True)
            break
except Exception as _e:  # noqa: BLE001
    print(f"[brain-loops] dotenv unavailable: {_e}", flush=True)

# Loops in DIESEM Prozess AN — egal was die Umgebung sonst sagt.
os.environ["BRAIN_BACKGROUND_LOOPS"] = "1"


class _State:
    """Schlanker State-Halter (ersetzt FastAPI app.state — _init_*-Funktionen
    erwarten nur ein beliebiges Objekt mit Attributen)."""
    testing = False


def main() -> int:
    print("[brain-loops] Worker startet — baue State + Loops (kein HTTP)...", flush=True)
    from web.brain_server import _init_brain_state, _init_production_modules

    state = _State()
    _init_brain_state(state, testing=False)
    _init_production_modules(state)   # startet die Loops (BRAIN_BACKGROUND_LOOPS=1)

    # Log-Retrainer (lebt sonst in der HTTP-Lifespan) — hier optional nachziehen,
    # damit der Worker auch das inkrementelle EventRoutingHead-Training uebernimmt.
    if getattr(state, "event_routing_head", None) is not None:
        try:
            import asyncio
            from core.log_retrainer import periodic_retrainer_loop
            _interval = int(os.getenv("BRAIN_RETRAIN_INTERVAL_SECONDS", "3600"))
            if _interval > 0:
                # eigener Event-Loop nur fuer den Retrainer-Task
                import threading

                def _retrain_runner():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        periodic_retrainer_loop(state, interval_seconds=_interval))

                threading.Thread(target=_retrain_runner, daemon=True,
                                 name="LogRetrainer-worker").start()
                print(f"[brain-loops] Log retrainer gestartet (interval={_interval}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[brain-loops] Log retrainer konnte nicht starten: {e}", flush=True)

    print("[brain-loops] Loops laufen. Heartbeat (kein HTTP-Server in diesem Prozess).", flush=True)
    # Daemon-Threads am Leben halten — der Hauptthread idlet.
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("[brain-loops] beende.", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
