"""Fail-closed, provider-neutral contract for Brain-to-Ideas operations.

The contract declares Brain's expectations of a future Ideas adapter.  It does
not select, configure, or invoke a provider; callers must explicitly inject an
adapter that implements :class:`IdeasAdapter`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class IdeasIntent(str, Enum):
    """Stable v1 vocabulary for requests that belong to the Ideas space."""

    CAPTURE = "capture"
    LIST_SEARCH = "list/search"
    INSPECT = "inspect"
    EDIT = "edit"
    LINK = "link"
    FORMAT = "format"
    GENERATE_DOCUMENT = "generate_document"
    PROPOSE_PROJECT = "propose_project"
    STATUS = "status"
    CANCEL = "cancel"
    RESUME = "resume"
    RESULT = "result"


class ClarificationField(str, Enum):
    """Known user-provided identifiers that can make an Ideas request usable."""

    IDEA_ID = "idea_id"
    BUBBLE_ID = "bubble_id"
    TARGET_ID = "target_id"
    OUTPUT_FORMAT = "output_format"
    PROJECT_ID = "project_id"


class LifecycleOperation(str, Enum):
    """The complete adapter lifecycle exposed to Brain."""

    HEALTH = "health"
    PLAN = "plan"
    START = "start"
    STATUS = "status"
    CANCEL = "cancel"
    RESUME = "resume"
    RESULT = "result"


class ContractStatus(str, Enum):
    """Fail-closed outcomes shared by the contract and its adapter boundary."""

    ACCEPTED = "accepted"
    CLARIFICATION_REQUIRED = "clarification_required"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    INVALID_REQUEST = "invalid_request"


class AdapterHealth(str, Enum):
    """Health declaration only; it is not proof of live provider execution."""

    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class IdeasCorrelation:
    """Opaque references owned by the shared Brain authority layer."""

    plan_id: str
    interaction_id: str
    task_id: str

    def __post_init__(self) -> None:
        for value in (self.plan_id, self.interaction_id, self.task_id):
            if not value.strip():
                raise ValueError("Ideas correlation references must be non-empty")


@dataclass(frozen=True)
class EvidenceReference:
    """An opaque evidence identifier; this contract never retrieves evidence."""

    reference_id: str

    def __post_init__(self) -> None:
        if not self.reference_id.strip():
            raise ValueError("Evidence reference IDs must be non-empty")


@dataclass(frozen=True)
class CostReference:
    """An opaque cost identifier; this contract never calculates charges."""

    reference_id: str

    def __post_init__(self) -> None:
        if not self.reference_id.strip():
            raise ValueError("Cost reference IDs must be non-empty")


@dataclass(frozen=True)
class IdeasRequest:
    """Provider-neutral input for one Ideas operation."""

    intent: IdeasIntent
    correlation: IdeasCorrelation
    idea_id: str | None = None
    bubble_id: str | None = None
    target_id: str | None = None
    output_format: str | None = None
    project_id: str | None = None
    approval_ref: str | None = None
    evidence_refs: tuple[EvidenceReference, ...] = ()
    cost_refs: tuple[CostReference, ...] = ()


@dataclass(frozen=True)
class AdapterHealthReport:
    """A declarative adapter-health response."""

    status: AdapterHealth
    detail: str = ""


@dataclass(frozen=True)
class AdapterOutcome:
    """A provider adapter's declarative response to one lifecycle operation."""

    status: ContractStatus
    message: str = ""
    evidence_refs: tuple[EvidenceReference, ...] = ()
    cost_refs: tuple[CostReference, ...] = ()


@dataclass(frozen=True)
class IdeasContractOutcome:
    """Brain-visible response preserving gates and opaque shared references."""

    contract_version: str
    status: ContractStatus
    operation: LifecycleOperation
    correlation: IdeasCorrelation
    message: str = ""
    clarification_fields: tuple[ClarificationField, ...] = ()
    requires_approval: bool = False
    evidence_refs: tuple[EvidenceReference, ...] = ()
    cost_refs: tuple[CostReference, ...] = ()


class IdeasAdapter(Protocol):
    """The only interface a future Ideas implementation must provide."""

    def health(self) -> AdapterHealthReport:
        """Return declarative health without executing an Ideas action."""

    def plan(self, request: IdeasRequest) -> AdapterOutcome:
        """Prepare a provider-neutral Ideas plan."""

    def start(self, request: IdeasRequest) -> AdapterOutcome:
        """Start an approved Ideas operation."""

    def status(self, request: IdeasRequest) -> AdapterOutcome:
        """Return lifecycle status for the correlated task."""

    def cancel(self, request: IdeasRequest) -> AdapterOutcome:
        """Cancel an approved correlated task."""

    def resume(self, request: IdeasRequest) -> AdapterOutcome:
        """Resume an approved correlated task."""

    def result(self, request: IdeasRequest) -> AdapterOutcome:
        """Return a correlated task result."""


class UnavailableIdeasAdapter:
    """Deterministic safe adapter used when no Ideas dependency is available."""

    def __init__(self, detail: str = "Ideas adapter is unavailable") -> None:
        self._detail = detail

    def health(self) -> AdapterHealthReport:
        return AdapterHealthReport(status=AdapterHealth.UNAVAILABLE, detail=self._detail)

    def plan(self, request: IdeasRequest) -> AdapterOutcome:
        return self._blocked()

    def start(self, request: IdeasRequest) -> AdapterOutcome:
        return self._blocked()

    def status(self, request: IdeasRequest) -> AdapterOutcome:
        return self._blocked()

    def cancel(self, request: IdeasRequest) -> AdapterOutcome:
        return self._blocked()

    def resume(self, request: IdeasRequest) -> AdapterOutcome:
        return self._blocked()

    def result(self, request: IdeasRequest) -> AdapterOutcome:
        return self._blocked()

    def _blocked(self) -> AdapterOutcome:
        return AdapterOutcome(
            status=ContractStatus.BLOCKED_DEPENDENCY,
            message=self._detail,
        )


class IdeasContract:
    """Validate and gate Ideas calls before delegating to an injected adapter."""

    CONTRACT_VERSION = "ideas-space-contract/v1"

    _REQUIRED_FIELDS: dict[IdeasIntent, tuple[ClarificationField, ...]] = {
        IdeasIntent.CAPTURE: (ClarificationField.BUBBLE_ID,),
        IdeasIntent.INSPECT: (ClarificationField.IDEA_ID,),
        IdeasIntent.EDIT: (ClarificationField.IDEA_ID,),
        IdeasIntent.LINK: (
            ClarificationField.IDEA_ID,
            ClarificationField.TARGET_ID,
        ),
        IdeasIntent.FORMAT: (
            ClarificationField.IDEA_ID,
            ClarificationField.OUTPUT_FORMAT,
        ),
        IdeasIntent.GENERATE_DOCUMENT: (
            ClarificationField.IDEA_ID,
            ClarificationField.TARGET_ID,
            ClarificationField.OUTPUT_FORMAT,
        ),
        IdeasIntent.PROPOSE_PROJECT: (
            ClarificationField.IDEA_ID,
            ClarificationField.PROJECT_ID,
        ),
    }
    _APPROVAL_REQUIRED = frozenset(
        {
            IdeasIntent.CAPTURE,
            IdeasIntent.EDIT,
            IdeasIntent.LINK,
            IdeasIntent.FORMAT,
            IdeasIntent.GENERATE_DOCUMENT,
            IdeasIntent.PROPOSE_PROJECT,
            IdeasIntent.CANCEL,
            IdeasIntent.RESUME,
        }
    )
    _STARTABLE_INTENTS = frozenset(
        {
            IdeasIntent.CAPTURE,
            IdeasIntent.LIST_SEARCH,
            IdeasIntent.INSPECT,
            IdeasIntent.EDIT,
            IdeasIntent.LINK,
            IdeasIntent.FORMAT,
            IdeasIntent.GENERATE_DOCUMENT,
            IdeasIntent.PROPOSE_PROJECT,
        }
    )

    def __init__(self, adapter: IdeasAdapter) -> None:
        self._adapter = adapter

    def health(self) -> AdapterHealthReport:
        """Read the adapter's declarative health only."""
        return self._adapter.health()

    def plan(self, request: IdeasRequest) -> IdeasContractOutcome:
        """Ask an adapter to plan after validating the request, without approval."""
        return self._run(LifecycleOperation.PLAN, request, require_approval=False)

    def start(self, request: IdeasRequest) -> IdeasContractOutcome:
        """Start a non-lifecycle intent after all required gates pass."""
        if request.intent not in self._STARTABLE_INTENTS:
            return self._invalid_operation(LifecycleOperation.START, request)
        return self._run(LifecycleOperation.START, request, require_approval=True)

    def status(self, request: IdeasRequest) -> IdeasContractOutcome:
        """Return status for a request declared with the status intent."""
        return self._lifecycle(LifecycleOperation.STATUS, IdeasIntent.STATUS, request)

    def cancel(self, request: IdeasRequest) -> IdeasContractOutcome:
        """Cancel only with an explicit approval reference."""
        return self._lifecycle(LifecycleOperation.CANCEL, IdeasIntent.CANCEL, request)

    def resume(self, request: IdeasRequest) -> IdeasContractOutcome:
        """Resume only with an explicit approval reference."""
        return self._lifecycle(LifecycleOperation.RESUME, IdeasIntent.RESUME, request)

    def result(self, request: IdeasRequest) -> IdeasContractOutcome:
        """Return a previously correlated result."""
        return self._lifecycle(LifecycleOperation.RESULT, IdeasIntent.RESULT, request)

    def _lifecycle(
        self,
        operation: LifecycleOperation,
        expected_intent: IdeasIntent,
        request: IdeasRequest,
    ) -> IdeasContractOutcome:
        if request.intent is not expected_intent:
            return self._invalid_operation(operation, request)
        return self._run(operation, request, require_approval=True)

    def _run(
        self,
        operation: LifecycleOperation,
        request: IdeasRequest,
        require_approval: bool,
    ) -> IdeasContractOutcome:
        clarifications = self._missing_fields(request)
        if clarifications:
            return IdeasContractOutcome(
                contract_version=self.CONTRACT_VERSION,
                status=ContractStatus.CLARIFICATION_REQUIRED,
                operation=operation,
                correlation=request.correlation,
                clarification_fields=clarifications,
                requires_approval=self._needs_approval(request, require_approval),
                evidence_refs=request.evidence_refs,
                cost_refs=request.cost_refs,
            )

        needs_approval = self._needs_approval(request, require_approval)
        if needs_approval and not self._has_approval(request):
            return IdeasContractOutcome(
                contract_version=self.CONTRACT_VERSION,
                status=ContractStatus.APPROVAL_REQUIRED,
                operation=operation,
                correlation=request.correlation,
                message="An approval reference is required before this operation.",
                requires_approval=True,
                evidence_refs=request.evidence_refs,
                cost_refs=request.cost_refs,
            )

        outcome = self._call_adapter(operation, request)
        return IdeasContractOutcome(
            contract_version=self.CONTRACT_VERSION,
            status=outcome.status,
            operation=operation,
            correlation=request.correlation,
            message=outcome.message,
            requires_approval=needs_approval,
            evidence_refs=request.evidence_refs + outcome.evidence_refs,
            cost_refs=request.cost_refs + outcome.cost_refs,
        )

    def _call_adapter(
        self,
        operation: LifecycleOperation,
        request: IdeasRequest,
    ) -> AdapterOutcome:
        if operation is LifecycleOperation.PLAN:
            return self._adapter.plan(request)
        if operation is LifecycleOperation.START:
            return self._adapter.start(request)
        if operation is LifecycleOperation.STATUS:
            return self._adapter.status(request)
        if operation is LifecycleOperation.CANCEL:
            return self._adapter.cancel(request)
        if operation is LifecycleOperation.RESUME:
            return self._adapter.resume(request)
        if operation is LifecycleOperation.RESULT:
            return self._adapter.result(request)
        raise ValueError(f"Unsupported Ideas lifecycle operation: {operation.value}")

    def _missing_fields(self, request: IdeasRequest) -> tuple[ClarificationField, ...]:
        missing: list[ClarificationField] = []
        for field in self._REQUIRED_FIELDS.get(request.intent, ()):
            if not self._value_for(request, field):
                missing.append(field)
        return tuple(missing)

    @staticmethod
    def _value_for(request: IdeasRequest, field: ClarificationField) -> str | None:
        if field is ClarificationField.IDEA_ID:
            return request.idea_id
        if field is ClarificationField.BUBBLE_ID:
            return request.bubble_id
        if field is ClarificationField.TARGET_ID:
            return request.target_id
        if field is ClarificationField.OUTPUT_FORMAT:
            return request.output_format
        if field is ClarificationField.PROJECT_ID:
            return request.project_id
        raise ValueError(f"Unsupported Ideas clarification field: {field.value}")

    def _needs_approval(self, request: IdeasRequest, required_for_operation: bool) -> bool:
        return required_for_operation and request.intent in self._APPROVAL_REQUIRED

    @staticmethod
    def _has_approval(request: IdeasRequest) -> bool:
        return bool(request.approval_ref and request.approval_ref.strip())

    def _invalid_operation(
        self,
        operation: LifecycleOperation,
        request: IdeasRequest,
    ) -> IdeasContractOutcome:
        return IdeasContractOutcome(
            contract_version=self.CONTRACT_VERSION,
            status=ContractStatus.INVALID_REQUEST,
            operation=operation,
            correlation=request.correlation,
            message="The Ideas intent does not match this lifecycle operation.",
            evidence_refs=request.evidence_refs,
            cost_refs=request.cost_refs,
        )
