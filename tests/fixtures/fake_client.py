"""Eingespeister Agent-Client fuer Tests. Kein Netzzugriff."""

import threading


class FakeClient:
    """Gibt vorbereitete Antworten der Reihe nach zurueck.

    Ein Eintrag darf auch eine Exception sein - die wird dann geworfen statt
    zurueckgegeben. So laesst sich ein Transportfehler (Timeout, API-Fehler)
    an genau der Stelle nachstellen, an der er im Betrieb auftritt.

    Threadsicher, weil djset.analysis.analyse_all diesen Client aus vier
    Threads treibt. Die Pruefung auf "noch Antworten da" und das Herausnehmen
    muessen dabei zusammen unteilbar sein - sonst kommen zwei Threads durch die
    Pruefung und einer von ihnen faellt beim pop auf eine leere Liste. Dass es
    heute wegen des GIL nicht passiert, ist keine Zusicherung, sondern ein
    Zufall der Implementierung.
    """

    def __init__(self, responses: list[str | BaseException]) -> None:
        self._responses = list(responses)
        self._lock = threading.Lock()
        self.prompts: list[str] = []

    def complete(self, prompt: str, schema: dict[str, object]) -> str:
        with self._lock:
            self.prompts.append(prompt)
            if not self._responses:
                raise AssertionError("FakeClient wurde oefter gerufen als vorbereitet")
            answer = self._responses.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    @property
    def calls(self) -> int:
        with self._lock:
            return len(self.prompts)
