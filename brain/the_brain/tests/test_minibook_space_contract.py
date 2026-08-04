"""Static Brain contract for Minibook's deterministic status MCP path."""

from pathlib import Path

import yaml

from core import world_observer
from core.capability_targets import resolve_registry_execution_target


ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES_PATH = ROOT / "brain" / "the_brain" / "data" / "capabilities.yaml"
REGISTRY_PATH = ROOT / "config" / "space_agent_registry.yml"


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_minibook_status_uses_only_the_bound_deterministic_mcp_tool_and_independent_truth():
    registry = _load_yaml(REGISTRY_PATH)
    event = registry["spaces"]["minibook"]["events"]["minibook.status"]
    assert event == {
        "tool": "minibook_status",
        "required_params": [],
        "execution": {"kind": "mcp", "server": "spaces-minibook"},
    }
    assert "spaces-minibook" in registry["spaces"]["minibook"]["mcp_servers"]

    capabilities = {item["capability"]: item for item in _load_yaml(CAPABILITIES_PATH)}
    capability = capabilities["minibook.status"]
    assert capability["execution_target"] == "mcp:brain-knowledge:spaces-minibook:minibook_status"
    assert capability["validator"] == {
        "kind": "truth:http_ok",
        "on_fail": "block",
        "postcondition": {
            "check": "http_ok",
            "url_env": "MINIBOOK_STATUS_URL",
            "method": "HEAD",
            "expect_status_lt": 300,
        },
    }
    assert "openfang:" not in capability["execution_target"]
    assert "direct:" not in capability["execution_target"]
    assert resolve_registry_execution_target("minibook.status") == capability["execution_target"]


def test_minibook_non_status_events_remain_legacy_direct_paths():
    registry = _load_yaml(REGISTRY_PATH)
    events = registry["spaces"]["minibook"]["events"]
    assert set(events) == {
        "minibook.discuss",
        "minibook.collaborate",
        "minibook.status",
        "minibook.list_projects",
    }
    assert events["minibook.discuss"] == {"tool": "fetch", "required_params": ["topic"]}
    assert events["minibook.collaborate"] == {"tool": "fetch", "required_params": ["topic"]}
    assert events["minibook.list_projects"] == {"tool": "fetch", "required_params": []}


def test_minibook_truth_observation_rejects_redirects_without_following_them(monkeypatch):
    class Response:
        status_code = 302

    observed_calls = []

    def head(url, *, timeout, allow_redirects):
        observed_calls.append((url, timeout, allow_redirects))
        return Response()

    import requests

    monkeypatch.setattr(world_observer, "GROUND_TRUTH_ENABLED", True)
    monkeypatch.setenv("MINIBOOK_STATUS_URL", "https://minibook.truth.example/status")
    monkeypatch.setattr(requests, "head", head)
    observed = world_observer.observe({
        "check": "http_ok",
        "url_env": "MINIBOOK_STATUS_URL",
        "method": "HEAD",
        "expect_status_lt": 300,
    })

    assert observed.verdict == world_observer.REFUTED
    assert observed.signal == {
        "url": "https://minibook.truth.example/status",
        "status_code": 302,
        "method": "HEAD",
    }
    assert observed_calls == [
        ("https://minibook.truth.example/status", world_observer.OBSERVE_TIMEOUT, False),
    ]
