"""Fester Lesesatz des Space "The Brain": "Woran denkt das Gehirn gerade?".

Gelesen wird die eigene Schnittstelle des Gehirns (``brain:GET:/api/cortex/thoughts``, ueber
den BrainSelfExecutor), ohne Modell. Der Satz nennt die letzten zwei, drei Gedanken, gekuerzt.
Laeuft die Denkschleife nicht oder ist noch kein Gedanke da, sagt der Satz genau das.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

ANZAHL = 3
MAX_ZEICHEN = 120


def _kurz(text: Any) -> str:
    text = " ".join(re.sub(r"[\x00-\x1f]+", " ", str(text or "")).split())
    return text if len(text) <= MAX_ZEICHEN else text[: MAX_ZEICHEN - 1].rstrip() + "…"


def antwort(ergebnis: Any) -> Optional[str]:
    """Deutscher Satz aus der Antwort von /api/cortex/thoughts; ``None``, wenn nicht lesbar."""
    try:
        if isinstance(ergebnis, Mapping) and isinstance(ergebnis.get("result"), Mapping) and "thoughts" not in ergebnis:
            ergebnis = ergebnis["result"]
        if not isinstance(ergebnis, Mapping) or not isinstance(ergebnis.get("thoughts"), list):
            return None
        gedanken = [g for g in ergebnis["thoughts"] if isinstance(g, Mapping) and _kurz(g.get("content"))]
        if not gedanken:
            stats = ergebnis.get("stats") if isinstance(ergebnis.get("stats"), Mapping) else {}
            if ergebnis.get("thinking") is True or stats.get("running") is True:
                return "Das Gehirn denkt im Hintergrund, hat aber gerade noch keinen Gedanken festgehalten."
            return "Das Gehirn denkt gerade nichts im Hintergrund; die Denkschleife ist aus."

        def zeit(g: Mapping) -> float:
            wert = g.get("timestamp")
            return float(wert) if isinstance(wert, (int, float)) and not isinstance(wert, bool) else 0.0

        neueste = sorted(gedanken, key=zeit, reverse=True)[:ANZAHL]
        teile = [f"„{_kurz(g.get('content'))}“" for g in neueste]
        kopf = "Zuletzt dachte das Gehirn: " if len(teile) == 1 else "Die letzten Gedanken des Gehirns: "
        return kopf + "; ".join(teile) + "."
    except Exception:  # noqa: BLE001
        return None
