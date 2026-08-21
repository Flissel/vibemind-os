"""Eingespeister Agent-Client fuer Tests. Kein Netzzugriff."""


class FakeClient:
    """Gibt vorbereitete Antworten der Reihe nach zurueck."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str, schema: dict[str, object]) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("FakeClient wurde oefter gerufen als vorbereitet")
        return self._responses.pop(0)

    @property
    def calls(self) -> int:
        return len(self.prompts)
