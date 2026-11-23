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


def _traeger_je_space(eintrag: dict) -> tuple[list[dict], list[str]]:
    """Rechtetraeger eines Space (`rights_carriers`) und Meldungen zu fehlerhaften Eintraegen.

    Gueltig ist ein Eintrag mit `agent`. Ein Traeger nutzt das Auftragsbuch, wenn er den
    Server unter `mcp_servers` oder Werkzeuge unter `mcp_tools.auftragsbuch` nennt.
    """
    roh = eintrag.get("rights_carriers")
    if roh is None:
        return [], []
    if not isinstance(roh, list):
        return [], ["rights_carriers muss eine Liste sein."]
    gueltig: list[dict] = []
    meldungen: list[str] = []
    for position, traeger in enumerate(roh):
        agent = traeger.get("agent") if isinstance(traeger, dict) else None
        if not isinstance(agent, str) or not agent.strip():
            meldungen.append(f"rights_carriers[{position}] hat keinen agent.")
            continue
        gueltig.append(traeger)
    return gueltig, meldungen


def _traeger_werkzeuge(traeger: dict) -> list[str]:
    tools = traeger.get("mcp_tools")
    liste = tools.get(SERVER) if isinstance(tools, dict) else None
    return list(liste) if isinstance(liste, list) else []


def _traeger_nennt_server(traeger: dict) -> bool:
    server = traeger.get("mcp_servers")
    return isinstance(server, list) and SERVER in server


def _traeger_nutzt_server(traeger: dict) -> bool:
    return _traeger_nennt_server(traeger) or bool(_traeger_werkzeuge(traeger))


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
        ganzer_space = eintrag.get("rights_carrier") is True
        traeger, traeger_fehler = _traeger_je_space(eintrag)
        for text in traeger_fehler:
            abweichungen.append(f"Space {space_id}: {text}")
        traeger_am_server = [t for t in traeger if _traeger_nutzt_server(t)]
        if ganzer_space and traeger:
            abweichungen.append(
                f"Space {space_id}: rights_carrier: true und rights_carriers zugleich; es gilt nur ein Weg."
            )
        for t in traeger_am_server:
            if not _traeger_nennt_server(t):
                abweichungen.append(
                    f"Space {space_id}: Rechtetraeger {t['agent']} schaltet Werkzeuge von {SERVER} frei, "
                    f"nennt den Server aber nicht unter mcp_servers."
                )
            freigeschaltet += [w for w in _traeger_werkzeuge(t) if w not in freigeschaltet]

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

        # Regel 5: Die Auftragsbuch-Rechte liegen nie beim Agenten, der Nachrichten bearbeitet.
        # Entweder ist der ganze Space ein Rechtetraeger (rights_carrier: true), oder ein Eintrag
        # unter rights_carriers haelt sie.
        if hat_server and not ganzer_space:
            abweichungen.append(
                f"Space {space_id}: Agent {eintrag.get('agent')} nutzt den Server {SERVER}, traegt aber "
                f"nicht rights_carrier: true; die Rechte gehoeren in rights_carriers."
            )
        ereignisse_am_server = any(
            isinstance(e, dict) and isinstance(e.get("execution"), dict)
            and e["execution"].get("server") == SERVER
            for e in (eintrag.get("events") or {}).values()
        )
        if (hat_server or ereignisse_am_server) and not ganzer_space and not traeger_am_server:
            abweichungen.append(
                f"Space {space_id}: nutzt den Server {SERVER}, hat aber keinen Rechtetraeger "
                f"(rights_carrier: true oder rights_carriers)."
            )

        if hat_server and eintrag.get("agent"):
            rechtetraeger_agenten[str(eintrag["agent"])] = space_id
        for t in traeger_am_server:
            rechtetraeger_agenten[str(t["agent"]).strip()] = space_id

    # Regel 3: Der Agent eines Rechtetraegers (Space-Agent oder Eintrag in rights_carriers) ist nie Ziel einer Capability.
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
