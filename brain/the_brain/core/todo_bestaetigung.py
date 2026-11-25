"""Ein To-do der Bewertung wird nie direkt an den Agenten gegeben (Stufe 1, 25.11.2026).

"Erledige To-do 3" antwortet zuerst mit der Rückfrage aus ``ideen_bewertung.todo_frage``
(Titel, Art, Gewinn, was fehlt, ein Abo-Aufruf). Erst "Ja, erledigen" legt den Auftrag an, und nur:
- binnen ``GILT_S`` Sekunden nach genau dieser Rückfrage,
- wenn dazwischen kein anderer Satz kam (jeder andere Satz verwirft sie).

Der bestätigte Plan trägt einen Schlüssel, den der Plan-Ausführer genau einmal einlöst.
Ein To-do-Schritt ohne gültigen Schlüssel (z. B. vom Planer) startet nicht. Muster wie
``whitepaper_bestaetigung``.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from typing import Any, Dict, Optional

GILT_S = 120
SCHLUESSEL_GILT_S = 120

# Pruefung 25.11.2026: kopiert als  Ja, erledigen“.  ging die feste Form nicht und lief 16 s ins
# Modell. Bestaetigungen gelten auch mit umgebenden Anfuehrungszeichen und Satzzeichen.
_RAND_VORN = r"""^[\s"'„“”‚‘’«»]*"""
_RAND_HINTEN = r"""[\s.!?…"'„“”‚‘’«»]*$"""
_JA_ERLEDIGEN = re.compile(_RAND_VORN + r"ja\s*[,!.]?\s*(?:bitte\s+)?erledig(?:e|en)?(?:\s+(?:es|das))?"
                           + _RAND_HINTEN, re.IGNORECASE)
OHNE_FRAGE = ("Es ist gerade kein To-do zur Bestätigung offen. "
              "Sag „Erledige To-do N“, dann nenne ich zuerst, was passiert.")
OHNE_SCHLUESSEL = ("Ein To-do gebe ich nur nach Rückfrage an den Agenten. "
                   "Sag „Erledige To-do N“, dann nenne ich zuerst, was passiert.")

_sperre = threading.Lock()
_offen: Dict[str, Any] = {}               # {"argumente": dict, "bis": float}
_schluessel: Dict[str, float] = {}        # Schlüssel -> gültig bis


def _jetzt() -> float:
    return time.time()


def ist_bestaetigung(satz: Any) -> bool:
    return isinstance(satz, str) and bool(_JA_ERLEDIGEN.match(satz))


def fragen(argumente: Dict[str, Any]) -> None:
    """Die Rückfrage ist gestellt; ``argumente`` (bubble_id, nr) gelten für "Ja, erledigen"."""
    with _sperre:
        _offen.clear()
        _offen.update({"argumente": dict(argumente), "bis": _jetzt() + GILT_S})


def verwerfen() -> None:
    with _sperre:
        _offen.clear()


def satz_pruefen(satz: Any) -> Optional[Dict[str, Any]]:
    """Für jeden Satz vor allem anderen aufrufen (wie ``whitepaper_bestaetigung.satz_pruefen``)."""
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


def rueckfrage(argumente: Dict[str, Any], lesen: Any = None) -> str:
    """Die Rückfrage (nur lesen); merkt sie für "Ja, erledigen", wenn das To-do ans Modell gehen kann."""
    from .ideen_bewertung import todo_frage
    from .ideen_rueckweg import RueckwegFehler

    try:
        if lesen is None:
            from .ideen_dokument import DatenbankLesen
            lesen = DatenbankLesen()
        bubble = lesen.bubble(str(argumente.get("bubble_id") or ""))
        if bubble is None:
            verwerfen()
            return "Diese Bubble finde ich nicht."
        satz = todo_frage(bubble, argumente.get("nr"))
    except RueckwegFehler as e:
        verwerfen()
        return str(e)
    except Exception:  # noqa: BLE001
        verwerfen()
        return "Die Bubble lässt sich gerade nicht lesen. Versuch es gleich noch einmal."
    fragen({"bubble_id": argumente.get("bubble_id"), "nr": argumente.get("nr")})
    return satz
