"""Der echte Agent-Client. Wird nirgends im Test benutzt.

Ruling 3: die installierte claude_agent_sdk (0.2.143) weicht von der
urspruenglich skizzierten Form ab. `query` ist eine asynchrone
Generatorfunktion, keine synchrone - deshalb treibt `complete` sie ueber
`asyncio.run` an, um das synchrone `AgentClient`-Protokoll zu erfuellen.
`options` ist ausserdem ein `ClaudeAgentOptions`-Dataclass, kein einfaches
dict, und Nachrichtentext steckt nicht in einem `.text`-Attribut der Message
selbst, sondern in einzelnen `TextBlock`-Elementen von `AssistantMessage.content`.
"""

import asyncio
import json

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

# Modellname gemaess den Beispielen in ClaudeAgentOptions.model der
# installierten SDK-Version - nicht "claude-opus-5" wie urspruenglich skizziert.
MODEL = "claude-opus-4-5"


class ClaudeClient:
    """Erfuellt das AgentClient-Protokoll aus djset.agents.base."""

    def __init__(self, model: str = MODEL) -> None:
        self._model = model

    def complete(self, prompt: str, schema: dict[str, object]) -> str:
        full = (
            f"{prompt}\n\n"
            f"JSON-Schema der erwarteten Antwort:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        return asyncio.run(self._complete_async(full))

    async def _complete_async(self, prompt: str) -> str:
        # Keine Werkzeuge noetig - wir wollen nur eine JSON-Antwort im Text.
        options = ClaudeAgentOptions(model=self._model, tools=[])
        chunks: list[str] = []
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
        return "".join(chunks)
