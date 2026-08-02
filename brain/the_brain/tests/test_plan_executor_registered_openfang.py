"""Fail-closed routing for canonical Space events through OpenFang."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core.plan_executor import PlanExecutor
from core.plan_schema import HopSpec


class _Registry:
    def __init__(self, agent: str | None) -> None:
        self.agent = agent

    def get_event_agent(self, event_id: str) -> str | None:
        assert event_id == "bubble.create"
        return self.agent


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


def test_registered_event_gateway_failure_never_invokes_direct_executor(monkeypatch):
    """OpenFang outage is surfaced; the previous direct target is never used."""
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry", lambda: _Registry("brain-bubbles")
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor(
            {"ok": False, "error": "OpenFangUnavailable: gateway down"}
        ),
    )

    result = PlanExecutor()._exec_hop(_hop(), {})

    assert result.ok is False
    assert built_targets == ["openfang:brain-bubbles"]
    assert all(not target.startswith("direct:") for target in built_targets)


def test_registered_event_preserves_explicit_mcp_target(monkeypatch):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry", lambda: _Registry("brain-bubbles")
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(
        _hop(target="mcp:brain-bubbles:spaces-ideas:vibemind_bubble_create"), {}
    )

    assert result.ok is True
    assert built_targets == ["mcp:brain-bubbles:spaces-ideas:vibemind_bubble_create"]


def test_unregistered_capability_keeps_its_existing_target(monkeypatch):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry", lambda: _Registry(None)
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
    assert built_targets == ["openfang:brain-bubbles"]
