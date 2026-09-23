"""Canonical space identity, routing, and registry diagnostics.

``config/space_agent_registry.yml`` is the authority for public space IDs and
event ownership.  This module reports catalog consistency only; it never
treats that structural result as external execution availability.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

import yaml


CANONICAL_ALIASES: Dict[str, str] = {
    "autogen": "agentfarm",
    "roarboot": "rowboat",
    "shuttles": "bubbles",
}

_REGISTRY_ENV = "SPACE_AGENT_REGISTRY_PATH"
_REGISTRY_RELATIVE = Path("config") / "space_agent_registry.yml"


class RegistryNotFound(FileNotFoundError):
    """Die Space-Registry liegt nirgends, wo dieses Modul sie erwartet."""


def resolve_registry_path(start: Optional[Path] = None) -> Path:
    """Findet `config/space_agent_registry.yml`, ohne die Ordnertiefe zu raten.

    Vorher stand an vier Stellen `Path(__file__).resolve().parents[3]`. Das
    trifft im Repo (`brain/the_brain/core/x.py` -> `vibemind-os`) und bricht in
    der Ausbringung, wo dieselbe Datei als `/app/core/x.py` liegt: dort gibt es
    keine vier Eltern, und Python wirft `IndexError: 3` - eine Meldung, die
    nichts ueber die Ursache sagt. In `space_contract` stand die Zeile sogar auf
    Modulebene, der Import scheiterte also komplett.

    Reihenfolge:
      1. `SPACE_AGENT_REGISTRY_PATH` - die ausdrueckliche Ansage gewinnt immer.
         Denselben Namen setzt bereits `Dockerfile.deterministic-gateway`.
      2. Aufwaerts suchen, bis `config/space_agent_registry.yml` auftaucht.
         Das kommt ohne jede Annahme ueber die Tiefe aus.
      3. Sonst `RegistryNotFound` mit allen geprueften Pfaden - ein Aufrufer
         soll lesen koennen, WO gesucht wurde, statt `IndexError: 3` zu sehen.
    """
    configured = os.environ.get(_REGISTRY_ENV, "").strip()
    if configured:
        return Path(configured)

    here = (start or Path(__file__)).resolve()
    geprueft = []
    for parent in here.parents:
        kandidat = parent / _REGISTRY_RELATIVE
        geprueft.append(kandidat)
        if kandidat.is_file():
            return kandidat

    raise RegistryNotFound(
        f"space registry not found. Set {_REGISTRY_ENV}, or place "
        f"{_REGISTRY_RELATIVE} above {here}. Tried: "
        + ", ".join(str(p) for p in geprueft)
    )



@dataclass(frozen=True)
class SpaceContract:
    version: int
    source: Path
    spaces: Mapping[str, Mapping[str, Any]]
    space_ids: Tuple[str, ...]
    event_space_map: Mapping[str, str]


def load_space_contract(path: Optional[Path] = None) -> SpaceContract:
    # Erst beim Aufruf aufloesen, nicht beim Import: ein Modul, das sich
    # beim Laden am Dateisystem festbeisst, nimmt jedem Aufrufer die
    # Moeglichkeit, den Pfad selbst zu setzen.
    path = Path(path) if path is not None else resolve_registry_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    spaces = raw.get("spaces")
    if not isinstance(spaces, dict) or not spaces:
        raise ValueError(f"space registry has no spaces: {path}")

    event_space_map: Dict[str, str] = {}
    for space_id, meta in spaces.items():
        if not isinstance(space_id, str) or not isinstance(meta, dict):
            raise ValueError(f"invalid space entry in {path}: {space_id!r}")
        events = meta.get("events", {})
        if not isinstance(events, dict):
            raise ValueError(f"events for {space_id!r} must be a mapping")
        for event_type in events:
            if event_type in event_space_map:
                raise ValueError(f"duplicate event ownership: {event_type}")
            event_space_map[event_type] = space_id

    for event_type, space_id in tuple(event_space_map.items()):
        if space_id == "rowboat" and event_type.startswith("rowboat."):
            legacy_event = f"roarboot.{event_type.removeprefix('rowboat.')}"
            event_space_map.setdefault(legacy_event, space_id)

    return SpaceContract(
        version=int(raw.get("version", 0)),
        source=path,
        spaces=spaces,
        space_ids=tuple(spaces),
        event_space_map=event_space_map,
    )


def normalize_space_id(value: str, contract: Optional[SpaceContract] = None) -> Optional[str]:
    contract = contract or load_space_contract()
    candidate = (value or "").strip().lower()
    candidate = CANONICAL_ALIASES.get(candidate, candidate)
    return candidate if candidate in contract.spaces else None


def registry_health(
    contract: Optional[SpaceContract] = None,
    *,
    capabilities: Optional[list[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return a deterministic, queryable consistency view of Brain catalogs."""
    contract = contract or load_space_contract()
    issues = []

    from spaces._navigator.registry import SPACES as navigator_spaces
    navigator_ids = {
        normalize_space_id(space_id, contract)
        for space_id in navigator_spaces
        if space_id != "brain"
    }
    navigator_ids.discard(None)
    missing = sorted(set(contract.space_ids) - navigator_ids)
    if missing:
        issues.append({"kind": "navigator_missing_spaces", "spaces": missing})

    from .capability_targets import supported_kinds
    supported = sorted(supported_kinds())
    used = sorted({
        str(item["execution_target"]).split(":", 1)[0].lower()
        for item in (capabilities or [])
        if isinstance(item, Mapping) and item.get("execution_target")
    })
    unsupported = sorted(set(used) - set(supported))
    if unsupported:
        issues.append({"kind": "unsupported_executor_kinds", "kinds": unsupported})

    return {
        "status": "ok" if not issues else "degraded",
        "source": str(contract.source),
        "canonical_space_count": len(contract.space_ids),
        "event_count": len(contract.event_space_map),
        "executor_kinds_used": used,
        "executor_kinds_supported": supported,
        "issues": issues,
    }
