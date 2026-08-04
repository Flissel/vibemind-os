"""Offline contract for the durable Ideas edge MCP route."""

from __future__ import annotations

from pathlib import Path
import json
import sys

import yaml


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core.capability_targets import resolve_registry_execution_target
from core.capability_router import CapabilityMatch
from core.capability_validator import CapabilityValidator
from core.discourse_engine import DiscourseEngine
from core.plan_executor import PlanExecutor
from core.plan_schema import HopSpec
from core import world_observer


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []

    def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
        self.calls.append((arg, arg_kwarg, extra_params))
        return {"ok": True, "result": {}}

    def is_resolvable(self) -> bool:
        return True


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


def test_ideas_bridge_and_capability_have_no_legacy_title_or_supabase_contract() -> None:
    bridge = yaml.safe_load((ROOT / "bridge" / "config" / "space_agent_map.yaml").read_text(encoding="utf-8"))
    capabilities = yaml.safe_load((ROOT / "brain" / "the_brain" / "data" / "capabilities.yaml").read_text(encoding="utf-8"))
    capability = next(entry for entry in capabilities if entry["capability"] == "idea_connect")

    assert bridge["mappings"]["ideas"] == "brain-ideas"
    assert capability["execution_target"] == "mcp:brain-ideas:spaces-ideas:idea_connect"
    assert "arg_kwarg" not in capability
    assert capability["validator"]["kind"] == "truth:supabase_edge_ids"
    assert set(capability["validator"]["postcondition"]) == {"check", "expect"}


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


def test_plan_idea_connect_rejects_legacy_or_extra_arguments_before_build(monkeypatch) -> None:
    built_targets: list[str] = []
    monkeypatch.setattr(
        "core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: []
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor", lambda target: built_targets.append(target)
    )
    hop = _idea_connect_hop("openfang:brain-ideas")
    hop.arg_template = json.dumps({"idea1": "A", "idea2": "B"})

    result = PlanExecutor()._exec_hop(hop, {})

    assert result.ok is False
    assert result.error == "clarification required: only from_id, to_id, and edge_type are accepted"
    assert built_targets == []


def _capability_match() -> CapabilityMatch:
    return CapabilityMatch(
        capability="idea_connect",
        description="durable edge",
        primary_names=[],
        supporting_names=[],
        matched_pattern="test",
        execution_target="supabase:idea.connect",
        arg_kwarg="idea1",
    )


def _discourse_with_executor(executor: _Executor) -> DiscourseEngine:
    engine = DiscourseEngine.__new__(DiscourseEngine)
    engine.stats = {"intent_ticks": 0}
    engine._validator = None
    engine._intent_decisions = []
    engine._get_executor = lambda target: executor
    engine._fallback_to_broadcast = lambda *args: (_ for _ in ()).throw(AssertionError("broadcast"))
    return engine


def test_discourse_idea_connect_uses_registry_mcp_and_id_only_payload() -> None:
    executor = _Executor()
    engine = _discourse_with_executor(executor)

    record = engine._handle_direct_capability(
        _capability_match(),
        json.dumps({"from_id": "idea-1", "to_id": "idea-2"}),
        "",
    )

    assert record["ok"] is True
    assert record["direct_target"] == "mcp:brain-ideas:spaces-ideas:idea_connect"
    assert executor.calls == [({"from_id": "idea-1", "to_id": "idea-2"}, None, None)]


def test_discourse_idea_connect_missing_ids_blocks_without_executor_or_broadcast() -> None:
    executor = _Executor()
    engine = _discourse_with_executor(executor)

    record = engine._handle_direct_capability(_capability_match(), "connect Alpha and Beta", "")

    assert record["ok"] is False
    assert record["clarification_required"] is True
    assert executor.calls == []


def _mcp_receipt() -> dict:
    return {
        "isError": False,
        "content": [{
            "type": "text",
            "text": json.dumps({
                "edge_id": "edge-1", "from_id": "idea-1", "to_id": "idea-2", "edge_type": "related",
            }),
        }],
    }


def test_id_truth_validator_re_reads_exact_mcp_receipt_with_explicit_service_config(monkeypatch) -> None:
    calls: list[dict] = []

    class _Response:
        status_code = 200
        def json(self):
            return [{"id": "edge-1", "from_node_id": "idea-1", "to_node_id": "idea-2", "edge_type": "related"}]

    def get(url, *, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return _Response()

    import requests
    monkeypatch.setattr(world_observer, "GROUND_TRUTH_ENABLED", True)
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.example")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    monkeypatch.setattr(requests, "get", get)

    result = CapabilityValidator().validate(
        {"kind": "truth:supabase_edge_ids", "on_fail": "block", "postcondition": {"check": "supabase_edge_ids", "expect": "present"}},
        intent="connect", arg="", raw_result=_mcp_receipt(),
    )

    assert result["valid"] is True
    assert result["verified"] is True
    assert calls[0]["url"] == "https://supabase.example/rest/v1/canvas_edges"
    assert calls[0]["params"] == {"select": "id,from_node_id,to_node_id,edge_type", "id": "eq.edge-1", "limit": "2"}
    assert "title" not in str(calls)


def test_id_truth_observer_has_no_lan_or_anon_default(monkeypatch) -> None:
    monkeypatch.setattr(world_observer, "GROUND_TRUTH_ENABLED", True)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    observed = world_observer.observe({
        "check": "supabase_edge_ids", "edge_id": "edge-1", "from_id": "idea-1", "to_id": "idea-2", "edge_type": "related",
    })

    assert observed.verdict == world_observer.UNVERIFIED
    assert observed.reason == "SUPABASE_URL is required"


def test_id_truth_validator_rejects_malformed_mcp_envelope_before_observer(monkeypatch) -> None:
    monkeypatch.setattr(world_observer, "observe", lambda *_: (_ for _ in ()).throw(AssertionError("observer")))

    result = CapabilityValidator().validate(
        {"kind": "truth:supabase_edge_ids", "on_fail": "block", "postcondition": {"check": "supabase_edge_ids", "expect": "present"}},
        intent="connect", arg="", raw_result={"isError": False, "content": []},
    )

    assert result["valid"] is False
    assert result["verified"] is None
    assert result["reason"] == "ground-truth UNVERIFIED: MCP result receipt is missing"
