"""Eingespeister Agent-Client fuer Tests. Kein Netzzugriff."""

import threading


class FakeClient:
    """Ein Modell-Ersatz ohne Netz.

    Zwei Betriebsarten. Mit einer Liste beantwortet er Aufrufe der Reihe nach.
    Mit einer Zuordnung sucht er zu jedem Prompt den ersten Schluessel, der
    darin vorkommt, und antwortet damit - das braucht es, sobald mehrere
    Threads gleichzeitig fragen, weil die Reihenfolge dann nicht mehr feststeht.

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

    def __init__(
        self, responses: list[str | BaseException] | dict[str, str]
    ) -> None:
        self._by_key = responses if isinstance(responses, dict) else None
        self._responses = list(responses) if self._by_key is None else []
        self._lock = threading.Lock()
        self.prompts: list[str] = []
        self.calls = 0

    def complete(self, prompt: str, schema: dict[str, object]) -> str:
        with self._lock:
            self.prompts.append(prompt)
            self.calls += 1
            if self._by_key is not None:
                for key, answer in self._by_key.items():
                    if key in prompt:
                        return answer
                raise AssertionError(
                    f"Kein Schluessel des FakeClient kommt im Prompt vor: "
                    f"{sorted(self._by_key)}"
                )
            if not self._responses:
                raise AssertionError("FakeClient wurde oefter gerufen als vorbereitet")
            answer = self._responses.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer
