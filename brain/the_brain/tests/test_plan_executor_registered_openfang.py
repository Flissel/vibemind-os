"""Fail-closed routing for canonical Space events through OpenFang."""

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core.plan_executor import PlanExecutor
from core.plan_schema import HopSpec


class _Registry:
    def __init__(self, agents: dict[str, str] | None = None) -> None:
        self.agents = agents or {}

    def get_event_agent(self, event_id: str) -> str | None:
        return self.agents.get(event_id)


class _Executor:
    def __init__(self, result: dict) -> None:
        self.result = result

    def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
        return self.result


def _hop(*, capability: str = "bubble_create", target: str = "direct:test:run") -> HopSpec:
    return HopSpec(
        step_id="step-1",
        description=capability,
        capability=capability,
        execution_target=target,
    )


def _disable_kg_hits(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: []
    )


def test_deterministic_bubble_create_gateway_failure_never_invokes_llm_or_direct_executor(monkeypatch):
    """bubble.create reaches only its registry-declared OpenFang MCP tool."""
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"bubble.create": "brain-bubbles"}),
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor(
            {"ok": False, "error": "OpenFangUnavailable: gateway down"}
        ),
    )

    result = PlanExecutor()._exec_hop(_hop(), {})

    assert result.ok is False
    assert built_targets == [
        "mcp:brain-bubbles:spaces-ideas:bubble_create"
    ]
    assert all(
        not target.startswith(("openfang:", "direct:", "supabase:"))
        for target in built_targets
    )


@pytest.mark.parametrize(
    "execution_lines",
    [
        [],
        ["        execution:", "          kind: mcp"],
    ],
)
def test_deterministic_bubble_create_missing_mcp_metadata_fails_closed(
    monkeypatch, tmp_path, execution_lines
):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    registry = tmp_path / "space_agent_registry.yml"
    registry.write_text(
        "\n".join(
            [
                "version: 1",
                "spaces:",
                "  bubbles:",
                "    agent: brain-bubbles",
                "    enabled: true",
                "    events:",
                "      bubble.create:",
                "        tool: bubble_create",
                *execution_lines,
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "core.capability_targets._space_registry_path", lambda: registry
    )
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"bubble.create": "brain-bubbles"}),
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_hop(), {})

    assert result.ok is False
    assert "deterministic MCP" in (result.error or "")
    assert built_targets == []


def test_deterministic_bubble_create_preserves_exact_mcp_target(monkeypatch):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"bubble.create": "brain-bubbles"}),
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    target = "mcp:brain-bubbles:spaces-ideas:bubble_create"
    result = PlanExecutor()._exec_hop(_hop(target=target), {})

    assert result.ok is True
    assert built_targets == [target]


@pytest.mark.parametrize(
    "target",
    [
        "direct:test:run",
        "supabase:bubble.create",
        "openfang:brain-bubbles",
        "n8n-mcp:n8n.status",
        "coding-engine:GET:/api/status",
    ],
)
def test_deterministic_bubble_create_replaces_every_noncanonical_target(
    monkeypatch, target
):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"bubble.create": "brain-bubbles"}),
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda candidate: built_targets.append(candidate) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_hop(target=target), {})

    assert result.ok is True
    assert built_targets == [
        "mcp:brain-bubbles:spaces-ideas:bubble_create"
    ]


def test_unregistered_capability_keeps_its_existing_target(monkeypatch):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry", lambda: _Registry()
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(
        _hop(capability="custom_unregistered", target="direct:test:run"), {}
    )

    assert result.ok is True
    assert built_targets == ["direct:test:run"]


def test_registered_minibook_event_preserves_structured_direct_target(monkeypatch):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"minibook.status": "brain-knowledge"}),
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    target = "direct:spaces.minibook.tools.minibook_tools:status"
    result = PlanExecutor()._exec_hop(
        _hop(capability="minibook.status", target=target), {}
    )

    assert result.ok is True
    assert built_targets == [target]


def test_registered_space_registry_load_failure_is_fail_closed(monkeypatch):
    built_targets = []
    _disable_kg_hits(monkeypatch)

    def fail_registry_load():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr("core.agent_yaml_registry.get_registry", fail_registry_load)
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor(
            {"ok": False, "error": "OpenFangUnavailable: gateway down"}
        ),
    )

    result = PlanExecutor()._exec_hop(_hop(), {})

    assert result.ok is False
    assert built_targets == [
        "mcp:brain-bubbles:spaces-ideas:bubble_create"
    ]
