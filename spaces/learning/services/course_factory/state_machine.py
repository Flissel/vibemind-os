from __future__ import annotations

from enum import Enum


class FactoryState(str, Enum):
    QUEUED = "queued"
    INGESTING = "ingesting"
    STRUCTURING = "structuring"
    AUTHORING = "authoring"
    ASSESSING = "assessing"
    VERIFYING = "verifying"
    QUALITY_GATE = "quality_gate"
    REVIEW_READY = "review_ready"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


FACTORY_PIPELINE = (
    FactoryState.QUEUED,
    FactoryState.INGESTING,
    FactoryState.STRUCTURING,
    FactoryState.AUTHORING,
    FactoryState.ASSESSING,
    FactoryState.VERIFYING,
    FactoryState.QUALITY_GATE,
    FactoryState.REVIEW_READY,
    FactoryState.PUBLISHED,
)
NON_SUCCESS_TERMINAL_STATES = frozenset(
    {FactoryState.FAILED, FactoryState.CANCELLED, FactoryState.REJECTED}
)
TERMINAL_STATES = NON_SUCCESS_TERMINAL_STATES | {FactoryState.PUBLISHED}
ACTIVE_STATES = frozenset(FACTORY_PIPELINE[:-1])


class InvalidFactoryTransition(ValueError):
    pass


def require_transition(current: FactoryState, target: FactoryState) -> None:
    allowed: set[FactoryState] = set()
    if current in ACTIVE_STATES:
        allowed.update({FactoryState.FAILED, FactoryState.CANCELLED})
        position = FACTORY_PIPELINE.index(current)
        allowed.add(FACTORY_PIPELINE[position + 1])
    if current is FactoryState.REVIEW_READY:
        allowed.add(FactoryState.REJECTED)
    if target not in allowed:
        raise InvalidFactoryTransition(
            f"factory transition {current.value} -> {target.value} is not allowed"
        )


def require_retryable(state: FactoryState) -> None:
    if state not in NON_SUCCESS_TERMINAL_STATES:
        raise InvalidFactoryTransition(
            f"factory state {state.value} cannot create a retry attempt"
        )
