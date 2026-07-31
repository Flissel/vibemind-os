"""Fail-closed, provider-neutral orchestration contract for Bubbles.

This module describes validation, state transitions, and projections only.  It
does not choose an executor, contact a provider, or claim Golden-Path success.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BubblesIntent(str, Enum):
    """Stable v1 vocabulary for canonical Bubbles requests."""

    CREATE = "create"
    LIST = "list"
    FIND = "find"
    ENTER = "enter"
    EXIT = "exit"
    UPDATE = "update"
    SCORE = "score"
    EVALUATE = "evaluate"
    PROMOTE = "promote"


class LifecycleOperation(str, Enum):
    """Lifecycle projections available without dispatching an operation."""

    PLAN = "plan"
    STATUS = "status"
    CANCEL = "cancel"
    RESUME = "resume"
    RESULT = "result"


class ContractState(str, Enum):
    """Legal orchestration states; they are declarations, never execution proof."""

    PLANNED = "planned"
    APPROVAL_REQUIRED = "approval_required"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED_DEPENDENCY = "blocked_dependency"


class DependencyHealth(str, Enum):
    """A declared dependency condition supplied by the caller."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class ClarificationField(str, Enum):
    """User inputs that must be present before a request can be planned."""

    BUBBLE_ID = "bubble_id"
    NAME = "name"
    QUERY = "query"
    SCORE_CONTEXT = "score_context"
    EVALUATION_CONTEXT = "evaluation_context"
    PROMOTE_TARGET = "promote_target"


class TransitionRejection(str, Enum):
    """Deterministic reasons why a declared transition was not accepted."""

    CLARIFICATION_REQUIRED = "clarification_required"
    APPROVAL_REQUIRED = "approval_required"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    ILLEGAL_TRANSITION = "illegal_transition"
    MISSING_EVIDENCE = "missing_evidence"
    MISSING_COST_REFERENCE = "missing_cost_reference"


class ProjectionStage(str, Enum):
    """Legacy pipeline labels permitted only on a read-only projection."""

    SHUTTLES = "shuttles"


@dataclass(frozen=True)
class OpaqueCorrelation:
    """Shared identifiers owned outside the Bubbles contract."""

    plan_id: str
    interaction_id: str
    task_id: str

    def __post_init__(self) -> None:
        for reference in (self.plan_id, self.interaction_id, self.task_id):
            if not reference.strip():
                raise ValueError("Correlation references must be non-empty")


@dataclass(frozen=True)
class EvidenceReference:
    """Opaque evidence reference; the contract never resolves it."""

    reference_id: str

    def __post_init__(self) -> None:
        if not self.reference_id.strip():
            raise ValueError("Evidence reference IDs must be non-empty")


@dataclass(frozen=True)
class CostReference:
    """Opaque cost reference; the contract never calculates it."""

    reference_id: str

    def __post_init__(self) -> None:
        if not self.reference_id.strip():
            raise ValueError("Cost reference IDs must be non-empty")


@dataclass(frozen=True)
class BubblesRequest:
    """Provider-neutral request for one canonical Bubbles intent."""

    intent: BubblesIntent
    operation_id: str
    correlation: OpaqueCorrelation
    bubble_id: str | None = None
    name: str | None = None
    query: str | None = None
    score_context: str | None = None
    evaluation_context: str | None = None
    promote_target: str | None = None
    approval_ref: str | None = None
    evidence_refs: tuple[EvidenceReference, ...] = ()
    cost_refs: tuple[CostReference, ...] = ()
    cost_required: bool = False

    def __post_init__(self) -> None:
        if not self.operation_id.strip():
            raise ValueError("Operation IDs must be non-empty")


@dataclass(frozen=True)
class BubblesProjection:
    """Read-only status/result projection that cannot imply dispatch success."""

    contract_version: str
    canonical_space_id: str
    operation: LifecycleOperation
    state: ContractState
    operation_id: str
    correlation: OpaqueCorrelation
    accepted: bool
    clarification_fields: tuple[ClarificationField, ...] = ()
    requires_approval: bool = False
    approval_ref: str | None = None
    evidence_refs: tuple[EvidenceReference, ...] = ()
    cost_refs: tuple[CostReference, ...] = ()
    stage: ProjectionStage | None = None
    message: str = ""


@dataclass(frozen=True)
class TransitionOutcome:
    """A pure transition decision and its status projection."""

    projection: BubblesProjection
    rejection: TransitionRejection | None = None

    @property
    def accepted(self) -> bool:
        return self.rejection is None and self.projection.accepted


class BubblesOrchestrationContract:
    """Validate Bubbles requests and state transitions without execution."""

    CONTRACT_VERSION = "bubbles.orchestration.v1"
    CANONICAL_SPACE_ID = "bubbles"

    _REQUIRED_FIELDS: dict[BubblesIntent, tuple[ClarificationField, ...]] = {
        BubblesIntent.CREATE: (ClarificationField.NAME,),
        BubblesIntent.FIND: (ClarificationField.QUERY,),
        BubblesIntent.ENTER: (ClarificationField.BUBBLE_ID,),
        BubblesIntent.EXIT: (ClarificationField.BUBBLE_ID,),
        BubblesIntent.UPDATE: (ClarificationField.BUBBLE_ID,),
        BubblesIntent.SCORE: (
            ClarificationField.BUBBLE_ID,
            ClarificationField.SCORE_CONTEXT,
        ),
        BubblesIntent.EVALUATE: (
            ClarificationField.BUBBLE_ID,
            ClarificationField.EVALUATION_CONTEXT,
        ),
        BubblesIntent.PROMOTE: (
            ClarificationField.BUBBLE_ID,
            ClarificationField.PROMOTE_TARGET,
        ),
    }
    _ALLOWED_TRANSITIONS: dict[ContractState, frozenset[ContractState]] = {
        ContractState.PLANNED: frozenset(
            {
                ContractState.APPROVAL_REQUIRED,
                ContractState.QUEUED,
                ContractState.CANCELLED,
                ContractState.BLOCKED_DEPENDENCY,
            }
        ),
        ContractState.APPROVAL_REQUIRED: frozenset(
            {
                ContractState.QUEUED,
                ContractState.CANCELLED,
                ContractState.BLOCKED_DEPENDENCY,
            }
        ),
        ContractState.QUEUED: frozenset(
            {
                ContractState.RUNNING,
                ContractState.FAILED,
                ContractState.CANCELLED,
                ContractState.BLOCKED_DEPENDENCY,
            }
        ),
        ContractState.RUNNING: frozenset(
            {
                ContractState.SUCCEEDED,
                ContractState.FAILED,
                ContractState.CANCELLED,
                ContractState.BLOCKED_DEPENDENCY,
            }
        ),
        ContractState.FAILED: frozenset(
            {ContractState.QUEUED, ContractState.CANCELLED}
        ),
        ContractState.CANCELLED: frozenset({ContractState.QUEUED}),
        ContractState.BLOCKED_DEPENDENCY: frozenset(
            {ContractState.QUEUED, ContractState.CANCELLED}
        ),
        ContractState.SUCCEEDED: frozenset(),
    }

    @classmethod
    def plan(
        cls,
        request: BubblesRequest,
        *,
        dependency: DependencyHealth | None,
    ) -> BubblesProjection:
        """Return a validated plan projection; no work is queued or executed."""
        return cls._gate(
            request,
            operation=LifecycleOperation.PLAN,
            state=ContractState.PLANNED,
            dependency=dependency,
            require_promotion_approval=True,
        )

    @classmethod
    def status(
        cls,
        request: BubblesRequest,
        *,
        state: ContractState,
        dependency: DependencyHealth | None,
        evidence_refs: tuple[EvidenceReference, ...] = (),
        cost_refs: tuple[CostReference, ...] = (),
        stage: ProjectionStage | None = None,
    ) -> BubblesProjection:
        """Project an externally supplied status without treating it as execution."""
        return cls._gate(
            request,
            operation=LifecycleOperation.STATUS,
            state=state,
            dependency=dependency,
            evidence_refs=evidence_refs,
            cost_refs=cost_refs,
            stage=stage,
            require_promotion_approval=False,
        )

    @classmethod
    def cancel(
        cls,
        request: BubblesRequest,
        *,
        current_state: ContractState,
        dependency: DependencyHealth | None,
    ) -> TransitionOutcome:
        """Declare a cancellation transition without contacting an executor."""
        return cls.transition(
            request,
            current_state=current_state,
            next_state=ContractState.CANCELLED,
            dependency=dependency,
            operation=LifecycleOperation.CANCEL,
            require_promotion_approval=False,
        )

    @classmethod
    def resume(
        cls,
        request: BubblesRequest,
        *,
        current_state: ContractState,
        dependency: DependencyHealth | None,
    ) -> TransitionOutcome:
        """Declare a resumption transition without contacting an executor."""
        return cls.transition(
            request,
            current_state=current_state,
            next_state=ContractState.QUEUED,
            dependency=dependency,
            operation=LifecycleOperation.RESUME,
        )

    @classmethod
    def result(
        cls,
        request: BubblesRequest,
        *,
        state: ContractState,
        dependency: DependencyHealth | None,
        evidence_refs: tuple[EvidenceReference, ...] = (),
        cost_refs: tuple[CostReference, ...] = (),
        stage: ProjectionStage | None = None,
    ) -> BubblesProjection:
        """Project a result and require evidence for an asserted terminal success."""
        return cls._gate(
            request,
            operation=LifecycleOperation.RESULT,
            state=state,
            dependency=dependency,
            evidence_refs=evidence_refs,
            cost_refs=cost_refs,
            stage=stage,
            require_promotion_approval=False,
        )

    @classmethod
    def transition(
        cls,
        request: BubblesRequest,
        *,
        current_state: ContractState,
        next_state: ContractState,
        dependency: DependencyHealth | None,
        evidence_refs: tuple[EvidenceReference, ...] = (),
        cost_refs: tuple[CostReference, ...] = (),
        operation: LifecycleOperation = LifecycleOperation.STATUS,
        require_promotion_approval: bool = True,
    ) -> TransitionOutcome:
        """Validate one proposed state transition using only supplied declarations."""
        gate = cls._gate(
            request,
            operation=operation,
            state=current_state,
            dependency=dependency,
            evidence_refs=evidence_refs,
            cost_refs=cost_refs,
            require_promotion_approval=require_promotion_approval,
        )
        rejection = cls._rejection_for(gate)
        if rejection is not None:
            return TransitionOutcome(projection=gate, rejection=rejection)
        if next_state not in cls._ALLOWED_TRANSITIONS[current_state]:
            return TransitionOutcome(
                projection=gate,
                rejection=TransitionRejection.ILLEGAL_TRANSITION,
            )
        if next_state is ContractState.SUCCEEDED and not gate.evidence_refs:
            return TransitionOutcome(
                projection=gate,
                rejection=TransitionRejection.MISSING_EVIDENCE,
            )
        return TransitionOutcome(
            projection=cls._projection(
                request,
                operation=operation,
                state=next_state,
                accepted=True,
                evidence_refs=gate.evidence_refs,
                cost_refs=gate.cost_refs,
            )
        )

    @classmethod
    def _gate(
        cls,
        request: BubblesRequest,
        *,
        operation: LifecycleOperation,
        state: ContractState,
        dependency: DependencyHealth | None,
        evidence_refs: tuple[EvidenceReference, ...] = (),
        cost_refs: tuple[CostReference, ...] = (),
        stage: ProjectionStage | None = None,
        require_promotion_approval: bool = True,
    ) -> BubblesProjection:
        all_evidence = request.evidence_refs + evidence_refs
        all_costs = request.cost_refs + cost_refs
        clarification_fields = cls._missing_fields(request)
        if clarification_fields:
            return cls._projection(
                request,
                operation=operation,
                state=ContractState.PLANNED,
                accepted=False,
                clarification_fields=clarification_fields,
                evidence_refs=all_evidence,
                cost_refs=all_costs,
                stage=stage,
                message="Required Bubbles request data is missing.",
            )
        if (
            require_promotion_approval
            and request.intent is BubblesIntent.PROMOTE
            and not cls._has_approval(request)
        ):
            return cls._projection(
                request,
                operation=operation,
                state=ContractState.APPROVAL_REQUIRED,
                accepted=False,
                requires_approval=True,
                evidence_refs=all_evidence,
                cost_refs=all_costs,
                stage=stage,
                message="Promotion requires an approval reference.",
            )
        if dependency is not DependencyHealth.HEALTHY:
            return cls._projection(
                request,
                operation=operation,
                state=ContractState.BLOCKED_DEPENDENCY,
                accepted=False,
                evidence_refs=all_evidence,
                cost_refs=all_costs,
                stage=stage,
                message="The declared Bubbles dependency is unavailable or unhealthy.",
            )
        if request.cost_required and not all_costs:
            return cls._projection(
                request,
                operation=operation,
                state=state,
                accepted=False,
                evidence_refs=all_evidence,
                cost_refs=all_costs,
                stage=stage,
                message="This operation requires a cost reference.",
            )
        if state is ContractState.SUCCEEDED and not all_evidence:
            return cls._projection(
                request,
                operation=operation,
                state=state,
                accepted=False,
                evidence_refs=all_evidence,
                cost_refs=all_costs,
                stage=stage,
                message="A terminal success projection requires evidence.",
            )
        return cls._projection(
            request,
            operation=operation,
            state=state,
            accepted=True,
            evidence_refs=all_evidence,
            cost_refs=all_costs,
            stage=stage,
        )

    @classmethod
    def _projection(
        cls,
        request: BubblesRequest,
        *,
        operation: LifecycleOperation,
        state: ContractState,
        accepted: bool,
        clarification_fields: tuple[ClarificationField, ...] = (),
        requires_approval: bool = False,
        evidence_refs: tuple[EvidenceReference, ...] = (),
        cost_refs: tuple[CostReference, ...] = (),
        stage: ProjectionStage | None = None,
        message: str = "",
    ) -> BubblesProjection:
        return BubblesProjection(
            contract_version=cls.CONTRACT_VERSION,
            canonical_space_id=cls.CANONICAL_SPACE_ID,
            operation=operation,
            state=state,
            operation_id=request.operation_id,
            correlation=request.correlation,
            accepted=accepted,
            clarification_fields=clarification_fields,
            requires_approval=(
                requires_approval or request.intent is BubblesIntent.PROMOTE
            ),
            approval_ref=request.approval_ref if cls._has_approval(request) else None,
            evidence_refs=evidence_refs,
            cost_refs=cost_refs,
            stage=stage,
            message=message,
        )

    @classmethod
    def _missing_fields(
        cls,
        request: BubblesRequest,
    ) -> tuple[ClarificationField, ...]:
        missing: list[ClarificationField] = []
        for field in cls._REQUIRED_FIELDS.get(request.intent, ()):
            value = cls._field_value(request, field)
            if value is None or not value.strip():
                missing.append(field)
        return tuple(missing)

    @staticmethod
    def _field_value(
        request: BubblesRequest,
        field: ClarificationField,
    ) -> str | None:
        if field is ClarificationField.BUBBLE_ID:
            return request.bubble_id
        if field is ClarificationField.NAME:
            return request.name
        if field is ClarificationField.QUERY:
            return request.query
        if field is ClarificationField.SCORE_CONTEXT:
            return request.score_context
        if field is ClarificationField.EVALUATION_CONTEXT:
            return request.evaluation_context
        if field is ClarificationField.PROMOTE_TARGET:
            return request.promote_target
        raise ValueError(f"Unsupported clarification field: {field.value}")

    @staticmethod
    def _has_approval(request: BubblesRequest) -> bool:
        return bool(request.approval_ref and request.approval_ref.strip())

    @staticmethod
    def _rejection_for(
        projection: BubblesProjection,
    ) -> TransitionRejection | None:
        if projection.accepted:
            return None
        if projection.clarification_fields:
            return TransitionRejection.CLARIFICATION_REQUIRED
        if projection.state is ContractState.APPROVAL_REQUIRED:
            return TransitionRejection.APPROVAL_REQUIRED
        if projection.state is ContractState.BLOCKED_DEPENDENCY:
            return TransitionRejection.DEPENDENCY_UNAVAILABLE
        if "cost reference" in projection.message:
            return TransitionRejection.MISSING_COST_REFERENCE
        if "requires evidence" in projection.message:
            return TransitionRejection.MISSING_EVIDENCE
        return TransitionRejection.ILLEGAL_TRANSITION
