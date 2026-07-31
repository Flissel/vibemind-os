"""Tests for the provider-neutral Bubbles orchestration contract."""

from __future__ import annotations

import ast
import inspect

import pytest

from core import bubbles_orchestration_contract
from core.bubbles_orchestration_contract import (
    BubblesIntent,
    BubblesOrchestrationContract,
    BubblesRequest,
    ClarificationField,
    ContractState,
    CostReference,
    DependencyHealth,
    EvidenceReference,
    LifecycleOperation,
    OpaqueCorrelation,
    ProjectionStage,
    TransitionRejection,
)


def _request(intent: BubblesIntent, **overrides: object) -> BubblesRequest:
    values: dict[str, object] = {
        "intent": intent,
        "operation_id": "operation:bubbles-1",
        "correlation": OpaqueCorrelation(
            plan_id="plan:bubbles-1",
            interaction_id="interaction:bubbles-1",
            task_id="task:bubbles-1",
        ),
        "bubble_id": "bubble:1",
        "name": "Launch plan",
        "query": "launch",
        "score_context": "confidence and urgency",
        "evaluation_context": "readiness rubric",
        "promote_target": "project:launch",
    }
    values.update(overrides)
    return BubblesRequest(**values)


def test_contract_declares_only_the_canonical_bubbles_intents() -> None:
    assert BubblesOrchestrationContract.CONTRACT_VERSION == "bubbles.orchestration.v1"
    assert BubblesOrchestrationContract.CANONICAL_SPACE_ID == "bubbles"
    assert {intent.value for intent in BubblesIntent} == {
        "create",
        "list",
        "find",
        "enter",
        "exit",
        "update",
        "score",
        "evaluate",
        "promote",
    }
    assert {operation.value for operation in LifecycleOperation} == {
        "plan",
        "status",
        "cancel",
        "resume",
        "result",
    }


@pytest.mark.parametrize(
    ("intent", "field_name", "field", "value"),
    [
        (BubblesIntent.CREATE, "name", ClarificationField.NAME, " \t "),
        (BubblesIntent.FIND, "query", ClarificationField.QUERY, ""),
        (BubblesIntent.ENTER, "bubble_id", ClarificationField.BUBBLE_ID, None),
        (BubblesIntent.EXIT, "bubble_id", ClarificationField.BUBBLE_ID, " "),
        (BubblesIntent.UPDATE, "bubble_id", ClarificationField.BUBBLE_ID, ""),
        (BubblesIntent.SCORE, "score_context", ClarificationField.SCORE_CONTEXT, " "),
        (
            BubblesIntent.EVALUATE,
            "evaluation_context",
            ClarificationField.EVALUATION_CONTEXT,
            "",
        ),
        (BubblesIntent.PROMOTE, "promote_target", ClarificationField.PROMOTE_TARGET, "\t"),
    ],
)
def test_plan_requires_declared_bubbles_inputs(
    intent: BubblesIntent,
    field_name: str,
    field: ClarificationField,
    value: str | None,
) -> None:
    request = _request(intent, **{field_name: value})

    projection = BubblesOrchestrationContract.plan(
        request,
        dependency=DependencyHealth.HEALTHY,
    )

    assert projection.state is ContractState.PLANNED
    assert projection.clarification_fields == (field,)
    assert not projection.accepted


def test_promote_requires_an_explicit_approval_reference() -> None:
    projection = BubblesOrchestrationContract.plan(
        _request(BubblesIntent.PROMOTE, approval_ref="  "),
        dependency=DependencyHealth.HEALTHY,
    )

    assert projection.state is ContractState.APPROVAL_REQUIRED
    assert projection.requires_approval
    assert not projection.accepted


def test_approved_promote_plan_preserves_its_intrinsic_approval_requirement() -> None:
    projection = BubblesOrchestrationContract.plan(
        _request(BubblesIntent.PROMOTE, approval_ref="approval:promote-1"),
        dependency=DependencyHealth.HEALTHY,
    )

    assert projection.accepted
    assert projection.state is ContractState.PLANNED
    assert projection.requires_approval
    assert projection.approval_ref == "approval:promote-1"


@pytest.mark.parametrize(
    "current_state",
    [ContractState.PLANNED, ContractState.APPROVAL_REQUIRED],
)
def test_promote_cancel_is_allowed_without_approval(
    current_state: ContractState,
) -> None:
    outcome = BubblesOrchestrationContract.cancel(
        _request(BubblesIntent.PROMOTE),
        current_state=current_state,
        dependency=DependencyHealth.HEALTHY,
    )

    assert outcome.accepted
    assert outcome.projection.state is ContractState.CANCELLED
    assert outcome.projection.requires_approval


def test_promote_status_and_result_remain_readable_without_approval() -> None:
    request = _request(BubblesIntent.PROMOTE)
    status = BubblesOrchestrationContract.status(
        request,
        state=ContractState.APPROVAL_REQUIRED,
        dependency=DependencyHealth.HEALTHY,
    )
    result = BubblesOrchestrationContract.result(
        request,
        state=ContractState.SUCCEEDED,
        dependency=DependencyHealth.HEALTHY,
        evidence_refs=(EvidenceReference("evidence:promote-1"),),
    )

    assert status.accepted
    assert status.state is ContractState.APPROVAL_REQUIRED
    assert result.accepted
    assert result.state is ContractState.SUCCEEDED
    assert status.requires_approval
    assert result.requires_approval


def test_promote_resume_and_progression_still_require_approval() -> None:
    request = _request(BubblesIntent.PROMOTE)
    resumed = BubblesOrchestrationContract.resume(
        request,
        current_state=ContractState.FAILED,
        dependency=DependencyHealth.HEALTHY,
    )
    progressed = BubblesOrchestrationContract.transition(
        request,
        current_state=ContractState.APPROVAL_REQUIRED,
        next_state=ContractState.QUEUED,
        dependency=DependencyHealth.HEALTHY,
    )

    assert resumed.rejection is TransitionRejection.APPROVAL_REQUIRED
    assert progressed.rejection is TransitionRejection.APPROVAL_REQUIRED
    assert not resumed.accepted
    assert not progressed.accepted


def test_public_transition_does_not_expose_an_approval_bypass() -> None:
    with pytest.raises(TypeError, match="require_promotion_approval"):
        BubblesOrchestrationContract.transition(
            _request(BubblesIntent.PROMOTE),
            current_state=ContractState.APPROVAL_REQUIRED,
            next_state=ContractState.QUEUED,
            dependency=DependencyHealth.HEALTHY,
            require_promotion_approval=False,
        )


def test_public_transition_cannot_use_cancel_operation_to_bypass_approval() -> None:
    outcome = BubblesOrchestrationContract.transition(
        _request(BubblesIntent.PROMOTE),
        current_state=ContractState.APPROVAL_REQUIRED,
        next_state=ContractState.QUEUED,
        dependency=DependencyHealth.HEALTHY,
        operation=LifecycleOperation.CANCEL,
    )

    assert outcome.rejection is TransitionRejection.APPROVAL_REQUIRED
    assert not outcome.projection.accepted


def test_missing_or_unhealthy_dependency_blocks_without_dispatch() -> None:
    request = _request(BubblesIntent.LIST)

    missing = BubblesOrchestrationContract.plan(request, dependency=None)
    unhealthy = BubblesOrchestrationContract.plan(
        request,
        dependency=DependencyHealth.UNHEALTHY,
    )

    assert missing.state is ContractState.BLOCKED_DEPENDENCY
    assert unhealthy.state is ContractState.BLOCKED_DEPENDENCY
    assert not missing.accepted
    assert not unhealthy.accepted


def test_terminal_success_requires_evidence_and_accepts_a_projection_only() -> None:
    request = _request(BubblesIntent.EVALUATE)

    missing_evidence = BubblesOrchestrationContract.transition(
        request,
        current_state=ContractState.RUNNING,
        next_state=ContractState.SUCCEEDED,
        dependency=DependencyHealth.HEALTHY,
    )
    evidence = EvidenceReference("evidence:evaluation-1")
    succeeded = BubblesOrchestrationContract.transition(
        request,
        current_state=ContractState.RUNNING,
        next_state=ContractState.SUCCEEDED,
        dependency=DependencyHealth.HEALTHY,
        evidence_refs=(evidence,),
        cost_refs=(CostReference("cost:evaluation-1"),),
    )

    assert missing_evidence.rejection is TransitionRejection.MISSING_EVIDENCE
    assert not missing_evidence.accepted
    assert not missing_evidence.projection.accepted
    assert missing_evidence.projection.message == "Terminal Bubbles success requires evidence."
    assert missing_evidence.projection.state is ContractState.RUNNING
    assert missing_evidence.projection.correlation is request.correlation
    assert succeeded.accepted
    assert succeeded.projection.state is ContractState.SUCCEEDED
    assert succeeded.projection.evidence_refs == (evidence,)
    assert succeeded.projection.cost_refs == (CostReference("cost:evaluation-1"),)
    assert succeeded.projection.approval_ref is None


def test_invalid_transition_is_rejected_and_not_reclassified_as_success() -> None:
    outcome = BubblesOrchestrationContract.transition(
        _request(BubblesIntent.LIST),
        current_state=ContractState.PLANNED,
        next_state=ContractState.RUNNING,
        dependency=DependencyHealth.HEALTHY,
    )

    assert outcome.rejection is TransitionRejection.ILLEGAL_TRANSITION
    assert outcome.projection.state is ContractState.PLANNED
    assert not outcome.accepted
    assert not outcome.projection.accepted
    assert outcome.projection.message == "The requested Bubbles state transition is illegal."


def test_cost_required_operation_rejects_a_missing_cost_reference() -> None:
    request = _request(BubblesIntent.SCORE, cost_required=True)

    outcome = BubblesOrchestrationContract.transition(
        request,
        current_state=ContractState.QUEUED,
        next_state=ContractState.RUNNING,
        dependency=DependencyHealth.HEALTHY,
    )

    assert outcome.rejection is TransitionRejection.MISSING_COST_REFERENCE
    assert not outcome.accepted


def test_shuttles_is_only_a_legacy_projection_stage() -> None:
    projection = BubblesOrchestrationContract.result(
        _request(BubblesIntent.LIST),
        state=ContractState.SUCCEEDED,
        dependency=DependencyHealth.HEALTHY,
        evidence_refs=(EvidenceReference("evidence:list-1"),),
        stage=ProjectionStage.SHUTTLES,
    )

    assert projection.canonical_space_id == "bubbles"
    assert projection.stage is ProjectionStage.SHUTTLES
    assert not hasattr(projection, "executor")


def test_contract_module_has_no_runtime_or_side_effect_imports() -> None:
    tree = ast.parse(inspect.getsource(bubbles_orchestration_contract))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imports <= {"__future__", "dataclasses", "enum", "typing"}
    assert not any(
        isinstance(node, ast.Name) and node.id == "Any" for node in ast.walk(tree)
    )
