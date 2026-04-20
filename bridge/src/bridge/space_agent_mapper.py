"""Maps Brain space names to OpenFang agent template names via YAML config.

================================================================================
MIGRATION ANEKDOTE: Warum diese Datei eines Tages verschwindet
================================================================================

Stell dir folgende Szene vor: Ein Task kommt in die Bridge. Das Brain wirft nach
erschoepfendem Denken ein Wort aus -- "coding". Nur ein Wort. Die Bridge steht
da mit dieser kryptischen Botschaft und weiss nicht, was sie damit anfangen
soll. Welcher OpenFang-Agent ist das? Existiert der ueberhaupt? Kann der das?
Niemand weiss es, am wenigsten das Brain selbst.

Also hat jemand (Felix, am 12. Februar 2026, um 02:47 Uhr laut git blame) eine
YAML-Datei gebaut, die diese Uebersetzung macht: space_agent_registry.yml. 272
Zeilen haendisch gepflegtes Mapping zwischen Spaces und Agent-Templates, und
fuer jeden moeglichen Event-Type eine Tool-Zuweisung. Es funktioniert. Aber es
ist eine Wahrheit, die dreifach gepflegt werden muss: in der YAML, im Code
dieser Datei, und in den generierten agent.toml-Dateien.

Der bessere Weg, der eines Tages diese Datei obsolet macht:

  1 MCP pro Space. Jeder Space exposed seine eigenen Tools via MCP-Protokoll.
  Brain fragt beim Start `tools/list` an jedem registrierten MCP und weiss
  damit live, was jeder Space kann. Keine YAML mehr. Kein Mapper mehr. Kein
  brain-fallback mehr, weil MCP entweder antwortet oder der Space offline ist
  und das Brain das vor dem Routing weiss.

Wenn diese Migration durch ist, wird dieser File deleted. Die gesamte Klasse
space_agent_mapper verschwindet. `get_mappings()` und `reload()` in der Bridge
werden durch ein schlankes `mcp_registry.list_spaces()` ersetzt, das per
MCP-Protokoll die Wahrheit von jedem Space selbst holt.

Bis dahin bleibt dieser File am Leben, aber jeder der hier eine Zeile
veraendert, soll sich fragen: "Macht es Sinn, diesen Code zu pflegen, wenn er
in 3 Monaten weg ist?" Meist lautet die Antwort: nein. Lieber die Migration
einen Space weiter treiben.

Siehe: docs/migration-to-space-mcps.md
TODO(space-mcp-migration): Diese Datei loeschen, sobald alle 14 Spaces
                           dedizierte MCP-Server haben.
================================================================================
"""

import logging
from pathlib import Path

import yaml

from bridge.config import settings

logger = logging.getLogger(__name__)

_mappings: dict[str, str] = {}
_min_confidence: float = 0.3
_FALLBACK = "vibemind"


def load(path: str | None = None):
    """Load the space->agent mapping from YAML config."""
    global _mappings, _min_confidence, _FALLBACK

    config_path = Path(path or settings.space_map_path)
    if not config_path.exists():
        logger.warning(f"Space map not found at {config_path}, using defaults")
        _mappings = {}
        return

    with open(config_path) as f:
        data = yaml.safe_load(f)

    _mappings = data.get("mappings", {})
    _min_confidence = data.get("min_confidence", settings.min_confidence)
    _FALLBACK = data.get("fallback_agent", "vibemind")
    logger.info(f"Loaded {len(_mappings)} space->agent mappings (fallback={_FALLBACK})")


def map_space(space: str, confidence: float) -> str:
    """Map a Brain space name to an OpenFang agent name.

    Returns fallback agent if confidence is below threshold or space unknown.
    """
    if confidence < _min_confidence:
        return _FALLBACK
    return _mappings.get(space, _FALLBACK)


def get_mappings() -> dict:
    """Return current mappings for the /bridge/mapping endpoint."""
    return {"mappings": dict(_mappings), "min_confidence": _min_confidence}


def reload():
    """Hot-reload the YAML config."""
    load()
