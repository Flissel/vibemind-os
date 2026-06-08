"""Phase B — Dynamischer Capability-Team-Runner (das 'insane'-Geschütz).

Für vage/explorative Intents (difficulty=insane) baut dieser Runner ein
AutoGen-SelectorGroupChat-Team DYNAMISCH aus den intent-relevantesten
Capabilities: pro Capability ein AssistantAgent, dessen Tool die Capability über
den bestehenden Brain-Executor (build_executor(target).call_with_arg) ausführt.
Der Selector wählt pro Turn den zuständigen Cap-Agenten — echte Multi-Agent-
Exploration statt single-pass-Planung.

Auswahl SEMANTISCH (Qwen-Cosine auf Intent vs. Capability-Beschreibung, gleicher
Embedder wie difficulty_router) — kein Verb-Zählen. Fallback: erste N Caps.

Muster wiederverwendet aus runner/som_selector.py (AssistantAgent + tools +
SelectorGroupChat) + _lib/som_team.py (make_model_client/termination/run_team).
Embedder + capability_list injizierbar (Test ohne Modell/YAML).

Läuft synchron im (detached) Worker-Prozess (openfang_som_team_wrapper) — wie
som-planner. Aktiv nur über den Wrapper bei INSANE_AUTOGEN=1.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger("brain.som_team_runner")

_PLANNER = Path(__file__).resolve().parents[1]


def _load(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_tools = _load("team_tools", _PLANNER / "_lib" / "tools.py")
_team = _load("team_helpers", _PLANNER / "_lib" / "som_team.py")


def _default_capability_list() -> list[dict]:
    try:
        return _tools.capability_list()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[team] capability_list fehlgeschlagen ({e})")
        return []


class CapabilityTeamBuilder:
    """Wählt intent-relevante Capabilities + baut daraus ein SelectorGroupChat."""

    def __init__(
        self,
        embedder: Any = None,
        capability_list_fn: Optional[Callable[[], list[dict]]] = None,
        disable_semantic: bool = False,
    ) -> None:
        self._capability_list_fn = capability_list_fn or _default_capability_list
        self._disable_semantic = disable_semantic
        self._embedder = None if disable_semantic else embedder
        self._embedder_tried = disable_semantic or (embedder is not None)

    # ── Embedder lazy (reuse difficulty_router/qdrant_kg) ─────────────────────
    def _get_embedder(self) -> Any:
        if self._embedder is not None:
            return self._embedder
        if self._embedder_tried:
            return None
        self._embedder_tried = True
        try:
            from core.qdrant_kg import Embedder
            self._embedder = Embedder.get()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[team] Embedder nicht verfügbar ({e}), Fallback erste-N")
            self._embedder = None
        return self._embedder

    @staticmethod
    def _norm(v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    # ── Capability-Auswahl ────────────────────────────────────────────────────
    def select_capabilities(self, intent: str, top_n: int = 4) -> list[dict]:
        """Top-N intent-relevanteste Capabilities (Qwen-Cosine auf description).
        Fallback ohne Embedder: erste N. Leere Liste → []."""
        caps = self._capability_list_fn() or []
        if not caps:
            return []
        embedder = self._get_embedder()
        if embedder is None:
            return caps[:top_n]
        try:
            texts = [f"{c.get('name','')} {c.get('description','')}" for c in caps]
            mat = np.asarray(embedder.encode_batch(texts), dtype=np.float32)
            qv = self._norm(np.asarray(embedder.encode(intent), dtype=np.float32))
            sims = mat @ qv  # Qwen normalisiert → Cosine = Dot
            order = np.argsort(-sims)[:top_n]
            return [caps[i] for i in order]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[team] Cap-Auswahl-Cosine fehlgeschlagen ({e}), erste-N")
            return caps[:top_n]

    # ── Team-Bau ──────────────────────────────────────────────────────────────
    def build_team(self, intent: str, top_n: int = 4, max_messages: int = 14):
        """Baut ein SelectorGroupChat: je gewählter Capability ein AssistantAgent
        mit einem Tool das die Capability via Brain-Executor ausführt."""
        from autogen_agentchat.agents import AssistantAgent
        from autogen_agentchat.teams import SelectorGroupChat

        caps = self.select_capabilities(intent, top_n=top_n)
        if not caps:
            return None
        mc = _team.make_model_client()
        agents = []
        for c in caps:
            agents.append(self._make_cap_agent(AssistantAgent, mc, c))
        # Ein Synthesizer fasst am Ende zusammen + terminiert
        agents.append(AssistantAgent(
            name="Synthesizer",
            model_client=mc,
            description="Fasst die Beiträge der Capability-Agenten zu einem Ergebnis zusammen.",
            system_message=(
                "Du bist der Synthesizer. Wenn die Capability-Agenten genug beigetragen "
                "haben, fasse das Ergebnis in wenigen klaren Sätzen zusammen (was wurde "
                "erreicht / vorgeschlagen) und beende mit dem Wort TASK_COMPLETE."
            ),
        ))
        return SelectorGroupChat(
            participants=agents,
            model_client=mc,
            termination_condition=_team.make_termination("TASK_COMPLETE", max_messages),
            allow_repeated_speaker=False,
            selector_prompt=(
                "Wähle den nächsten Sprecher für die Aufgabe. Jede Rolle deckt eine "
                "Fähigkeit ab; der Synthesizer schließt ab. Rollen:\n{roles}\n\n{history}\n\n"
                "Wähle aus {participants} die passendste Rolle für den nächsten Schritt. "
                "Nur den Rollennamen zurückgeben."
            ),
        )

    def _make_cap_agent(self, AssistantAgent, mc, cap: dict):
        name = cap.get("name", "cap")
        target = cap.get("execution_target") or ""
        desc = cap.get("description", "")[:160]

        def run_capability(arg: str) -> dict:
            """Führt diese Capability über den Brain-Executor aus."""
            try:
                from core.capability_targets import build_executor
                ex = build_executor(target)
                res = ex.call_with_arg(arg, extra_params={"_intent": arg})
                return {"ok": True, "capability": name, "result": res}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "capability": name, "error": str(e)[:200]}

        return AssistantAgent(
            name=name,
            model_client=mc,
            tools=[run_capability],
            description=f"Capability '{name}': {desc}",
            system_message=(
                f"Du repräsentierst die Fähigkeit '{name}' ({desc}). Wenn dein Beitrag "
                f"für die Aufgabe relevant ist, rufe run_capability(arg) mit einem "
                f"konkreten Argument auf und berichte das Ergebnis knapp. Sonst gib kurz "
                f"an, dass deine Fähigkeit hier nicht passt, und übergib."
            ),
        )


# ── Öffentlicher Entrypoint (vom Wrapper gerufen) ─────────────────────────────
async def run_team_async(intent: str, top_n: int = 4) -> dict:
    builder = CapabilityTeamBuilder()
    team = builder.build_team(intent, top_n=top_n)
    if team is None:
        return {"ok": False, "error": "keine Capabilities für das Team"}
    out = await _team.run_team(team, intent)
    return {"ok": True, "intent": intent, "team": out,
            "final_text": out.get("last_text") if isinstance(out, dict) else ""}


def run(intent: str, top_n: int = 4) -> dict:
    """Synchroner Entrypoint (Wrapper-Worker)."""
    n = int(os.environ.get("INSANE_TEAM_SIZE", str(top_n)))
    return asyncio.run(run_team_async(intent, top_n=n))
