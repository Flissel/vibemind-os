"""Inkrementeller Leser fuer die Antwort des Gestaltungs-Agenten (sales-claw Spec 2026-10-02-newsletter-agent-live).
Waehrend Claude streamt, liefert StromLeser jede VOLLSTAENDIGE Aenderung aus "aenderungen" genau einmal. Rein:
kein Dienst, kein Dateizugriff. Zustand (Tiefe, String, Escape) bleibt zwischen futter()-Aufrufen erhalten."""
from __future__ import annotations

import json

_SCHLUESSEL = "aenderungen"


class StromLeser:
    def __init__(self) -> None:
        self.fehler: list[str] = []
        self._stuecke: list[str] = []
        self._tiefe = 0            # {/[-Tiefe ausserhalb von Strings; 1 = oberstes Objekt, 2 = Array-Ebene
        self._im_string = False
        self._escape = False
        self._schluessel: list[str] = []   # Zeichen des gerade gelesenen Strings auf Tiefe 1
        self._letzter = ""                 # zuletzt beendeter String auf Tiefe 1
        self._erwarte_array = False        # "aenderungen": gesehen, naechstes Zeichen soll [ sein
        self._im_array = False
        self._element: list[str] = []
        self._fertig = False
        self._zaehler = 0

    @property
    def text(self) -> str:
        return "".join(self._stuecke)

    def futter(self, stueck: str) -> list[dict]:
        self._stuecke.append(stueck)
        neu: list[dict] = []
        for c in stueck:
            if self._fertig:
                continue
            if self._im_array and self._tiefe >= 2:
                self._element_zeichen(c, neu)
                continue
            self._kopf_zeichen(c)
        return neu

    def _kopf_zeichen(self, c: str) -> None:
        """Zeichen ausserhalb der Array-Elemente: Vortext, oberstes Objekt, Schluessel-Erkennung."""
        if self._im_string:
            self._string_schritt(c)
            if not self._im_string:
                self._letzter = "".join(self._schluessel) if self._tiefe == 1 else ""
                self._schluessel = []
            elif self._tiefe == 1 and len(self._schluessel) < 40:
                self._schluessel.append(c)
            return
        if self._erwarte_array:
            if c.isspace():
                return
            self._erwarte_array = False
            if c == "[" and self._tiefe == 1:
                self._tiefe, self._im_array = 2, True
                return
        if c == '"' and self._tiefe > 0:
            self._im_string, self._schluessel = True, []
        elif c == "{" or c == "[":
            self._tiefe += 1
        elif (c == "}" or c == "]") and self._tiefe > 0:
            self._tiefe -= 1
            if self._tiefe == 0:   # Objekt ohne "aenderungen" (z.B. Vortext mit {...}) zu Ende: weitersuchen
                self._letzter = ""
        elif c == ":" and self._tiefe == 1 and self._letzter == _SCHLUESSEL:
            self._erwarte_array = True

    def _element_zeichen(self, c: str, neu: list[dict]) -> None:
        """Zeichen innerhalb des Arrays: Elemente auf Tiefe 2 sammeln, beim schliessenden } parsen."""
        if self._im_string:
            self._string_schritt(c)
            if self._element:
                self._element.append(c)
            return
        if c == '"':
            self._im_string = True
            if self._element:
                self._element.append(c)
        elif c == "{" or c == "[":
            self._tiefe += 1
            if self._tiefe == 3 and c == "{":
                self._element = [c]
            elif self._element:
                self._element.append(c)
        elif c == "}" or c == "]":
            self._tiefe -= 1
            if self._element:
                self._element.append(c)
            if self._tiefe == 2 and self._element:
                roh, self._element = "".join(self._element), []
                self._ausgeben(roh, neu)
            elif self._tiefe == 1:   # Array geschlossen: der Rest ist fuer uns nicht mehr von Belang
                self._element, self._im_array, self._fertig = [], False, True
        elif self._element:
            self._element.append(c)

    def _string_schritt(self, c: str) -> None:
        if self._escape:
            self._escape = False
        elif c == "\\":
            self._escape = True
        elif c == '"':
            self._im_string = False

    def _ausgeben(self, roh: str, neu: list[dict]) -> None:
        self._zaehler += 1
        try:
            d = json.loads(roh)
        except ValueError as e:
            self.fehler.append(f"Änderung {self._zaehler} nicht lesbar: {e}")
            return
        if isinstance(d, dict):
            neu.append(d)
        else:
            self.fehler.append("Änderung ist kein Objekt")
