"""``ziel`` für jede Antwort, deren Ergebnis in einem Bereich liegt (Routing 24.11.2026).

Bisher bekam nur ein fester Ideen-Schreibaufruf ein ``ziel`` (``{space: ideas, bubble_id,
node_id}``, core/ideen_abkuerzung.ziel_fuer_schritt). Die App springt aber auch ohne
Bubble in einen Bereich. Hier entsteht für jede gelungene Antwort ``{"space": <Name>}``:

- aus dem Feld ``space`` der Antwort, sonst
- aus den ausgeführten Schritten, wenn alle gelungenen Schritte im selben Bereich liegen.

Der Name ist der Bereichsname der Registry (config/space_agent_registry.yml). Die App bildet
ihn selbst auf ihre Bereiche ab (rowboat → roarboot, coding → projects, bubbles/research/
recherche → ideas). Kein ``ziel`` bei Rückfrage, Hinweis, angenommenem Auftrag, Abbruch,
Fehler oder einem Namen, den die Registry nicht kennt. Wirft nie.

Kein ``ziel`` auch dann, wenn der Satz schon aus diesem Bereich kam (``context.space``):
dort gibt es nichts zu springen, und ein Sprung ohne bubble_id würde eine offene Bubble
verlassen (Lesen in der Bubble, „Zurück“ aus der Übersicht).

Nur die HTTP-Antwort auf einen Satz bekommt dieses ``ziel``; ``plan_completed`` bleibt bei
den Ideen-Schreibaufrufen, weil das Ereignis auch Pläne ohne Menschensatz meldet.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional


def _registry_bereiche() -> frozenset:
    try:
        from .space_contract import load_space_contract

        return frozenset(load_space_contract().spaces)
    except Exception:  # noqa: BLE001
        return frozenset()


def _space_des_schritts(schritt: Mapping[str, Any]) -> Optional[str]:
    vorhanden = schritt.get("space")
    if isinstance(vorhanden, str) and vorhanden:
        return vorhanden
    try:
        from .step_space import space_for_step

        return space_for_step(str(schritt.get("capability") or ""), str(schritt.get("target") or ""))
    except Exception:  # noqa: BLE001
        return None


def bereich_der_schritte(schritte: Iterable[Any]) -> Optional[str]:
    """Der eine Bereich aller gelungenen Schritte, sonst ``None`` (keiner oder mehrere)."""
    bereiche = set()
    for schritt in schritte:
        if not isinstance(schritt, Mapping) or not schritt.get("ok") or schritt.get("pending"):
            continue
        space = _space_des_schritts(schritt)
        if not space:
            return None
        bereiche.add(space)
    return bereiche.pop() if len(bereiche) == 1 else None


_APP_BEREICH = {"rowboat": "roarboot", "coding": "projects", "bubbles": "ideas", "research": "ideas",
                "recherche": "ideas", "ideen": "ideas", "project": "projects"}


def app_bereich(space: Any) -> Optional[str]:
    """Der Bereich der App zu einem Namen der Registry oder der App (wie die Tabelle der Hülle)."""
    if not isinstance(space, str) or not space.strip():
        return None
    kennung = space.strip().lower()
    return _APP_BEREICH.get(kennung, kennung)


def ziel_ergaenzen(daten: Dict[str, Any], bereiche: Optional[frozenset] = None,
                   kontext_space: Any = None) -> Dict[str, Any]:
    """Setzt ``daten["ziel"] = {"space": …}``, wenn die Antwort gelungen ist, in einem
    Bereich der Registry liegt und der Satz nicht schon aus diesem Bereich kam. Ein
    vorhandenes ``ziel`` bleibt unverändert."""
    try:
        if not isinstance(daten, dict) or "ziel" in daten or daten.get("ok") is not True:
            return daten
        if any(daten.get(feld) for feld in ("rueckfrage", "hinweis", "pending", "fehler")):
            return daten
        if daten.get("status") == "abgebrochen" or daten.get("verstanden") is False:
            return daten
        bekannt = bereiche if bereiche is not None else _registry_bereiche()
        space = daten.get("space")
        if not (isinstance(space, str) and space in bekannt):
            ausgefuehrt = daten.get("executed")
            space = bereich_der_schritte(ausgefuehrt.values()) if isinstance(ausgefuehrt, Mapping) else None
        if isinstance(space, str) and space in bekannt:
            if kontext_space is not None and app_bereich(space) == app_bereich(kontext_space):
                return daten
            daten["ziel"] = {"space": space}
    except Exception:  # noqa: BLE001
        pass
    return daten
