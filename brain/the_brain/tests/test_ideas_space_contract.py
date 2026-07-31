"""Tests for the fail-closed Brain-to-Ideas adapter contract."""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field

import pytest

from core import ideas_space_contract
from core.ideas_space_contract import (
    AdapterHealth,
    AdapterHealthReport,
    AdapterOutcome,
    ClarificationField,
    ContractStatus,
    CostReference,
    EvidenceReference,
    IdeasContract,
    IdeasCorrelation,
    IdeasIntent,
    IdeasRequest,
    LifecycleOperation,
    UnavailableIdeasAdapter,
)


@dataclass
class RecordingIdeasAdapter:
    """In-memory adapter double: records contract calls without side effects."""

    calls: list[LifecycleOperation] = field(default_factory=list)
    outcome: AdapterOutcome = field(
        default_factory=lambda: AdapterOutcome(
            status=ContractStatus.ACCEPTED,
            message="adapter accepted request",
            evidence_refs=(EvidenceReference("evidence:adapter"),),
            cost_refs=(CostReference("cost:adapter"),),
        )
    )

    def health(self) -> AdapterHealthReport:
        self.calls.append(LifecycleOperation.HEALTH)
        return AdapterHealthReport(status=AdapterHealth.HEALTHY)

    def plan(self, request: IdeasRequest) -> AdapterOutcome:
        self.calls.append(LifecycleOperation.PLAN)
        return self.outcome

    def start(self, request: IdeasRequest) -> AdapterOutcome:
        self.calls.append(LifecycleOperation.START)
        return self.outcome

    def status(self, request: IdeasRequest) -> AdapterOutcome:
        self.calls.append(LifecycleOperation.STATUS)
        return self.outcome

    def cancel(self, request: IdeasRequest) -> AdapterOutcome:
        self.calls.append(LifecycleOperation.CANCEL)
        return self.outcome

    def resume(self, request: IdeasRequest) -> AdapterOutcome:
        self.calls.append(LifecycleOperation.RESUME)
        return self.outcome

    def result(self, request: IdeasRequest) -> AdapterOutcome:
        self.calls.append(LifecycleOperation.RESULT)
        return self.outcome


def _request(intent: IdeasIntent, **overrides: object) -> IdeasRequest:
    values: dict[str, object] = {
        "intent": intent,
        "correlation": IdeasCorrelation(
            plan_id="plan:ideas-1",
            interaction_id="interaction:ideas-1",
            task_id="task:ideas-1",
        ),
        "idea_id": "idea:1",
        "bubble_id": "bubble:1",
        "target_id": "target:1",
        "output_format": "markdown",
        "project_id": "project:1",
    }
    values.update(overrides)
    return IdeasRequest(**values)


def test_contract_declares_the_stable_v1_intent_vocabulary() -> None:
    assert IdeasContract.CONTRACT_VERSION == "ideas-space-contract/v1"
    assert {intent.value for intent in IdeasIntent} == {
        "capture",
        "list/search",
        "inspect",
        "edit",
        "link",
        "format",
        "generate_document",
        "propose_project",
        "status",
        "cancel",
        "resume",
        "result",
    }


@pytest.mark.parametrize(
    ("intent", "missing_name", "expected_field"),
    [
        (IdeasIntent.CAPTURE, "bubble_id", ClarificationField.BUBBLE_ID),
        (IdeasIntent.INSPECT, "idea_id", ClarificationField.IDEA_ID),
        (IdeasIntent.LINK, "target_id", ClarificationField.TARGET_ID),
        (IdeasIntent.FORMAT, "output_format", ClarificationField.OUTPUT_FORMAT),
        (IdeasIntent.PROPOSE_PROJECT, "project_id", ClarificationField.PROJECT_ID),
    ],
)
def test_start_requests_required_ideas_clarifications(
    intent: IdeasIntent,
    missing_name: str,
    expected_field: ClarificationField,
) -> None:
    adapter = RecordingIdeasAdapter()
    response = IdeasContract(adapter).start(_request(intent, **{missing_name: None}))

    assert response.status is ContractStatus.CLARIFICATION_REQUIRED
    assert response.clarification_fields == (expected_field,)
    assert adapter.calls == []


def test_writing_or_costly_start_requires_an_approval_before_adapter_call() -> None:
    adapter = RecordingIdeasAdapter()
    contract = IdeasContract(adapter)

    blocked = contract.start(_request(IdeasIntent.GENERATE_DOCUMENT))

    assert blocked.status is ContractStatus.APPROVAL_REQUIRED
    assert blocked.requires_approval is True
    assert adapter.calls == []

    approved = contract.start(
        _request(IdeasIntent.GENERATE_DOCUMENT, approval_ref="approval:ideas-1")
    )

    assert approved.status is ContractStatus.ACCEPTED
    assert approved.requires_approval is True
    assert adapter.calls == [LifecycleOperation.START]


def test_unavailable_adapter_blocks_deterministically_without_a_fallback() -> None:
    contract = IdeasContract(UnavailableIdeasAdapter("Ideas provider is not configured"))

    response = contract.start(
        _request(IdeasIntent.CAPTURE, approval_ref="approval:ideas-1")
    )

    assert response.status is ContractStatus.BLOCKED_DEPENDENCY
    assert response.message == "Ideas provider is not configured"
    assert response.operation is LifecycleOperation.START


def test_lifecycle_operations_delegate_only_after_contract_gates() -> None:
    adapter = RecordingIdeasAdapter()
    contract = IdeasContract(adapter)
    read_request = _request(IdeasIntent.STATUS)
    approved_write_request = _request(
        IdeasIntent.CAPTURE, approval_ref="approval:ideas-1"
    )

    assert contract.health().status is AdapterHealth.HEALTHY
    assert contract.plan(_request(IdeasIntent.LIST_SEARCH)).status is ContractStatus.ACCEPTED
    assert contract.start(approved_write_request).status is ContractStatus.ACCEPTED
    assert contract.status(read_request).status is ContractStatus.ACCEPTED
    assert contract.cancel(
        _request(IdeasIntent.CANCEL, approval_ref="approval:ideas-1")
    ).status is ContractStatus.ACCEPTED
    assert contract.resume(
        _request(IdeasIntent.RESUME, approval_ref="approval:ideas-1")
    ).status is ContractStatus.ACCEPTED
    assert contract.result(_request(IdeasIntent.RESULT)).status is ContractStatus.ACCEPTED
    assert adapter.calls == [
        LifecycleOperation.HEALTH,
        LifecycleOperation.PLAN,
        LifecycleOperation.START,
        LifecycleOperation.STATUS,
        LifecycleOperation.CANCEL,
        LifecycleOperation.RESUME,
        LifecycleOperation.RESULT,
    ]


def test_contract_projects_correlation_and_declarative_evidence_and_cost_ids() -> None:
    adapter = RecordingIdeasAdapter()
    request = _request(
        IdeasIntent.CAPTURE,
        approval_ref="approval:ideas-1",
        evidence_refs=(EvidenceReference("evidence:requested"),),
        cost_refs=(CostReference("cost:requested"),),
    )

    response = IdeasContract(adapter).start(request)

    assert response.correlation == request.correlation
    assert response.evidence_refs == (
        EvidenceReference("evidence:requested"),
        EvidenceReference("evidence:adapter"),
    )
    assert response.cost_refs == (
        CostReference("cost:requested"),
        CostReference("cost:adapter"),
    )


def test_contract_module_has_no_direct_legacy_database_or_http_dependencies() -> None:
    tree = ast.parse(inspect.getsource(ideas_space_contract))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert imported_roots.isdisjoint(
        {"http", "requests", "sqlalchemy", "supabase", "urllib"}
    )
    source = inspect.getsource(ideas_space_contract)
    assert "IdeasClient" not in source
    assert "AutoDispatcher" not in source
