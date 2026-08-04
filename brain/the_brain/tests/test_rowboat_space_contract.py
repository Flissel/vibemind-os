"""Contract for the canonical Rowboat Brain -> OpenFang path."""

from pathlib import Path

import yaml

from core.capability_router import CapabilityRouter
from core import world_observer
from core.capability_targets import resolve_registry_execution_target


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


def test_rowboat_status_uses_only_the_bound_deterministic_mcp_tool_and_independent_truth():
    registry = _load_yaml(REGISTRY_PATH)
    event = registry["spaces"]["rowboat"]["events"]["rowboat.status"]
    assert event == {
        "tool": "rowboat_status",
        "required_params": [],
        "execution": {"kind": "mcp", "server": "spaces-rowboat"},
    }
    assert "spaces-rowboat" in registry["spaces"]["rowboat"]["mcp_servers"]

    capabilities = {item["capability"]: item for item in _load_yaml(CAPABILITIES_PATH)}
    capability = capabilities["rowboat_status"]
    assert capability["execution_target"] == "mcp:rowboat-chat:spaces-rowboat:rowboat_status"
    assert capability["validator"] == {
        "kind": "truth:http_ok",
        "on_fail": "block",
        "postcondition": {"check": "http_ok", "url_env": "ROWBOAT_URL", "method": "HEAD"},
    }
    assert "openfang:" not in capability["execution_target"]
    assert "direct:" not in capability["execution_target"]
    assert resolve_registry_execution_target("rowboat.status") == capability["execution_target"]


def test_rowboat_truth_observation_requires_its_own_env_url(monkeypatch):
    monkeypatch.setattr(world_observer, "GROUND_TRUTH_ENABLED", True)
    monkeypatch.delenv("ROWBOAT_URL", raising=False)
    observed = world_observer.observe({"check": "http_ok", "url_env": "ROWBOAT_URL", "method": "HEAD"})
    assert observed.verdict == world_observer.UNVERIFIED
    assert observed.reason == "ROWBOAT_URL is required"


def test_rowboat_truth_observation_independently_heads_its_env_url(monkeypatch):
    class Response:
        status_code = 204

    observed_calls = []

    def head(url, *, timeout, allow_redirects):
        observed_calls.append((url, timeout, allow_redirects))
        return Response()

    import requests

    monkeypatch.setattr(world_observer, "GROUND_TRUTH_ENABLED", True)
    monkeypatch.setenv("ROWBOAT_URL", "https://rowboat.truth.example")
    monkeypatch.setattr(requests, "head", head)
    observed = world_observer.observe({"check": "http_ok", "url_env": "ROWBOAT_URL", "method": "HEAD"})
    assert observed.verdict == world_observer.VERIFIED
    assert observed.signal == {
        "url": "https://rowboat.truth.example",
        "status_code": 204,
        "method": "HEAD",
    }
    assert observed_calls == [("https://rowboat.truth.example", world_observer.OBSERVE_TIMEOUT, False)]


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
