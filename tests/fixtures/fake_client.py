"""Eingespeister Agent-Client fuer Tests. Kein Netzzugriff."""


class FakeClient:
    """Gibt vorbereitete Antworten der Reihe nach zurueck.

    Ein Eintrag darf auch eine Exception sein - die wird dann geworfen statt
    zurueckgegeben. So laesst sich ein Transportfehler (Timeout, API-Fehler)
    an genau der Stelle nachstellen, an der er im Betrieb auftritt.
    """

    def __init__(self, responses: list[str | BaseException]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str, schema: dict[str, object]) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("FakeClient wurde oefter gerufen als vorbereitet")
        answer = self._responses.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    @property
    def calls(self) -> int:
        return len(self.prompts)
