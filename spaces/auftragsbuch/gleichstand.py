"""Gleichstand von Space-Vertraegen, Space-Verzeichnis und Werkzeug-Dienst.

Meldet Abweichungen als Saetze (nur Space-, Ereignis- und Werkzeugnamen);
eine leere Liste heisst Gleichstand.
"""
from __future__ import annotations

from spaces.auftragsbuch.vertrag import werkzeugname

FESTE_WERKZEUGE = ("auftrag_probe", "auftrag_lesen")
SERVER = "auftragsbuch"


def _werkzeuge_je_space(arten: dict) -> dict[str, set[str]]:
    """Erlaubte Werkzeugnamen je Space: feste Werkzeuge plus die der eigenen Auftragsarten."""
    erlaubt: dict[str, set[str]] = {}
    for a in arten.values():
        erlaubt.setdefault(a.space, set()).add(werkzeugname(a))
    return erlaubt


def gleichstand(
    arten: dict,
    registry: dict,
    capabilities: list[dict],
    dienst_werkzeuge: list[str] | None = None,
) -> list[str]:
    """Liste der Abweichungen zwischen Vertrag, Verzeichnis, Capabilities und Dienst."""
    abweichungen: list[str] = []
    eigene = _werkzeuge_je_space(arten)
    rechtetraeger_agenten: dict[str, str] = {}

    for space_id, eintrag in registry.items():
        eintrag = eintrag or {}
        freigeschaltet = list((eintrag.get("mcp_tools") or {}).get(SERVER) or [])
        hat_server = SERVER in (eintrag.get("mcp_servers") or [])

        # Regel 1: Freigeschaltete Werkzeuge sind feste oder eigene Auftragsarten.
        erlaubt = set(FESTE_WERKZEUGE) | eigene.get(space_id, set())
        for tool in freigeschaltet:
            if tool not in erlaubt:
                abweichungen.append(
                    f"Space {space_id}: Werkzeug {tool} unter mcp_tools.{SERVER} "
                    f"ist weder fest noch eine Auftragsart dieses Space."
                )

        # Regel 2: Ereignisse ueber den Server gehoeren dem Space und sind freigeschaltet.
        for name, ereignis in (eintrag.get("events") or {}).items():
            ereignis = ereignis or {}
            if (ereignis.get("execution") or {}).get("server") != SERVER:
                continue
            if not name.startswith(f"{space_id}."):
                abweichungen.append(
                    f"Space {space_id}: Ereignis {name} ueber {SERVER} beginnt nicht mit {space_id}."
                )
            tool = ereignis.get("tool")
            if tool not in freigeschaltet:
                abweichungen.append(
                    f"Space {space_id}: Ereignis {name} nutzt Werkzeug {tool}, "
                    f"das nicht unter mcp_tools.{SERVER} freigeschaltet ist."
                )

        if hat_server and eintrag.get("agent"):
            rechtetraeger_agenten[str(eintrag["agent"])] = space_id

    # Regel 3: Der Agent eines Rechtetraegers ist nie Ziel einer Capability.
    for cap in capabilities:
        ziel = cap.get("execution_target")
        if not isinstance(ziel, str) or not ziel.startswith("openfang:"):
            continue
        agent = ziel[len("openfang:"):]
        if agent in rechtetraeger_agenten:
            abweichungen.append(
                f"Capability {cap.get('capability')}: Ziel openfang:{agent} ist der Agent "
                f"des Rechtetraegers {rechtetraeger_agenten[agent]} und darf kein Agenten-Ziel sein."
            )

    # Regel 4: Der Dienst bietet genau die festen und alle Auftragsart-Werkzeuge.
    if dienst_werkzeuge is not None:
        soll = set(FESTE_WERKZEUGE) | {werkzeugname(a) for a in arten.values()}
        ist = set(dienst_werkzeuge)
        for tool in sorted(soll - ist):
            abweichungen.append(f"Dienst: Werkzeug {tool} fehlt.")
        for tool in sorted(ist - soll):
            abweichungen.append(f"Dienst: Werkzeug {tool} ist im Vertrag nicht vorgesehen.")

    return abweichungen
