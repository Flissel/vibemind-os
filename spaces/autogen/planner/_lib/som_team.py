"""SoM Planner — AutoGen 0.7.5 Team-Fundament (wiederverwendbar).

Stellt model_client + Team-Bau-Helper bereit, damit Phase 2 (Matrix-Aggregator
via SelectorGroupChat) UND spätere Phasen (3: Feedback-Loop, 4: dynamische
Ausführung) dieselbe AutoGen-Basis nutzen. Kein Matrix-spezifischer Code hier.

Modell-Wahl (env):
  SOM_TEAM_MODEL   default "openai::gpt-5.5"
  (günstiger Override: SOM_TEAM_MODEL=openai::gpt-5.5-mini bzw. openai::gpt-4o-mini)

WICHTIG: OpenAI-Default weil tool-nutzende Agents sauberes Function-Calling
brauchen. Groq-llama-3.3 produziert kaputte Tool-Call-Syntax (tool_use_failed,
verifiziert 2026-06-02) → für AutoGen-Tools NICHT geeignet. Groq bleibt fürs
single-shot-Planning (som_core/Wrapper) ok, aber AutoGen-Teams mit Tools → OpenAI.
OPENAI_API_KEY kommt aus der Root-.env (Vibemind_V1/.env).

Prefixe: openai::<model> | groq::<model> | sonst OpenRouter falls Key, sonst OpenAI.
"""

from __future__ import annotations

import os
from pathlib import Path

# .env aus Vibemind_V1-ROOT laden (Root hat Priorität, dann vibemind-os).
_REPO_ROOT = Path(__file__).resolve().parents[5]   # ...->planner->autogen->spaces->vibemind-os->Vibemind_V1


def _bootstrap_env() -> None:
    """Lädt API-Keys aus der Root-.env. Prüft gezielt auf OPENAI_API_KEY (den
    AutoGen-Teams brauchen) — nicht auf Groq/OpenRouter, sonst würde die .env bei
    vorhandenem Groq-Key übersprungen und OPENAI_API_KEY fehlte."""
    if os.environ.get("OPENAI_API_KEY"):
        return
    # ROOT zuerst (gewünschte Quelle der Wahrheit), dann vibemind-os als Fallback
    for env_path in (_REPO_ROOT / ".env", _REPO_ROOT / "vibemind-os" / ".env"):
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip(); v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v
            if os.environ.get("OPENAI_API_KEY"):
                break  # Root hatte den Key → fertig
        except Exception:  # noqa: BLE001
            continue


_bootstrap_env()


def make_model_client(model: str | None = None):
    """Baut einen AutoGen ChatCompletionClient. Groq/OpenRouter/OpenAI je nach
    Modell-String + verfügbaren Keys. Liefert model_info mit (Pflicht für
    non-OpenAI-Modelle, sonst kennt AutoGen die Capabilities nicht)."""
    from autogen_ext.models.openai import OpenAIChatCompletionClient
    from autogen_core.models import ModelInfo

    model = model or os.environ.get("SOM_TEAM_MODEL", "openai::gpt-5.5")

    # OpenAI direkt — sauberes Tool-Calling. Neuere Modelle (gpt-5.x) kennt das
    # installierte autogen_ext evtl. noch nicht → explizites model_info liefern.
    if model.startswith("openai::"):
        real = model.split("::", 1)[1]
        key = os.environ.get("OPENAI_API_KEY", "")
        try:
            return OpenAIChatCompletionClient(model=real, api_key=key)
        except Exception:  # noqa: BLE001 — Modell nicht in der Lib-Registry
            oai_info = ModelInfo(
                vision=True,
                function_calling=True,
                json_output=True,
                family="gpt-5",
                structured_output=True,
                multiple_system_messages=True,
            )
            return OpenAIChatCompletionClient(model=real, api_key=key, model_info=oai_info)

    # model_info für non-OpenAI (Groq/OpenRouter brauchen es explizit).
    model_info = ModelInfo(
        vision=False,
        function_calling=True,
        json_output=True,
        family="unknown",
        structured_output=False,
        multiple_system_messages=True,
    )

    if model.startswith("groq::"):
        # WARNUNG: Groq-llama-3.3 scheitert an AutoGen-Tool-Calls. Nur für
        # tool-FREIE Agents nutzen.
        real = model.split("::", 1)[1]
        return OpenAIChatCompletionClient(
            model=real,
            api_key=os.environ.get("GROQ_API_KEY", ""),
            base_url="https://api.groq.com/openai/v1",
            model_info=model_info,
        )

    openrouter = os.environ.get("OPENROUTER_API_KEY")
    if openrouter:
        return OpenAIChatCompletionClient(
            model=model,
            api_key=openrouter,
            base_url="https://openrouter.ai/api/v1",
            model_info=model_info,
        )

    real = model.split("/", 1)[1] if "/" in model else model
    return OpenAIChatCompletionClient(model=real, api_key=os.environ.get("OPENAI_API_KEY", ""))


def make_termination(mention: str = "TASK_COMPLETE", max_messages: int = 12):
    """Standard-Termination: Mention-Wort ODER Nachrichten-Limit (Endlos-Bremse)."""
    from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination
    return TextMentionTermination(mention) | MaxMessageTermination(max_messages)


async def run_team(team, task: str) -> dict:
    """Führt ein AutoGen-Team aus, gibt {messages, last_text} zurück.
    Generisch — funktioniert für RoundRobin, Selector, etc."""
    result = await team.run(task=task)
    messages = getattr(result, "messages", []) or []
    last_text = ""
    for m in reversed(messages):
        content = getattr(m, "content", None)
        if isinstance(content, str) and content.strip():
            last_text = content
            break
    return {
        "n_messages": len(messages),
        "last_text": last_text,
        "stop_reason": getattr(result, "stop_reason", None),
    }


if __name__ == "__main__":
    import asyncio
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    async def _selftest():
        from autogen_agentchat.agents import AssistantAgent
        from autogen_agentchat.teams import RoundRobinGroupChat
        mc = make_model_client()
        a = AssistantAgent("Echo", mc, system_message="Antworte knapp. Sage am Ende TASK_COMPLETE.")
        team = RoundRobinGroupChat([a], termination_condition=make_termination(max_messages=2))
        out = await run_team(team, "Sag Hallo auf Deutsch.")
        print("som_team selftest:", out["n_messages"], "msgs | stop:", out["stop_reason"])
        print("last:", out["last_text"][:100])

    asyncio.run(_selftest())
