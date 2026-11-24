"""Beispielsätze je Bereich für das „?“ der App (Routing 24.11.2026).

``GET /api/beispielsaetze?space=<id>`` liefert ``{"space": <id>, "saetze": [...]}``:
höchstens fünf Sätze, nur aus ``core/verstehen.SPACES[...].beispiele``, also Sätze, die
nachweislich einen festen Weg nehmen (kein Planer, kein Modellauftrag). Belegt wird das von
tests/test_routing_bereiche.py: jeder Satz trifft dort ohne Modell seinen festen Aufruf.

Die Kennung darf die der App (roarboot, projects, clawport, orders ...) oder der Name der
Registry (rowboat, coding, schedule ...) sein; unbekannt heißt leere Liste. Wirft nie.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

MAX_SAETZE = 5


def app_kennung(space: Any) -> Optional[str]:
    try:
        from .verstehen import SPACES, _ALIASE

        if not isinstance(space, str):
            return None
        kennung = space.strip().lower()
        kennung = _ALIASE.get(kennung, kennung)
        return kennung if kennung in SPACES else None
    except Exception:  # noqa: BLE001
        return None


def saetze_fuer(space: Any) -> List[str]:
    try:
        from .verstehen import SPACES

        kennung = app_kennung(space)
        if kennung is None:
            return []
        return [str(s) for s in SPACES[kennung].beispiele][:MAX_SAETZE]
    except Exception:  # noqa: BLE001
        return []


def antwort(space: Any) -> Dict[str, Any]:
    angefragt = space.strip().lower()[:40] if isinstance(space, str) else ""
    return {"space": angefragt, "saetze": saetze_fuer(space)}
