"""Contract tests for subscription-backed coding routes and agent manifests."""

from __future__ import annotations

import importlib
import re
import sys
import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES_PATH = ROOT / "brain" / "the_brain" / "data" / "capabilities.yaml"
AGENTS_ROOT = ROOT / "openfang" / "agents"
_BRAIN_ROOT = ROOT / "brain" / "the_brain"
if str(_BRAIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAIN_ROOT))

CapabilityRouter = importlib.import_module("core.capability_router").CapabilityRouter

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


def test_actual_router_prefers_each_explicit_anthropic_selector_without_cross_route_fallback():
    router = CapabilityRouter(CAPABILITIES_PATH)

    for phrase in (
        "use Claude to fix the failing test in tests/test_foo.py",
        "use Anthropic to fix tests/test_foo.py",
        "bitte mit Anthropic die Funktion in src/main.py schreiben",
        "mit Claude die Funktion in src/main.py schreiben",
        "Claude Code verwenden und den Fehler in app.py beheben",
        "Anthropic verwenden und app.py reparieren",
    ):
        match = router.route(phrase)
        assert match is not None, phrase
        assert match.capability == "coding_task_anthropic", phrase
        assert match.execution_target == "openfang:brain-coder-anthropic", phrase
        assert match.match_method == "regex", phrase

    default_match = router.route("fix the failing test in tests/test_foo.py")
    assert default_match is not None
    assert default_match.capability == "coding_task"
    assert default_match.execution_target == "openfang:brain-coder-openai"


def test_actual_router_keeps_explicit_anthropic_operational_requests_off_openai():
    router = CapabilityRouter(CAPABILITIES_PATH)

    for phrase in (
        "use Claude to run tests/test_foo.py",
        "use Anthropic to review src/main.py",
        "mit Claude nach src/main.py suchen",
        "use Claude to delete obsolete.py",
    ):
        match = router.route(phrase)
        assert match is not None, phrase
        assert match.capability == "coding_task_anthropic", phrase
        assert match.execution_target == "openfang:brain-coder-anthropic", phrase

    unsupported = router.route("use Claude to summarize src/main.py")
    assert unsupported is None or unsupported.execution_target != "openfang:brain-coder-openai"


def test_actual_router_keeps_all_explicit_anthropic_coding_operations_off_openai():
    router = CapabilityRouter(CAPABILITIES_PATH)

    for phrase in (
        "use Anthropic to fix the bug in auth",
        "use Claude to refactor this function",
        "use Anthropic to implement an API",
        "use Claude to create a github repo",
        "use Anthropic to deploy to vercel",
        "use Claude to commit these changes",
    ):
        match = router.route(phrase)
        assert match is not None, phrase
        assert match.capability == "coding_task_anthropic", phrase
        assert match.execution_target == "openfang:brain-coder-anthropic", phrase


def test_explicit_anthropic_selector_preserves_non_coding_collision_routes():
    router = CapabilityRouter(CAPABILITIES_PATH)
    expected = {
        "use Claude to open https://example.com": "browser_automation",
        "use Anthropic for a security scan": "security_scan",
        "use Anthropic to review this pull request": "code_review",
        "use Claude to find the function": "code_search",
    }

    for phrase, capability in expected.items():
        match = router.route(phrase)
        assert match is not None, phrase
        assert match.capability == capability, phrase
        assert match.execution_target != "openfang:brain-coder-openai", phrase


def test_actual_router_requires_provider_selection_and_concrete_coding_mutation_for_anthropic():
    router = CapabilityRouter(CAPABILITIES_PATH)

    for phrase in (
        "use Claude",
        "use Anthropic to summarize this document",
        "mit Claude die GitHub-Seite im Browser öffnen",
        "use Anthropic to review this pull request",
        "mit Claude nach einer Funktion im Code suchen",
        "use Anthropic for a security scan",
        "Claude is the name of this code subject",
    ):
        match = router.route(phrase)
        assert match is None or match.capability != "coding_task_anthropic", phrase

    for phrase in (
        "use Claude to fix tests/test_foo.py",
        "use Anthropic to fix tests/test_foo.py",
        "mit Claude die Funktion in src/main.py schreiben",
        "mit Anthropic die Funktion in src/main.py schreiben",
        "Claude Code verwenden und app.py reparieren",
        "Anthropic verwenden und app.py reparieren",
    ):
        match = router.route(phrase)
        assert match is not None, phrase
        assert match.capability == "coding_task_anthropic", phrase
        assert match.execution_target == "openfang:brain-coder-anthropic", phrase

    default_match = router.route("fix tests/test_foo.py")
    assert default_match is not None
    assert default_match.capability == "coding_task"
    assert default_match.execution_target == "openfang:brain-coder-openai"


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
        assert model["base_url"] == contract["wrapper"]
        assert "{{MEMORY}}" in raw
        assert manifest["capabilities"] == base["capabilities"]
        assert manifest["mcp_servers"] == base["mcp_allowed"]["servers"]
        assert manifest["resources"] == base["resources"]
        assert "fallback_models" not in manifest
        assert "api_key_env" not in model
        assert "openrouter" not in raw.lower()
        assert "api.openai.com" not in raw.lower()
        assert "api.anthropic.com" not in raw.lower()
