"""The bridge space->agent map must mirror the registry.

`config/space_agent_registry.yml` declares itself the single source of truth
(ABSORB-7: "LEGACY_SPACE_AGENT_MAP derives/validates against this"), but
nothing enforced it — on 2026-08-07 seven of thirteen entries had drifted,
so the bridge routed bubbles and minibook to `brain-writer`, and schedule,
video, flowzen and mirofish all to `vibemind`.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY = REPO_ROOT / "config" / "space_agent_registry.yml"
BRIDGE_MAP = REPO_ROOT / "bridge" / "config" / "space_agent_map.yaml"


def _registry_agents() -> dict[str, str]:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    agents: dict[str, str] = {}
    for space, spec in (data.get("spaces") or {}).items():
        if isinstance(spec, dict) and spec.get("agent"):
            agents[str(space)] = str(spec["agent"])
    return agents


def _bridge_mappings() -> dict[str, str]:
    data = yaml.safe_load(BRIDGE_MAP.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in (data.get("mappings") or {}).items()}


def test_bridge_map_matches_registry():
    registry = _registry_agents()
    bridge = _bridge_mappings()

    mismatches = {
        space: (agent, registry[space])
        for space, agent in bridge.items()
        if space in registry and agent != registry[space]
    }
    assert not mismatches, (
        "bridge/config/space_agent_map.yaml drifted from the registry "
        f"(space: bridge -> registry): {mismatches}"
    )


def test_bridge_map_has_no_unknown_spaces():
    registry = _registry_agents()
    unknown = sorted(set(_bridge_mappings()) - set(registry))
    assert not unknown, (
        f"bridge map routes spaces the registry does not declare: {unknown}"
    )
