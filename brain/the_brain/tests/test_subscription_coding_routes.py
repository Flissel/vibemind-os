"""Contract tests for subscription-backed coding routes and agent manifests."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES_PATH = ROOT / "brain" / "the_brain" / "data" / "capabilities.yaml"
AGENTS_ROOT = ROOT / "openfang" / "agents"

OPENAI_AGENT = "brain-coder-openai"
ANTHROPIC_AGENT = "brain-coder-anthropic"


def _capabilities() -> list[dict[str, object]]:
    result = yaml.safe_load(CAPABILITIES_PATH.read_text(encoding="utf-8"))
    assert isinstance(result, list)
    return result


def _capability(name: str) -> dict[str, object]:
    for capability in _capabilities():
        if capability.get("capability") == name:
            return capability
    raise AssertionError(f"capability {name!r} is missing")


def _template(agent_name: str) -> tuple[str, dict[str, object]]:
    path = AGENTS_ROOT / agent_name / "agent.toml.tmpl"
    assert path.is_file(), f"missing agent template: {path}"
    raw = path.read_text(encoding="utf-8")
    return raw, tomllib.loads(raw)


def _matches(capability: dict[str, object], text: str) -> bool:
    patterns = capability.get("match_patterns")
    assert isinstance(patterns, list)
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _first_matching_capability(text: str) -> str | None:
    for capability in _capabilities():
        if _matches(capability, text):
            name = capability.get("capability")
            assert isinstance(name, str)
            return name
    return None


def test_coding_routes_are_provider_explicit_without_cross_provider_fallbacks():
    default = _capability("coding_task")
    anthropic = _capability("coding_task_anthropic")

    assert default["execution_target"] == "openfang:brain-coder-openai"
    assert anthropic["execution_target"] == "openfang:brain-coder-anthropic"
    assert anthropic["validator"] == default["validator"]
    assert "openclaude" not in str(default["description"]).lower()

    for route in (default, anthropic):
        assert "fallback" not in route
        assert "fallback_target" not in route
        assert "fallbacks" not in route


def test_anthropic_selectors_are_explicit_and_openai_is_the_deterministic_default():
    for phrase in (
        "use Claude to fix the failing test in tests/test_foo.py",
        "bitte mit Anthropic die Funktion in src/main.py schreiben",
        "Claude Code verwenden und den Fehler in app.py beheben",
    ):
        assert _first_matching_capability(phrase) == "coding_task_anthropic"

    assert _first_matching_capability(
        "fix the failing test in tests/test_foo.py"
    ) == "coding_task"


def test_subscription_agent_templates_preserve_coding_restrictions_and_use_matching_wrappers():
    base_raw, base = _template("brain-coder")
    assert "{{MEMORY}}" in base_raw

    expected = {
        OPENAI_AGENT: {
            "description": "VibeMind coding agent using the ChatGPT subscription through OpenCode.",
            "wrapper": "openfang_opencode_wrapper.cmd",
        },
        ANTHROPIC_AGENT: {
            "description": "VibeMind coding agent using the Claude Pro/Max subscription through official Claude Code.",
            "wrapper": "openfang_claude_subscription_wrapper.cmd",
        },
    }

    for name, contract in expected.items():
        raw, manifest = _template(name)
        model = manifest.get("model")
        assert isinstance(model, dict)

        assert manifest["name"] == name
        assert manifest["description"] == contract["description"]
        assert model["provider"] == "claude-code"
        assert model["base_url"].replace("\\", "/").endswith(
            f"scripts/{contract['wrapper']}"
        )
        assert (ROOT.parent / model["base_url"]).is_file()
        assert "{{MEMORY}}" in raw
        assert manifest["capabilities"] == base["capabilities"]
        assert manifest["mcp_allowed"] == base["mcp_allowed"]
        assert manifest["resources"] == base["resources"]
        assert "fallback_models" not in manifest
        assert "api_key_env" not in model
        assert "openrouter" not in raw.lower()
        assert "api.openai.com" not in raw.lower()
        assert "api.anthropic.com" not in raw.lower()
