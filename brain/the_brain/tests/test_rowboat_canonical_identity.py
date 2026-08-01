"""Contract tests for the canonical Rowboat Child-Space identity."""

from __future__ import annotations

from pathlib import Path
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "brain" / "the_brain"))
sys.path.insert(0, str(REPO_ROOT))

from core.space_routing_head import EVENT_SPACE_MAP, SPACE_NAMES as ROUTING_SPACE_NAMES
from spaces._navigator.registry import SPACES, get_renderer_id, resolve_alias


def _load_yaml(relative_path: str) -> dict:
    with (REPO_ROOT / relative_path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_rowboat_is_the_only_canonical_child_space_id() -> None:
    """All Child registries normalize the temporary roarboot ingress alias to rowboat."""
    space_registry = _load_yaml("config/space_agent_registry.yml")
    bridge_map = _load_yaml("bridge/config/space_agent_map.yaml")

    assert "rowboat" in space_registry["spaces"]
    assert "roarboot" not in space_registry["spaces"]
    assert space_registry["spaces"]["rowboat"]["prefixes"] == ["rowboat."]
    assert all(
        event_name.startswith("rowboat.")
        for event_name in space_registry["spaces"]["rowboat"]["events"]
    )

    assert bridge_map["mappings"]["rowboat"] == "vibemind"
    assert "roarboot" not in bridge_map["mappings"]

    assert "rowboat" in ROUTING_SPACE_NAMES
    assert "roarboot" not in ROUTING_SPACE_NAMES
    assert EVENT_SPACE_MAP["rowboat.query"] == "rowboat"
    assert EVENT_SPACE_MAP["roarboot.query"] == "rowboat"

    assert "rowboat" in SPACES
    assert SPACES["rowboat"]["event_prefix"] == "rowboat."
    assert SPACES["rowboat"]["stream"] == "events:tasks:rowboat"
    assert SPACES["rowboat"]["capabilities"] == ["rowboat.query"]
    assert resolve_alias("roarboot") == "rowboat"
    assert get_renderer_id("rowboat") == "roarboot"
