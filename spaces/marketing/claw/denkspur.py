"""Denk- und Schrittspur eines Agenten-Auftrags (Spec sales-claw 2026-10-09-agent-denken-sichtbar).

Sammelt Claudes zusammengefasstes Denken und kurze Arbeitsschritte und meldet beides gedrosselt
ueber `senden(denken, schritte) -> bool`. Sichtbarkeit darf einen Auftrag nie kippen: jeder Fehler
beim Senden wird geschluckt, der Stand bleibt fuer den naechsten Takt offen."""
from __future__ import annotations

import time
from collections.abc import Callable

DENKEN_MAX = 20_000
SCHRITTE_MAX = 60
SCHRITT_MAX = 200
DROSSEL_S = 2.0
GEKUERZT = "… gekürzt\n"
KORREKTUR = "\n\n— Korrekturrunde —\n\n"


class Spur:
    def __init__(self, senden: Callable[[str, list[dict]], bool], uhr: Callable[[], float] = time.monotonic,
                 jetzt: Callable[[], str] = lambda: time.strftime("%H:%M:%S"), drossel_s: float = DROSSEL_S):
        self._senden, self._uhr, self._jetzt, self._drossel = senden, uhr, jetzt, drossel_s
        self._denken = ""
        self._gekuerzt = False
        self.schritte: list[dict] = []
        self._gesendet_am: float | None = None
        self._offen = False
        self.aus = False

    @property
    def denken_text(self) -> str:
        if not self._gekuerzt and len(self._denken) <= DENKEN_MAX:
            return self._denken
        return GEKUERZT + self._denken[-(DENKEN_MAX - len(GEKUERZT)):]

    def _anhaengen(self, text: str) -> None:
        self._denken += text
        if len(self._denken) > 2 * DENKEN_MAX:      # Speicher begrenzen; denken_text kuerzt ohnehin
            self._denken = self._denken[-DENKEN_MAX:]
            self._gekuerzt = True

    def denken(self, text: str) -> None:
        if not isinstance(text, str) or not text:
            return
        self._anhaengen(text)
        self._offen = True
        self.melden()

    def schritt(self, text: str) -> None:
        self.schritte.append({"zeit": self._jetzt(), "text": str(text)[:SCHRITT_MAX]})
        del self.schritte[:-SCHRITTE_MAX]
        self._offen = True
        self.melden()

    def korrektur(self) -> None:
        self._anhaengen(KORREKTUR)
        self.schritt("Korrekturrunde")

    def melden(self, immer: bool = False) -> None:
        if self.aus or not (self._offen or immer):
            return
        jetzt = self._uhr()
        if not immer and self._gesendet_am is not None and jetzt - self._gesendet_am < self._drossel:
            return
        self._gesendet_am, self._offen = jetzt, False
        try:
            weiter = self._senden(self.denken_text, list(self.schritte))
        except Exception:  # noqa: BLE001 - Sichtbarkeit darf einen Auftrag nie kippen
            self._offen = True
            return
        if weiter is False:
            self.aus = True

    def ende(self) -> None:
        self.melden(immer=True)
