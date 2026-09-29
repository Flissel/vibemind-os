"""Offline contract for the durable Ideas edge MCP route."""

from __future__ import annotations

from pathlib import Path
import copy
import json
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core.capability_targets import McpExecutor, resolve_registry_execution_target
from core.capability_router import CapabilityMatch
from core.capability_validator import CapabilityValidator
from core.discourse_engine import DiscourseEngine
from core.idea_connect_contract import (
    canonical_idea_connect_arguments,
    extract_idea_connect_mcp_receipt,
)
from core.plan_executor import PlanExecutor
from core.plan_schema import HopSpec
from core import world_observer


CANONICAL_VALIDATOR = {
    "kind": "truth:supabase_edge_ids",
    "on_fail": "block",
    "require_verified": True,
    "postcondition": {"check": "supabase_edge_ids", "expect": "present"},
}
CANONICAL_IDEA_CONNECT_TARGET = "mcp:brain-ideas:spaces-ideas:idea_connect"
SEMANTIC_IDEA_CONNECT_ALIAS = "mcp: BRAIN-IDEAS : spaces_ideas : idea-connect "


class _Executor:
    def __init__(self, result: object | None = None) -> None:
        self.calls: list[tuple[object, object, object]] = []
        self.result = {} if result is None else result

    def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
        self.calls.append((arg, arg_kwarg, extra_params))
        return {"ok": True, "result": self.result}

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


class _RecordingValidator:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict] = []
        self.raises = raises

    def validate(self, config, **_kwargs):
        self.calls.append(copy.deepcopy(config))
        config["kind"] = "poisoned"
        if self.raises:
            raise RuntimeError("sensitive validator failure")
        return {
            "valid": True,
            "kind": "truth:supabase_edge_ids",
            "on_fail": "block",
            "verified": True,
        }


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


def test_semantic_ideas_alias_has_same_mcp_tool_identity() -> None:
    canonical = McpExecutor(CANONICAL_IDEA_CONNECT_TARGET)
    alias = McpExecutor(SEMANTIC_IDEA_CONNECT_ALIAS)

    assert alias.agent_name.lower() == canonical.agent_name.lower()
    assert alias.namespaced_tool == canonical.namespaced_tool


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
    executor = _Executor(_mcp_receipt())
    validator = _RecordingValidator()
    monkeypatch.setattr(
        "core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: []
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or executor,
    )

    result = PlanExecutor(validator=validator)._exec_hop(
        _idea_connect_hop("supabase:idea.connect"), {}
    )

    assert result.ok is True
    assert built_targets == ["mcp:brain-ideas:spaces-ideas:idea_connect"]
    assert executor.calls == [
        ({"from_id": "idea-1", "to_id": "idea-2", "edge_type": "related"}, None, None)
    ]
    assert validator.calls == [CANONICAL_VALIDATOR]


def test_plan_idea_connect_requires_validator_before_executor_build(monkeypatch) -> None:
    built_targets: list[str] = []
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr(
        "core.capability_targets.build_executor", lambda target: built_targets.append(target)
    )

    result = PlanExecutor()._exec_hop(_idea_connect_hop("supabase:idea.connect"), {})

    assert result.ok is False
    assert result.error == "canonical idea.connect validator unavailable"
    assert built_targets == []


def test_plan_idea_connect_overrides_stale_validator_with_fresh_canonical_copy(monkeypatch) -> None:
    executor = _Executor(_mcp_receipt())
    validator = _RecordingValidator()
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr("core.capability_targets.build_executor", lambda _target: executor)

    for _ in range(2):
        hop = _idea_connect_hop("direct:legacy")
        hop.validator = {"kind": "rule:string_nonempty", "on_fail": "report"}
        assert PlanExecutor(validator=validator)._exec_hop(hop, {}).ok is True

    assert validator.calls == [CANONICAL_VALIDATOR, CANONICAL_VALIDATOR]


def test_plan_idea_connect_validator_exception_blocks_result(monkeypatch) -> None:
    executor = _Executor(_mcp_receipt())
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr("core.capability_targets.build_executor", lambda _target: executor)

    result = PlanExecutor(validator=_RecordingValidator(raises=True))._exec_hop(
        _idea_connect_hop("direct:legacy"), {}
    )

    assert result.ok is False
    assert "validator" in (result.error or "")


def test_plan_idea_connect_validator_exception_is_unverified_not_failed(monkeypatch) -> None:
    """T2 Task 0b, Punkt C: ein abgestuerzter Pruefer ist kein verifiziertes
    Scheitern. Unter strict MCP bleibt die Blockade (ok=False, Policy), aber
    das Exception-Verdict muss "verified": None tragen - sonst zaehlt
    decision_outcome.wirksamer_befund den Hop als verified_failure statt als
    unverifizierten Pruefer-Ausfall."""
    executor = _Executor(_mcp_receipt())
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr("core.capability_targets.build_executor", lambda _target: executor)

    result = PlanExecutor(validator=_RecordingValidator(raises=True))._exec_hop(
        _idea_connect_hop("direct:legacy"), {}
    )

    assert result.ok is False
    assert result.contract_pass is False  # Policy-Blockade (strict MCP) bleibt
    assert result.validator_verdict is not None
    assert "verified" in result.validator_verdict
    assert result.validator_verdict["verified"] is None

    from core.decision_outcome import wirksamer_befund
    assert wirksamer_befund(result) is None


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


@pytest.mark.parametrize(
    ("resolved_by_router", "target"),
    [
        (False, CANONICAL_IDEA_CONNECT_TARGET),
        (True, CANONICAL_IDEA_CONNECT_TARGET),
        (False, SEMANTIC_IDEA_CONNECT_ALIAS),
        (True, SEMANTIC_IDEA_CONNECT_ALIAS),
    ],
)
def test_plan_rejects_canonical_ideas_target_for_non_idea_capability_before_build(
    monkeypatch, resolved_by_router, target
) -> None:
    built_targets: list[str] = []

    class _Router:
        def get_capability(self, _capability):
            return {"execution_target": target}

    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr(
        "core.capability_targets.build_executor", lambda target: built_targets.append(target)
    )
    hop = HopSpec(
        step_id="hostile-1",
        description="attempt canonical Ideas tool confusion",
        capability="custom_edge_alias",
        execution_target=None if resolved_by_router else target,
        arg_template=json.dumps({"from_id": "idea-1", "to_id": "idea-2"}),
    )

    result = PlanExecutor(capability_router=_Router())._exec_hop(hop, {})

    assert result.ok is False
    assert result.error == "canonical Ideas MCP target is bound to idea.connect"
    assert built_targets == []


def _capability_match(validator: dict | None = None) -> CapabilityMatch:
    return CapabilityMatch(
        capability="idea_connect",
        description="durable edge",
        primary_names=[],
        supporting_names=[],
        matched_pattern="test",
        execution_target="supabase:idea.connect",
        arg_kwarg="idea1",
        validator=validator,
    )


def _discourse_with_executor(
    executor: _Executor, validator: object | None = None
) -> DiscourseEngine:
    engine = DiscourseEngine.__new__(DiscourseEngine)
    engine.stats = {"intent_ticks": 0}
    engine._validator = validator
    engine._intent_decisions = []
    engine._get_executor = lambda target: executor
    engine._fallback_to_broadcast = lambda *args: (_ for _ in ()).throw(AssertionError("broadcast"))
    return engine


def test_discourse_idea_connect_uses_registry_mcp_and_id_only_payload() -> None:
    executor = _Executor(_mcp_receipt())
    validator = _RecordingValidator()
    engine = _discourse_with_executor(executor, validator)

    record = engine._handle_direct_capability(
        _capability_match(),
        json.dumps({"from_id": "idea-1", "to_id": "idea-2"}),
        "",
    )

    assert record["ok"] is True
    assert record["direct_target"] == "mcp:brain-ideas:spaces-ideas:idea_connect"
    assert executor.calls == [({"from_id": "idea-1", "to_id": "idea-2"}, None, None)]
    assert validator.calls == [CANONICAL_VALIDATOR]


def test_discourse_idea_connect_requires_validator_before_executor_lookup() -> None:
    executor = _Executor(_mcp_receipt())
    engine = _discourse_with_executor(executor)
    engine._get_executor = lambda _target: (_ for _ in ()).throw(AssertionError("executor lookup"))

    record = engine._handle_direct_capability(
        _capability_match(), json.dumps({"from_id": "idea-1", "to_id": "idea-2"}), ""
    )

    assert record["ok"] is False
    assert record["direct_error"] == "canonical idea.connect validator unavailable"
    assert executor.calls == []


def test_discourse_idea_connect_overrides_stale_validator_and_blocks_exception() -> None:
    executor = _Executor(_mcp_receipt())
    validator = _RecordingValidator(raises=True)
    engine = _discourse_with_executor(executor, validator)

    record = engine._handle_direct_capability(
        _capability_match({"kind": "rule:string_nonempty", "on_fail": "report"}),
        json.dumps({"from_id": "idea-1", "to_id": "idea-2"}),
        "",
    )

    assert record["ok"] is False
    assert record["blocked_by_validator"] is True
    assert validator.calls == [CANONICAL_VALIDATOR]
    assert record["validation"]["verified"] is None
    assert "sensitive validator failure" not in str(record)


@pytest.mark.parametrize(
    "target", [CANONICAL_IDEA_CONNECT_TARGET, SEMANTIC_IDEA_CONNECT_ALIAS]
)
def test_discourse_rejects_canonical_ideas_target_for_non_idea_capability(target) -> None:
    executor = _Executor(_mcp_receipt())
    engine = _discourse_with_executor(executor, _RecordingValidator())
    engine._get_executor = lambda _target: (_ for _ in ()).throw(AssertionError("executor lookup"))
    cap_match = CapabilityMatch(
        capability="custom_edge_alias",
        description="hostile alias",
        primary_names=[],
        supporting_names=[],
        matched_pattern="test",
        execution_target=target,
    )

    record = engine._handle_direct_capability(cap_match, "hostile", "")

    assert record["ok"] is False
    assert record["direct_error"] == "canonical Ideas MCP target is bound to idea.connect"
    assert executor.calls == []


@pytest.mark.parametrize(
    ("verified", "reason", "signal"),
    [
        (False, "durable edge receipt refuted", {"edge_id": "edge-1", "rows_found": 1}),
        (None, "durable edge read-back unavailable", {"status": "unverified"}),
    ],
)
def test_discourse_preserves_canonical_truth_failure_envelope(
    verified, reason, signal
) -> None:
    class _VerdictValidator:
        def validate(self, _config, **_kwargs):
            return {
                "valid": False,
                "verified": verified,
                "verify_signal": signal,
                "reason": reason,
                "kind": "truth:supabase_edge_ids",
                "on_fail": "report",
                "elapsed_s": 0.25,
            }

    engine = _discourse_with_executor(_Executor(_mcp_receipt()), _VerdictValidator())
    record = engine._handle_direct_capability(
        _capability_match(),
        json.dumps({"from_id": "idea-1", "to_id": "idea-2"}),
        "",
    )

    assert record["ok"] is False
    assert record["blocked_by_validator"] is True
    assert record["validation"] == {
        "valid": False,
        "verified": verified,
        "verify_signal": signal,
        "reason": reason,
        "kind": "truth:supabase_edge_ids",
        "on_fail": "block",
        "elapsed_s": 0.25,
    }


def test_discourse_idea_connect_missing_ids_blocks_without_executor_or_broadcast() -> None:
    executor = _Executor()
    engine = _discourse_with_executor(executor)

    record = engine._handle_direct_capability(_capability_match(), "connect Alpha and Beta", "")

    assert record["ok"] is False
    assert record["clarification_required"] is True
    assert executor.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"from_id": " idea-1", "to_id": "idea-2"},
        {"from_id": "idea-1 ", "to_id": "idea-2"},
        {"from_id": "idea.1", "to_id": "idea-2"},
        {"from_id": "idea-1", "to_id": "idea-2", "edge_type": "related filter"},
        {"from_id": "a" * 129, "to_id": "idea-2"},
        {"from_id": "idea-1", "to_id": "idea-1"},
    ],
)
def test_idea_connect_arguments_match_exact_mcp_grammar(arguments) -> None:
    parsed, _reason = canonical_idea_connect_arguments(arguments)
    assert parsed is None


def test_idea_connect_receipt_uses_exact_mcp_grammar() -> None:
    receipt = _mcp_receipt()
    receipt["content"][0]["text"] = json.dumps({
        "edge_id": " edge-1",
        "from_id": "idea-1",
        "to_id": "idea-2",
        "edge_type": "related",
    })

    parsed, _reason = extract_idea_connect_mcp_receipt(receipt)

    assert parsed is None


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

    def get(url, *, params, headers, timeout, allow_redirects):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout, "allow_redirects": allow_redirects})
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
    assert calls[0]["allow_redirects"] is False
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


@pytest.mark.parametrize(
    "url",
    [
        "ftp://supabase.example",
        "https:///missing-host",
        "https://user:pass@supabase.example",
        "https://supabase.example?secret=value",
        "https://supabase.example/#fragment",
    ],
)
def test_id_truth_observer_rejects_unsafe_supabase_urls(monkeypatch, url) -> None:
    import requests
    monkeypatch.setattr(world_observer, "GROUND_TRUTH_ENABLED", True)
    monkeypatch.setenv("SUPABASE_URL", url)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("request")))

    observed = world_observer.observe({
        "check": "supabase_edge_ids", "edge_id": "edge-1", "from_id": "idea-1", "to_id": "idea-2", "edge_type": "related",
    })

    assert observed.verdict == world_observer.UNVERIFIED
    assert observed.reason == "SUPABASE_URL is invalid"
    assert url not in str(observed)


def test_id_truth_observer_does_not_follow_or_leak_redirect(monkeypatch) -> None:
    calls: list[bool] = []

    class _Redirect:
        status_code = 302
        headers = {"Location": "https://secret.example/?token=should-not-leak"}

    def get(_url, **kwargs):
        calls.append(kwargs["allow_redirects"])
        return _Redirect()

    import requests
    monkeypatch.setattr(world_observer, "GROUND_TRUTH_ENABLED", True)
    monkeypatch.setenv("SUPABASE_URL", "http://192.168.178.65:54321")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    monkeypatch.setattr(requests, "get", get)

    observed = world_observer.observe({
        "check": "supabase_edge_ids", "edge_id": "edge-1", "from_id": "idea-1", "to_id": "idea-2", "edge_type": "related",
    })

    assert calls == [False]
    assert observed.verdict == world_observer.UNVERIFIED
    assert observed.reason == "durable edge read-back unavailable"
    assert "should-not-leak" not in str(observed)


def test_id_truth_validator_rejects_malformed_mcp_envelope_before_observer(monkeypatch) -> None:
    monkeypatch.setattr(world_observer, "observe", lambda *_: (_ for _ in ()).throw(AssertionError("observer")))

    result = CapabilityValidator().validate(
        {"kind": "truth:supabase_edge_ids", "on_fail": "block", "postcondition": {"check": "supabase_edge_ids", "expect": "present"}},
        intent="connect", arg="", raw_result={"isError": False, "content": []},
    )

    assert result["valid"] is False
    assert result["verified"] is None
    assert result["reason"] == "ground-truth UNVERIFIED: MCP result receipt is missing"
