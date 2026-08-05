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
        self.calls = 0

    def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
        self.calls += 1
        return self.result


def _hop(*, capability: str = "bubble_create", target: str = "direct:test:run") -> HopSpec:
    return HopSpec(
        step_id="step-1",
        description=capability,
        capability=capability,
        execution_target=target,
    )


def _idea_hop(*, target: str = "supabase:idea.create") -> HopSpec:
    return _hop(capability="idea_create", target=target)


def _write_idea_mcp_registry(tmp_path, *, missing: str | None = None) -> Path:
    lines = ["version: 1", "spaces:", "  ideas:"]
    if missing != "agent":
        lines.append("    agent: brain-ideas")
    lines.extend(
        [
            "    enabled: true",
            "    mcp_servers: [spaces-ideas]",
            "    mcp_tools:",
            "      spaces-ideas: [db_ideas_create]",
            "    events:",
            "      idea.create:",
        ]
    )
    if missing != "tool":
        lines.append("        tool: db_ideas_create")
    if missing != "approval_ref":
        lines.append("        approval_ref: approval:test")
    if missing != "cost_ref":
        lines.append("        cost_ref: cost:test")
    lines.extend(["        execution:", "          kind: mcp"])
    if missing != "server":
        lines.append("          server: spaces-ideas")
    registry = tmp_path / "space_agent_registry.yml"
    registry.write_text("\n".join(lines), encoding="utf-8")
    return registry


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


def test_deterministic_bubble_create_failure_is_never_retried_by_plan(monkeypatch):
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"bubble.create": "brain-bubbles"}),
    )
    executor = _Executor(
        {"ok": False, "error": "uncertain outcome after MCP mutation attempt"}
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor", lambda target: executor
    )
    hop = _hop()
    hop.retries = 3

    result = PlanExecutor()._exec_hop(hop, {})

    assert result.ok is False
    assert executor.calls == 1


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


def test_registered_minibook_status_event_uses_structured_mcp_target(monkeypatch):
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

    requested_target = "direct:spaces.minibook.tools.minibook_tools:status"
    expected_target = "mcp:brain-knowledge:spaces-minibook:minibook_status"
    result = PlanExecutor()._exec_hop(
        _hop(capability="minibook.status", target=requested_target), {}
    )

    assert result.ok is True
    assert built_targets == [expected_target]


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


def test_deterministic_idea_create_overrides_legacy_supabase_target(monkeypatch, tmp_path):
    """A registry-declared MCP event wins over its legacy direct target."""
    built_targets = []
    _disable_kg_hits(monkeypatch)
    registry = _write_idea_mcp_registry(tmp_path)
    monkeypatch.setattr(
        "core.capability_targets._space_registry_path", lambda: registry
    )
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"idea.create": "brain-ideas"}),
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_idea_hop(), {})

    assert result.ok is True
    assert built_targets == ["mcp:brain-ideas:spaces-ideas:db_ideas_create"]


@pytest.mark.parametrize("missing", ["agent", "server", "tool", "approval_ref", "cost_ref"])
def test_deterministic_idea_create_missing_mcp_identity_fails_closed(
    monkeypatch, tmp_path, missing
):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    registry = _write_idea_mcp_registry(tmp_path, missing=missing)
    monkeypatch.setattr(
        "core.capability_targets._space_registry_path", lambda: registry
    )
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"idea.create": "brain-ideas"}),
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_idea_hop(), {})

    assert result.ok is False
    assert "canonical deterministic MCP routing" in (result.error or "")
    assert missing in (result.error or "")
    assert built_targets == []


def test_deterministic_mcp_server_outside_agent_scope_fails_before_executor(
    monkeypatch, tmp_path
):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    registry = _write_idea_mcp_registry(tmp_path)
    registry.write_text(
        registry.read_text(encoding="utf-8").replace(
            "    mcp_servers: [spaces-ideas]\n", "    mcp_servers: [vibemind-db]\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.capability_targets._space_registry_path", lambda: registry)
    monkeypatch.setattr("core.plan_executor._canonical_space_event_agent", lambda _: "brain-ideas")
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_idea_hop(), {})

    assert result.ok is False
    assert "mcp_servers" in (result.error or "")
    assert built_targets == []


def test_deterministic_unknown_mcp_tool_fails_before_executor(monkeypatch, tmp_path):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    registry = _write_idea_mcp_registry(tmp_path)
    registry.write_text(
        registry.read_text(encoding="utf-8").replace(
            "        tool: db_ideas_create\n", "        tool: unknown_tool\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.capability_targets._space_registry_path", lambda: registry)
    monkeypatch.setattr("core.plan_executor._canonical_space_event_agent", lambda _: "brain-ideas")
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_idea_hop(), {})

    assert result.ok is False
    assert "mcp_tools" in (result.error or "")
    assert built_targets == []


def test_deterministic_mcp_agent_scope_drift_fails_before_executor(
    monkeypatch, tmp_path
):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    registry = _write_idea_mcp_registry(tmp_path)
    monkeypatch.setattr("core.capability_targets._space_registry_path", lambda: registry)
    monkeypatch.setattr("core.plan_executor._canonical_space_event_agent", lambda _: "brain-other")
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_idea_hop(), {})

    assert result.ok is False
    assert "agent scope drift" in (result.error or "")
    assert built_targets == []


def test_cognitive_idea_create_keeps_the_openfang_chat_agent(monkeypatch):
    """An event without execution.kind=mcp remains a cognitive agent route."""
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"idea.create": "brain-ideas"}),
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_idea_hop(), {})

    assert result.ok is True
    assert built_targets == ["openfang:brain-ideas"]


def test_unregistered_direct_hop_does_not_require_the_mcp_registry(
    monkeypatch, tmp_path
):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    missing_registry = tmp_path / "missing-space-agent-registry.yml"
    monkeypatch.setattr(
        "core.capability_targets._space_registry_path", lambda: missing_registry
    )
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


def test_deterministic_idea_create_keeps_configured_plan_retries(
    monkeypatch, tmp_path
):
    _disable_kg_hits(monkeypatch)
    registry = _write_idea_mcp_registry(tmp_path)
    monkeypatch.setattr(
        "core.capability_targets._space_registry_path", lambda: registry
    )
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"idea.create": "brain-ideas"}),
    )
    executor = _Executor({"ok": False, "error": "transient deterministic failure"})
    monkeypatch.setattr(
        "core.capability_targets.build_executor", lambda target: executor
    )
    hop = _idea_hop()
    hop.retries = 3

    result = PlanExecutor()._exec_hop(hop, {})

    assert result.ok is False
    assert executor.calls == 3
