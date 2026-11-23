"""Ein White Paper startet nie direkt (Dritter Lauf: 105 Karten, Auftrag ohne Vorwarnung).

Der Satz "Erstelle ein White Paper ..." antwortet zuerst mit der Rückfrage aus
``ideen_dokument.white_paper_frage`` (Läufe, lesbare Karten, Abschnitte, leere Karten).
Erst "Ja, erstellen" legt den ersten Auftrag an, und nur:
- binnen ``GILT_S`` Sekunden nach genau dieser Rückfrage,
- wenn dazwischen kein anderer Satz kam (jeder andere Satz verwirft sie).

Der bestätigte Plan trägt einen Schlüssel, den der Plan-Ausführer genau einmal einlöst.
Ein White-Paper-Schritt ohne gültigen Schlüssel (z. B. vom Planer) startet nicht.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from typing import Any, Dict, Optional

GILT_S = 120
SCHLUESSEL_GILT_S = 120

_JA_ERSTELLEN = re.compile(r"^\s*ja\s*[,!.]?\s*(?:bitte\s+)?erstell(?:e|en)?(?:\s+es)?\s*[.!]?\s*$", re.IGNORECASE)
OHNE_FRAGE = ("Es ist gerade kein White Paper zur Bestätigung offen. "
              "Sag „Erstelle ein White Paper aus dieser Bubble“, dann nenne ich zuerst den Umfang.")
OHNE_SCHLUESSEL = ("Ein White Paper starte ich nur nach Rückfrage. "
                   "Sag „Erstelle ein White Paper aus dieser Bubble“, dann nenne ich zuerst den Umfang.")

_sperre = threading.Lock()
_offen: Dict[str, Any] = {}               # {"argumente": dict, "bis": float}
_schluessel: Dict[str, float] = {}        # Schlüssel -> gültig bis


def _jetzt() -> float:
    return time.time()


def ist_bestaetigung(satz: Any) -> bool:
    return isinstance(satz, str) and bool(_JA_ERSTELLEN.match(satz))


def fragen(argumente: Dict[str, Any]) -> None:
    """Die Rückfrage ist gestellt; ``argumente`` (bubble_id, max_laeufe) gelten für "Ja, erstellen"."""
    with _sperre:
        _offen.clear()
        _offen.update({"argumente": dict(argumente), "bis": _jetzt() + GILT_S})


def verwerfen() -> None:
    with _sperre:
        _offen.clear()


def satz_pruefen(satz: Any) -> Optional[Dict[str, Any]]:
    """Für jeden Satz vor allem anderen aufrufen.

    ``None``: keine Bestätigung; eine offene Rückfrage verfällt (gebunden an genau sie).
    ``{"argumente": {...}}``: bestätigt, mit Schlüssel für den Plan-Ausführer.
    ``{"satz": ...}``: "Ja, erstellen" ohne gültige Rückfrage, nichts angelegt.
    """
    jetzt = _jetzt()
    with _sperre:
        offen = dict(_offen)
        _offen.clear()
        if not ist_bestaetigung(satz):
            return None
        if not offen or offen.get("bis", 0) < jetzt:
            return {"satz": OHNE_FRAGE}
        schluessel = secrets.token_hex(8)
        for alt in [s for s, bis in _schluessel.items() if bis < jetzt]:
            _schluessel.pop(alt, None)
        _schluessel[schluessel] = jetzt + SCHLUESSEL_GILT_S
    return {"argumente": {**offen["argumente"], "bestaetigung": schluessel}}


def einloesen(schluessel: Any) -> bool:
    """Genau einmal wahr für einen Schlüssel aus ``satz_pruefen``."""
    if not isinstance(schluessel, str):
        return False
    with _sperre:
        bis = _schluessel.pop(schluessel, None)
    return bis is not None and bis >= _jetzt()
