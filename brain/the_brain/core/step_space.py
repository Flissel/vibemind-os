"""Zu welchem Space gehoert ein Plan-Schritt?

Der Plan-Ausfuehrer meldet je Schritt Faehigkeit und Ziel. Die VibeMind-App soll bei
einer Aufgabe in den zugehoerigen Space wechseln; dafuer braucht die Meldung den Space
selbst, sonst muesste jeder Abnehmer ihn aus Faehigkeitsnamen erraten.

Quelle ist allein ``config/space_agent_registry.yml`` (Ereignisse und Vorsilben je
Space). Diese Datei hier kennt keine Space-Namen ausser den wenigen Ziel-Hinweisen
unten, die sich aus der Registry nicht ableiten lassen.
"""
from __future__ import annotations

import re
from typing import Optional

from .space_contract import SpaceContract, load_space_contract, normalize_space_id

# Ziele, deren Space weder im Namen der Faehigkeit noch im Ereignis steht.
# Geordnet: der erste passende Anfang gewinnt.
_ZIEL_HINWEISE = (
    ("coding-engine:", "coding"),
    ("openfang:brain-coder", "coding"),
    ("openfang:openclaw", "desktop"),
    ("openfang:skill-coordinator", "desktop"),
    ("openfang:rowboat", "rowboat"),
    ("mirofish:", "mirofish"),
    ("research:", "research"),
    # Bauteil-Anforderungen haengen an einer Idee, haben aber eine eigene Vorsilbe.
    ("supabase:component.", "ideas"),
)

_DIREKT = re.compile(r"^direct:spaces\.([a-z0-9_]+)\.")
_MCP_SPACE = re.compile(r"^mcp:[^:]+:spaces-([a-z0-9_-]+):")

_vertrag: Optional[SpaceContract] = None


def _lade_vertrag() -> Optional[SpaceContract]:
    global _vertrag
    if _vertrag is None:
        try:
            _vertrag = load_space_contract()
        except Exception:
            return None
    return _vertrag


def _space_aus_ereignis(ereignis: str, vertrag: SpaceContract) -> Optional[str]:
    if not ereignis:
        return None
    if ereignis in vertrag.event_space_map:
        return vertrag.event_space_map[ereignis]
    treffer: Optional[str] = None
    laenge = 0
    for space_id, meta in vertrag.spaces.items():
        for vorsilbe in meta.get("prefixes") or ():
            if isinstance(vorsilbe, str) and ereignis.startswith(vorsilbe) and len(vorsilbe) > laenge:
                treffer, laenge = space_id, len(vorsilbe)
    return treffer


def space_for_step(
    capability: str,
    execution_target: str = "",
    contract: Optional[SpaceContract] = None,
) -> Optional[str]:
    """Space-Kennung der Registry fuer einen Schritt, oder ``None`` (allgemeine Aufgabe).

    Wirft nie: Eine fehlende Registry oder ein unbekannter Name darf keinen Plan stoppen.
    """
    try:
        vertrag = contract or _lade_vertrag()
        if vertrag is None:
            return None
        name = (capability or "").strip()
        ziel = (execution_target or "").strip()

        kandidaten = []
        try:
            from .capability_targets import _CANONICAL_SPACE_EVENT_IDS

            kandidaten.append(_CANONICAL_SPACE_EVENT_IDS.get(name, ""))
        except Exception:
            pass
        kandidaten.append(name)
        if "." not in name and "_" in name:
            kandidaten.append(name.replace("_", ".", 1))  # idea_format_table -> idea.format_table
        if ziel.startswith("supabase:"):
            kandidaten.append(ziel[len("supabase:"):])
        for ereignis in kandidaten:
            space = _space_aus_ereignis(ereignis, vertrag)
            if space:
                return space

        for anfang, space in _ZIEL_HINWEISE:
            if ziel.startswith(anfang):
                return normalize_space_id(space, vertrag)
        for muster in (_DIREKT, _MCP_SPACE):
            gefunden = muster.match(ziel)
            if gefunden:
                space = normalize_space_id(gefunden.group(1).replace("_", "-"), vertrag) \
                    or normalize_space_id(gefunden.group(1), vertrag)
                if space:
                    return space
        return None
    except Exception:
        return None
