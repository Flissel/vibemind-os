"""Offline contract for the durable Ideas edge MCP route."""

from __future__ import annotations

from pathlib import Path
import json
import sys

import yaml


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core.capability_targets import resolve_registry_execution_target
from core.plan_executor import PlanExecutor
from core.plan_schema import HopSpec


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []

    def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
        self.calls.append((arg, arg_kwarg, extra_params))
        return {"ok": True, "result": {}}


def _idea_connect_hop(target: str) -> HopSpec:
    return HopSpec(
        step_id="edge-1",
        description="connect durable idea IDs",
        capability="idea_connect",
        execution_target=target,
        arg_template=json.dumps(
            {"from_id": "idea-1", "to_id": "idea-2", "edge_type": "related"}
        ),
    )


def test_idea_connect_registry_declares_exact_canonical_mcp_target() -> None:
    registry_path = ROOT / "config" / "space_agent_registry.yml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    spec = registry["spaces"]["ideas"]["events"]["idea.connect"]

    assert spec["tool"] == "idea_connect"
    assert spec["required_params"] == ["from_id", "to_id"]
    assert spec["execution"] == {"kind": "mcp", "server": "spaces-ideas"}
    assert resolve_registry_execution_target("idea.connect") == (
        "mcp:brain-ideas:spaces-ideas:idea_connect"
    )


def test_plan_idea_connect_replaces_legacy_target_and_sends_only_durable_ids(monkeypatch) -> None:
    built_targets: list[str] = []
    executor = _Executor()
    monkeypatch.setattr(
        "core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: []
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or executor,
    )

    result = PlanExecutor()._exec_hop(
        _idea_connect_hop("supabase:idea.connect"), {}
    )

    assert result.ok is True
    assert built_targets == ["mcp:brain-ideas:spaces-ideas:idea_connect"]
    assert executor.calls == [
        ({"from_id": "idea-1", "to_id": "idea-2", "edge_type": "related"}, None, None)
    ]
