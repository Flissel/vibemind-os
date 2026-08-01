"""Contract for the canonical Rowboat Brain -> OpenFang path."""

from pathlib import Path

import yaml

from core.capability_router import CapabilityRouter


ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES_PATH = ROOT / "brain" / "the_brain" / "data" / "capabilities.yaml"
REGISTRY_PATH = ROOT / "config" / "space_agent_registry.yml"
BRIDGE_MAP_PATH = ROOT / "bridge" / "config" / "space_agent_map.yaml"

EXPECTED_EVENTS = {
    "rowboat.search": ("rowboat_search", "search_knowledge", ["query"]),
    "rowboat.query": ("rowboat_query", "query_knowledge", ["query"]),
    "rowboat.email_draft": (
        "rowboat_email_draft",
        "draft_email",
        ["recipient", "topic"],
    ),
    "rowboat.meeting_brief": (
        "rowboat_meeting_brief",
        "generate_meeting_brief",
        ["topic"],
    ),
    "rowboat.deck": ("rowboat_deck", "generate_deck", ["topic"]),
}


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_rowboat_events_use_canonical_identity_and_real_openfang_target():
    registry = _load_yaml(REGISTRY_PATH)
    spaces = registry["spaces"]
    assert "rowboat" in spaces
    assert "roarboot" not in spaces

    rowboat = spaces["rowboat"]
    assert rowboat["agent"] == "rowboat-chat"
    assert rowboat["prefixes"] == ["rowboat."]
    assert _load_yaml(BRIDGE_MAP_PATH)["mappings"]["rowboat"] == "rowboat-chat"

    capabilities = {
        item["capability"]: item for item in _load_yaml(CAPABILITIES_PATH)
    }
    for event, (capability_name, tool, required_params) in EXPECTED_EVENTS.items():
        event_spec = rowboat["events"][event]
        assert event_spec["tool"] == tool
        assert event_spec["required_params"] == required_params

        capability = capabilities[capability_name]
        assert capability["execution_target"] == "openfang:rowboat-chat"
        assert capability["agents"]["primary"] == ["rowboat-chat"]
        assert capability["validator"] == {
            "kind": "rule:non_empty_result",
            "on_fail": "block",
        }
        assert capability.get("local_fallback") is None


def test_roarboot_is_only_an_input_alias_for_rowboat_capabilities():
    capabilities = {
        item["capability"]: item for item in _load_yaml(CAPABILITIES_PATH)
    }

    for capability_name, _, _ in EXPECTED_EVENTS.values():
        capability = capabilities[capability_name]
        assert capability_name.startswith("rowboat_")
        assert any(
            "roarboot" in pattern.lower()
            for pattern in capability.get("match_patterns", [])
        )


def test_voice_and_api_phrases_route_to_canonical_rowboat_capabilities():
    router = CapabilityRouter(CAPABILITIES_PATH)
    cases = {
        "durchsuche Rowboat nach Projekt Phoenix": "rowboat_search",
        "durchsuche Roarboot nach Projekt Phoenix": "rowboat_search",
        "frage Rowboat was wir über Anna wissen": "rowboat_query",
        "frage Roarboot was wir über Anna wissen": "rowboat_query",
        "entwirf mit Rowboat eine Mail an Anna über Phoenix": "rowboat_email_draft",
        "erstelle mit Rowboat ein Meeting-Briefing zu Phoenix": "rowboat_meeting_brief",
        "erstelle mit Rowboat ein Deck zu Phoenix": "rowboat_deck",
    }

    for phrase, expected in cases.items():
        match = router.route(phrase)
        assert match is not None
        assert match.capability == expected
