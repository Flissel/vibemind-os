from __future__ import annotations

import sys
from pathlib import Path

import yaml

from spaces.learning.contracts.events import EVENT_TOOL_MAP


REPO_ROOT = Path(__file__).resolve().parents[4]
BRAIN_ROOT = REPO_ROOT / "brain" / "the_brain"
if str(BRAIN_ROOT) not in sys.path:
    sys.path.insert(0, str(BRAIN_ROOT))

from core.space_contract import load_space_contract, normalize_space_id  # noqa: E402
from spaces._navigator.registry import SPACES, resolve_alias  # noqa: E402


def _registry() -> dict:
    return yaml.safe_load(
        (REPO_ROOT / "config" / "space_agent_registry.yml").read_text(encoding="utf-8")
    )


def test_learning_is_a_canonical_space_not_an_alias() -> None:
    contract = load_space_contract()

    assert "learning" in contract.space_ids
    assert normalize_space_id("learning", contract) == "learning"
    assert resolve_alias("learning") == "learning"
    assert SPACES["learning"]["event_prefix"] == "learning."
    assert SPACES["learning"]["renderer_id"] == "learning"


def test_learning_agent_and_mcp_scope_are_closed() -> None:
    learning = _registry()["spaces"]["learning"]
    expected_tools = sorted(tool.value for tool in EVENT_TOOL_MAP.values())

    assert learning["agent"] == "brain-learning"
    assert learning["enabled"] is True
    assert learning["prefixes"] == ["learning."]
    assert learning["mcp_servers"] == ["spaces-learning"]
    assert sorted(learning["mcp_tools"]) == ["spaces-learning"]
    assert sorted(learning["mcp_tools"]["spaces-learning"]) == expected_tools


def test_every_learning_event_routes_to_its_exact_mcp_tool() -> None:
    learning = _registry()["spaces"]["learning"]
    events = learning["events"]
    contract = load_space_contract()

    assert set(events) == {event.value for event in EVENT_TOOL_MAP}
    for event, tool in EVENT_TOOL_MAP.items():
        spec = events[event.value]
        assert spec["tool"] == tool.value
        assert spec["execution"] == {"kind": "mcp", "server": "spaces-learning"}
        assert spec["required_provenance"] == ["approval_ref", "cost_ref"]
        assert contract.event_space_map[event.value] == "learning"


def test_bridge_map_claims_only_the_canonical_learning_agent() -> None:
    bridge = yaml.safe_load(
        (REPO_ROOT / "bridge" / "config" / "space_agent_map.yaml").read_text(
            encoding="utf-8"
        )
    )
    mappings = bridge["mappings"]

    assert mappings["learning"] == "brain-learning"
    assert "learn" not in mappings
    assert "quiz" not in mappings
