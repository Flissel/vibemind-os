"""Der echte Agent-Client. Gegen das echte SDK wird er nirgends getestet -
tests/agents/test_client.py ersetzt `query` durch eine Faelschung und prueft
nur das Zeitlimit.

Ruling 3: die installierte claude_agent_sdk (0.2.143) weicht in der API-
Oberflaeche von der urspruenglich skizzierten Form ab. `query` ist eine
asynchrone Generatorfunktion, keine synchrone - deshalb treibt `complete`
sie ueber `asyncio.run` an, um das synchrone `AgentClient`-Protokoll zu
erfuellen. `options` ist ausserdem ein `ClaudeAgentOptions`-Dataclass, kein
einfaches dict, und Nachrichtentext steckt nicht in einem `.text`-Attribut
der Message selbst, sondern in einzelnen `TextBlock`-Elementen von
`AssistantMessage.content`. Diese drei Punkte sind SDK-Fakten und wurden
entsprechend angepasst.

Der Modellname ist dagegen keine SDK-Frage, sondern eine Projektentscheidung
des Briefs - er bleibt exakt wie vorgegeben, auch wenn ein Beispiel in der
SDK-Docstring einen aelteren Namen zeigt.

Zeitlimit: ein Modellaufruf, der nie antwortet, liesse einen unbeaufsichtigten
Lauf sonst haengen, bis das Zeitbudget des ganzen Laufs (bis zu einer Stunde)
greift - und das Budget prueft nur zwischen zwei Aufrufen. Nach
MODELL_ZEITLIMIT_S wird abgebrochen, mit einem TimeoutError. Das ist kein
AgentError, also behandelt agents/base.py ihn als Transportfehler und
wiederholt mit Backoff.

Die Frist ist ein anyio-Bereich (`anyio.fail_after`), kein `asyncio.wait_for`:
SubprocessCLITransport.close im SDK raeumt in einem abgeschirmten anyio-Bereich
auf, und das Abschirmen wirkt laut seiner Doku nur gegen einen Abbruch aus
einem anyio-Bereich. Ein roher asyncio-Abbruch ueberspraenge terminate/kill,
und der CLI-Kindprozess liefe bis zum Ende von dj-set weiter. `aclosing`
schliesst den Generator noch innerhalb der Frist, damit sein Aufraeumen dort
laeuft und nicht erst beim Herunterfahren der Ereignisschleife. anyio kommt
mit claude-agent-sdk (dessen eigene Abhaengigkeit) und ist keine neue.
"""

import asyncio
import json
from contextlib import aclosing

import anyio

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

MODEL = "claude-opus-5"

# Laenger wartet kein einzelner Modellaufruf. Die laengsten Antworten dieses
# Projekts (Profil, Prompts) brauchen Sekunden bis wenige Minuten.
MODELL_ZEITLIMIT_S = 300.0


class ClaudeClient:
    """Erfuellt das AgentClient-Protokoll aus djset.agents.base."""

    def __init__(
        self, model: str = MODEL, zeitlimit_s: float = MODELL_ZEITLIMIT_S
    ) -> None:
        self._model = model
        self._zeitlimit_s = zeitlimit_s

    def complete(self, prompt: str, schema: dict[str, object]) -> str:
        full = (
            f"{prompt}\n\n"
            f"JSON-Schema der erwarteten Antwort:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        return asyncio.run(self._complete_with_timeout(full))

    async def _complete_with_timeout(self, prompt: str) -> str:
        try:
            with anyio.fail_after(self._zeitlimit_s):
                return await self._complete_async(prompt)
        except TimeoutError as error:
            raise TimeoutError(
                f"Modellaufruf nach dem Zeitlimit von {self._zeitlimit_s:.0f} s "
                f"abgebrochen"
            ) from error

    async def _complete_async(self, prompt: str) -> str:
        # Keine Werkzeuge noetig - wir wollen nur eine JSON-Antwort im Text.
        options = ClaudeAgentOptions(model=self._model, tools=[])
        chunks: list[str] = []
        async with aclosing(query(prompt=prompt, options=options)) as messages:
            async for message in messages:
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            chunks.append(block.text)
        return "".join(chunks)
