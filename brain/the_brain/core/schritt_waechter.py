"""Schritt-Waechter: Urteilsfunktion.

Beurteilt einen ausgefuehrten Plan-Schritt: auffaellig (True), unauffaellig
(False, nur wenn ein Modell es bestaetigt) oder nicht beurteilt (None).
Ein nicht beurteilter Schritt gilt nie als unauffaellig.

Reine Funktion ohne Seiteneffekte, kein Netz. Das Modell kommt nur als
Callable `frage` herein.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .capability_targets import result_indicates_failure

MAX_PRUEFTEXT = 4000
_MAX_PARAMETER = 1000

_FRAGE_PRAEFIXE = ("please enter", "which idea", "what would you like me to")


@dataclass(frozen=True)
class Urteil:
    auffaellig: Optional[bool]
    grund: str
    quelle: str  # "regel", "modell" oder "keine"


_KEIN_URTEIL = Urteil(None, "", "keine")


def _als_text(wert: object, grenze: int) -> str:
    """Text eines Werts, gekuerzt auf `grenze` Zeichen."""
    if wert is None:
        return ""
    if isinstance(wert, str):
        text = wert
    else:
        try:
            text = json.dumps(wert, ensure_ascii=False, default=str)
        except Exception:
            text = str(wert)
    return text[:grenze]


def _regel(ok: bool, ergebnis: object, fehler: Optional[str], parameter: object,
           vorgaenger_auffaellig: bool) -> Optional[str]:
    """Grund des ersten Regeltreffers oder None."""
    if not ok:
        return "Schritt meldet Fehler: " + (_als_text(fehler, 200) or "ohne Angabe")
    if result_indicates_failure(ergebnis):
        return "Ergebnis zeigt einen Fehlschlag"
    text = _als_text(ergebnis, MAX_PRUEFTEXT)
    if "{{" in text:
        return "Ergebnis enthaelt einen unaufgeloesten Platzhalter"
    klein = text.strip().lower()
    if any(klein.startswith(p) for p in _FRAGE_PRAEFIXE):
        return "Ergebnis ist eine Rueckfrage statt einer Ausfuehrung"
    if "{{" in _als_text(parameter, MAX_PRUEFTEXT):
        return "Parameter enthaelt einen unaufgeloesten Platzhalter"
    if vorgaenger_auffaellig:
        return "Vorgaenger-Schritt war auffaellig"
    return None


def _modell_urteil(frage: Callable[[str], str], ergebnis: object,
                   parameter: object) -> Urteil:
    try:
        text = ("Parameter: " + _als_text(parameter, _MAX_PARAMETER)
                + "\nErgebnis: " + _als_text(ergebnis, MAX_PRUEFTEXT))
        antwort = json.loads(frage(text))
        folge = antwort["folge"]
        grund = antwort.get("grund", "")
        if folge not in ("nichts", "pruefen") or not isinstance(grund, str):
            return _KEIN_URTEIL
        return Urteil(folge == "pruefen", grund, "modell")
    except Exception:
        return _KEIN_URTEIL


def beurteile(*, ok: bool, ergebnis: object, fehler: Optional[str], parameter: object,
              vorgaenger_auffaellig: bool,
              frage: Optional[Callable[[str], str]] = None) -> Urteil:
    grund = _regel(ok, ergebnis, fehler, parameter, vorgaenger_auffaellig)
    if grund is not None:
        return Urteil(True, grund, "regel")
    if frage is None:
        return _KEIN_URTEIL
    return _modell_urteil(frage, ergebnis, parameter)
