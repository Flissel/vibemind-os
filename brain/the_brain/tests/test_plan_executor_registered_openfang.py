"""Fail-closed routing for canonical Space events through OpenFang."""

from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core.plan_executor import PlanExecutor
from core.openfang_runtime_authority import runtime_invocation_id
from core.plan_schema import HopResult, HopSpec, Plan, validate_plan


class _Registry:
    def __init__(self, agents: dict[str, str] | None = None) -> None:
        self.agents = agents or {}

    def get_event_agent(self, event_id: str) -> str | None:
        return self.agents.get(event_id)


class _Executor:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = 0
        self.extra_params = None

    def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
        self.calls += 1
        self.extra_params = extra_params
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
    if missing != "required_provenance":
        lines.append("        required_provenance: [approval_ref, cost_ref]")
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


def _handoff_bundle() -> dict[str, object]:
    """Opaque, already-admitted bundle transported unchanged to Shared."""
    return {
        "channel_intent": {"correlation_id": "event_v1_01J8Q3Z4R5T6V7W8X9Y0ABCDEF"},
        "brain_plan": {"plan_id": "plan_v1_01J8Q3Z4R5T6V7W8X9Y0ABCDEF"},
        "space_execution_contracts": [{"space_id": "research"}],
        "lifecycle": {"status": "execution_deferred", "revision": 3},
        "handoff": {
            "execution_mode": "cognitive",
            "execution_boundary": "openfang",
            "approval_ref": "approval:opaque-openfang-boundary",
            "cost_ref": "cost:opaque-openfang-boundary",
        },
    }


def _schema_valid_handoff_bundle() -> dict[str, object]:
    channel_intent = {
        "contract_version": "v1",
        "correlation_id": "event_v1_01J8Q3Z4R5T6V7W8X9Y0ABCDEF",
        "channel_kind": "desktop-chat",
        "actor_context": {"actor_id": "user:local-42", "locale": "de-DE"},
        "session_context": {
            "session_id": "session-20260731-01",
            "conversation_id": "desktop-chat-42",
        },
        "message": "Bitte fasse die Projektlage zusammen.",
        "received_at": "2026-07-31T10:00:00Z",
        "requested_space_id": "research",
        "user_facing_expectations": {"reply": "required", "evidence": "summary"},
    }
    brain_plan = {
        "contract_version": "v1",
        "plan_id": "plan_v1_01J8Q3Z4R5T6V7W8X9Y0ABCDEF",
        "intent": {
            "summary": "Prepare a verified cross-space research brief.",
            "context": ["The source material is already available offline."],
            "requested_by": "product-owner",
        },
        "participating_spaces": [
            {
                "space_id": "research",
                "roles": [{"role": "researcher", "required_agent_count": 1}],
            },
            {
                "space_id": "coding",
                "roles": [{"role": "developer", "required_agent_count": 1}],
            },
        ],
        "tasks": [
            {
                "node_id": "research-sources",
                "order": 1,
                "summary": "Extract source findings.",
                "space_ids": ["research"],
                "depends_on": [],
                "success_criteria": ["Findings are traceable."],
                "evidence_requirements": [
                    {
                        "evidence_type": "artifact",
                        "description": "A source finding artifact is available.",
                    }
                ],
            },
            {
                "node_id": "write-brief",
                "order": 2,
                "summary": "Create the brief from the findings.",
                "space_ids": ["coding"],
                "depends_on": ["research-sources"],
                "success_criteria": ["The brief answers the stated intent."],
                "evidence_requirements": [
                    {
                        "evidence_type": "artifact",
                        "description": "A reviewable brief artifact is available.",
                    }
                ],
            },
        ],
    }
    space_execution_contracts = [
        {
            "contract_version": "v1",
            "contract_id": (
                "space_execution_contract_v1_01J8Q3Z4R5T6V7W8X9Y0ABCDEF"
            ),
            "space_id": space_id,
            "executor_id": "executor:brain-orchestrator",
            "approval_policy_ref": "approval-policy:standard",
            "cost_policy_ref": "cost-policy:bounded",
            "healthcheck_ref": "healthcheck:bubbles-structural",
            "golden_path_ref": "golden-path:bubbles-promote",
        }
        for space_id in ("research", "coding")
    ]
    lifecycle = {
        "contract_version": "v1",
        "correlation_id": channel_intent["correlation_id"],
        "plan_id": brain_plan["plan_id"],
        "participating_space_ids": ["research", "coding"],
        "revision": 3,
        "status": "execution_deferred",
        "updated_at": "2026-08-01T10:02:00Z",
        "event_history": [
            {
                "revision": 1,
                "status": "planned",
                "occurred_at": "2026-08-01T10:00:00Z",
            },
            {
                "revision": 2,
                "status": "admitted",
                "reason_code": "admission-validated",
                "occurred_at": "2026-08-01T10:01:00Z",
            },
            {
                "revision": 3,
                "status": "execution_deferred",
                "reason_code": "execution-engine-deferred",
                "occurred_at": "2026-08-01T10:02:00Z",
            },
        ],
    }
    handoff = {
        "contract_version": "v1",
        "execution_mode": "cognitive",
        "execution_boundary": "openfang",
        "correlation_id": channel_intent["correlation_id"],
        "plan_id": brain_plan["plan_id"],
        "lifecycle_revision": 3,
        "brain_task_node_id": "research-sources",
        "space_id": "research",
        "roles": [{"role": "researcher", "required_agent_count": 1}],
        "approval_ref": "approval:opaque-openfang-boundary",
        "cost_ref": "cost:opaque-openfang-boundary",
        "retry": {"classification": "transient", "max_attempts": 3},
    }
    return {
        "channel_intent": channel_intent,
        "brain_plan": brain_plan,
        "space_execution_contracts": space_execution_contracts,
        "lifecycle": lifecycle,
        "handoff": handoff,
    }


def _install_handoff_validator(monkeypatch, validator) -> None:
    shared = ModuleType("vibemind_shared")
    contracts = ModuleType("vibemind_shared.contracts")
    contracts.validate_brain_openfang_handoff_bundle = validator
    shared.contracts = contracts
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    monkeypatch.setitem(sys.modules, "vibemind_shared.contracts", contracts)


def _openfang_hop() -> HopSpec:
    return _hop(capability="cognitive_unregistered", target="openfang:brain-research")


def test_cognitive_openfang_without_complete_handoff_bundle_fails_before_executor(
    monkeypatch,
):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr("core.agent_yaml_registry.get_registry", lambda: _Registry())
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_openfang_hop(), {})

    assert result.ok is False
    assert "OpenFang handoff admission" in (result.error or "")
    assert built_targets == []


@pytest.mark.parametrize(
    "admission_error",
    (
        "correlation_id continuity drift",
        "plan_id continuity drift",
        "lifecycle_revision continuity drift",
        "brain_task_node_id continuity drift",
        "space_id continuity drift",
        "roles continuity drift",
        "execution_deferred required",
        "execution_mode must be cognitive",
        "execution_boundary must be openfang",
        "approval_ref required",
        "cost_ref required",
    ),
)
def test_cognitive_openfang_shared_admission_rejection_prevents_dispatch(
    monkeypatch, admission_error
):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr("core.agent_yaml_registry.get_registry", lambda: _Registry())

    def reject_bundle(*_args):
        raise ValueError(admission_error)

    _install_handoff_validator(monkeypatch, reject_bundle)
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_openfang_hop(), {}, plan_ctx=_handoff_bundle())

    assert result.ok is False
    assert "OpenFang handoff admission rejected: ValueError" == result.error
    assert built_targets == []


def test_cognitive_openfang_missing_shared_package_fails_before_executor(
    monkeypatch,
):
    built_targets = []
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr("core.agent_yaml_registry.get_registry", lambda: _Registry())
    monkeypatch.setitem(sys.modules, "vibemind_shared", None)
    monkeypatch.delitem(sys.modules, "vibemind_shared.contracts", raising=False)
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(
        _openfang_hop(), {}, plan_ctx=_handoff_bundle()
    )

    assert result.ok is False
    assert result.error == "OpenFang handoff admission rejected: ModuleNotFoundError"
    assert built_targets == []


def test_cognitive_openfang_passes_exact_bundle_to_public_shared_api_before_executor(
    monkeypatch,
):
    built_targets = []
    validator_calls = []
    bundle = _handoff_bundle()
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr("core.agent_yaml_registry.get_registry", lambda: _Registry())

    def validate_bundle(*args):
        validator_calls.append(args)

    _install_handoff_validator(monkeypatch, validate_bundle)
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_openfang_hop(), {}, plan_ctx=bundle)

    assert result.ok is True
    assert validator_calls == [
        (
            bundle["channel_intent"],
            bundle["brain_plan"],
            bundle["space_execution_contracts"],
            bundle["lifecycle"],
            bundle["handoff"],
        )
    ]
    assert built_targets == ["openfang:brain-research"]


def test_public_execute_threads_opaque_handoff_bundle_to_cognitive_dispatch(
    monkeypatch,
):
    built_targets = []
    validator_calls = []
    bundle = _schema_valid_handoff_bundle()
    _disable_kg_hits(monkeypatch)
    monkeypatch.setenv("MULTIHOP_EPISODIC_WRITE", "0")
    monkeypatch.setenv("MULTIHOP_INGEST_ENABLED", "0")
    monkeypatch.setattr("core.agent_yaml_registry.get_registry", lambda: _Registry())

    shared_src = ROOT / "shared" / "src"
    assert shared_src.is_dir(), "the pinned Shared package must be materialized"
    monkeypatch.syspath_prepend(str(shared_src))
    for module_name in tuple(sys.modules):
        if module_name == "vibemind_shared" or module_name.startswith(
            "vibemind_shared."
        ):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
    import vibemind_shared.contracts as shared_contracts

    real_validator = shared_contracts.validate_brain_openfang_handoff_bundle

    def validate_bundle(*args):
        validator_calls.append(args)
        return real_validator(*args)

    monkeypatch.setattr(
        shared_contracts,
        "validate_brain_openfang_handoff_bundle",
        validate_bundle,
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    class _Recorder:
        def record(self, _snapshot) -> None:
            return None

    plan = Plan(
        plan_id="plan-public-handoff",
        intent="research",
        rationale="test public handoff transport",
        hops=[_openfang_hop()],
    )

    result = PlanExecutor(recorder=_Recorder()).execute(
        plan,
        openfang_handoff_bundle=bundle,
    )

    assert result["ok"] is True
    assert validator_calls == [
        (
            bundle["channel_intent"],
            bundle["brain_plan"],
            bundle["space_execution_contracts"],
            bundle["lifecycle"],
            bundle["handoff"],
        )
    ]
    assert all(
        argument is bundle[key]
        for argument, key in zip(
            validator_calls[0],
            (
                "channel_intent",
                "brain_plan",
                "space_execution_contracts",
                "lifecycle",
                "handoff",
            ),
            strict=True,
        )
    )
    assert built_targets == ["openfang:brain-research"]

    invalid_bundle = dict(bundle)
    invalid_bundle["handoff"] = dict(bundle["handoff"])
    invalid_bundle["handoff"].pop("approval_ref")
    built_targets.clear()
    validator_calls.clear()

    rejected = PlanExecutor(recorder=_Recorder()).execute(
        plan,
        openfang_handoff_bundle=invalid_bundle,
    )

    assert rejected["ok"] is False
    assert built_targets == []
    assert rejected["executed"]["step-1"]["error"] == (
        "OpenFang handoff admission rejected: ContractValidationError"
    )


def test_deterministic_mcp_bypasses_cognitive_handoff_admission(monkeypatch):
    built_targets = []
    _disable_kg_hits(monkeypatch)

    def unexpected_admission(*_args):
        raise AssertionError("deterministic MCP must not use cognitive admission")

    _install_handoff_validator(monkeypatch, unexpected_admission)
    monkeypatch.setattr("core.agent_yaml_registry.get_registry", lambda: _Registry())
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )
    target = "mcp:brain-bubbles:spaces-ideas:bubble_create"

    result = PlanExecutor()._exec_hop(
        _hop(capability="custom_unregistered", target=target),
        {},
        plan_ctx={"approval_ref": "approval:plan-1", "cost_ref": "cost:plan-1"},
    )

    assert result.ok is True
    assert built_targets == [target]


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


def test_deterministic_mcp_forwards_only_runtime_authority_context(
    monkeypatch,
):
    _disable_kg_hits(monkeypatch)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"bubble.create": "brain-bubbles"}),
    )
    executor = _Executor({"ok": True, "result": {}})
    monkeypatch.setattr("core.capability_targets.build_executor", lambda _: executor)

    result = PlanExecutor()._exec_hop(
        _hop(), {},
        plan_ctx={
            "plan_id": "plan-runtime-authority",
            "plan_revision": 1,
            "trace_id": "trace-runtime-authority",
        },
    )

    assert result.ok is True
    assert set(executor.extra_params) == {"_runtime_authority"}
    context = executor.extra_params["_runtime_authority"]
    assert context.correlation_id == "trace-runtime-authority"
    assert context.plan_id == "plan-runtime-authority"
    assert context.plan_revision == 1
    assert context.step_id == "step-1"


def test_runtime_invocation_identity_is_stable_per_revision_and_changes_after_replan():
    same_hop = runtime_invocation_id("plan-runtime-authority", 1, "step-1")
    repeated_hop = runtime_invocation_id("plan-runtime-authority", 1, "step-1")
    replanned_hop = runtime_invocation_id("plan-runtime-authority", 2, "step-1")

    assert same_hop == repeated_hop
    assert replanned_hop != same_hop


def test_pending_authority_stops_dependents_without_replan(monkeypatch):
    first = HopSpec("first", "first", execution_target="direct:test:first")
    dependent = HopSpec(
        "dependent", "dependent", execution_target="direct:test:dependent", depends_on=["first"],
    )
    plan = Plan("plan-pending", "pending", "", [first, dependent])
    calls = []

    def _pending(self, hop, _state, _context):
        calls.append(hop.step_id)
        return HopResult(
            step_id=hop.step_id, ok=False, pending=True,
            authority_status="pending_approval", invocation_id="brain-mcp-pending",
            error="runtime authority approval pending", contract_pass=None, reward=0.0,
        )

    monkeypatch.setattr(PlanExecutor, "_exec_hop", _pending)
    result = PlanExecutor().execute(plan, replanner=lambda *_args: pytest.fail("pending must not replan"))

    assert calls == ["first"]
    assert result["pending"] is True
    assert result["authority_status"] == "pending_approval"
    assert result["invocation_id"] == "brain-mcp-pending"
    assert result["executed"]["dependent"]["ok"] is False
    assert result["executed"]["first"]["contract_pass"] is None
    assert result["executed"]["first"]["reward"] == 0.0
    assert result["executed"]["dependent"]["contract_pass"] is None
    assert result["executed"]["dependent"]["reward"] == 0.0


def test_terminal_pending_skips_plan_failure_learning_and_recording(monkeypatch):
    from core import decision_recall, decision_self_prior

    calls = {"record": 0, "self_prior": 0, "sequence_record": 0}

    class _Recorder:
        def record(self, _snapshot):
            calls["sequence_record"] += 1

    first = HopSpec(
        "first", "first", capability="pending-capability", execution_target="direct:test:first",
    )
    plan = Plan("plan-pending-terminal", "pending", "", [first])

    def _pending(_self, hop, _state, _context):
        return HopResult(
            step_id=hop.step_id, ok=False, pending=True, capability=hop.capability,
            authority_status="pending_approval", invocation_id="brain-mcp-pending",
            contract_pass=None, reward=0.0,
        )

    monkeypatch.setattr(PlanExecutor, "_exec_hop", _pending)
    monkeypatch.setattr(
        decision_recall, "record",
        lambda *_args, **_kwargs: calls.__setitem__("record", calls["record"] + 1),
    )
    monkeypatch.setattr(
        decision_self_prior, "update",
        lambda *_args, **_kwargs: calls.__setitem__("self_prior", calls["self_prior"] + 1),
    )

    result = PlanExecutor(kg=object(), recorder=_Recorder()).execute(plan)

    assert result["pending"] is True
    assert calls == {"record": 0, "self_prior": 0, "sequence_record": 0}


def test_nonretryable_executor_failure_stops_plan_retry_loop(monkeypatch):
    _disable_kg_hits(monkeypatch)
    executor = _Executor({"ok": False, "error": "outcome_unknown", "retryable": False})
    monkeypatch.setattr("core.capability_targets.build_executor", lambda _target: executor)
    hop = HopSpec("step-1", "nonretryable", execution_target="direct:test:run", retries=3)

    result = PlanExecutor()._exec_hop(hop, {})

    assert result.ok is False
    assert executor.calls == 1
    assert result.retried == 0


def test_pending_executor_result_does_not_replay_within_hop(monkeypatch):
    _disable_kg_hits(monkeypatch)
    executor = _Executor({
        "ok": False, "pending": True, "authority_status": "pending_approval",
        "invocation_id": "brain-mcp-pending",
    })
    monkeypatch.setattr("core.capability_targets.build_executor", lambda _target: executor)
    hop = HopSpec("step-1", "pending", execution_target="direct:test:run", retries=3)

    result = PlanExecutor()._exec_hop(hop, {})

    assert result.pending is True
    assert result.contract_pass is None
    assert result.reward == 0.0
    assert executor.calls == 1


@pytest.mark.parametrize("plan_revision", [0, False, 1.5])
def test_plan_from_dict_preserves_invalid_revision_for_validation(plan_revision):
    plan = Plan.from_dict({
        "plan_id": "plan-invalid-revision", "intent": "revision", "rationale": "",
        "plan_revision": plan_revision,
        "hops": [{"step_id": "s1", "description": "noop", "execution_target": "direct:test:run"}],
    })

    assert "plan_revision must be a positive integer" in validate_plan(plan)


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


@pytest.mark.parametrize("missing", ["agent", "server", "tool", "required_provenance"])
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
    _install_handoff_validator(monkeypatch, lambda *_args: None)
    monkeypatch.setattr(
        "core.agent_yaml_registry.get_registry",
        lambda: _Registry({"idea.create": "brain-ideas"}),
    )
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: built_targets.append(target) or _Executor({"ok": True, "result": {}}),
    )

    result = PlanExecutor()._exec_hop(_idea_hop(), {}, plan_ctx=_handoff_bundle())

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
