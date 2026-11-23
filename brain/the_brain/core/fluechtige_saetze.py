"""Fluechtige Saetze mit Bildschirminhalt: nur im Speicher, einmal abholbar, nie auf Platte.

Ein fester Aufruf, dessen Antwort Fenstertitel traegt (desktop_fenster, desktop_bestaetigen),
gibt im Schritt-Ergebnis nur eine Kennung zurueck. Der Satz liegt hier hoechstens
``HALTBAR_S`` Sekunden und wird genau einmal fuer die Antwort an die App abgeholt.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Optional

MAX_SAETZE = 50
HALTBAR_S = 600
_SAETZE: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
_SPERRE = threading.Lock()


def merken(satz: str) -> str:
    """Satz ablegen; gibt die Kennung zurueck."""
    kennung = uuid.uuid4().hex[:16]
    with _SPERRE:
        grenze = time.monotonic() - HALTBAR_S
        for alt in [k for k, (t, _) in _SAETZE.items() if t < grenze]:
            del _SAETZE[alt]
        _SAETZE[kennung] = (time.monotonic(), satz)
        while len(_SAETZE) > MAX_SAETZE:
            _SAETZE.popitem(last=False)
    return kennung


def abholen(kennung: Any) -> Optional[str]:
    """Satz zur Kennung, danach ist er weg. ``None``: unbekannt oder verfallen."""
    with _SPERRE:
        eintrag = _SAETZE.pop(str(kennung or ""), None)
    if eintrag is None or eintrag[0] < time.monotonic() - HALTBAR_S:
        return None
    return eintrag[1]
