from __future__ import annotations

import pytest

from spaces.learning.services.course_factory.state_machine import (
    FACTORY_PIPELINE,
    FactoryState,
    InvalidFactoryTransition,
    require_retryable,
    require_transition,
)


def test_every_pipeline_transition_is_explicit_and_sequential() -> None:
    expected = (
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

    assert FACTORY_PIPELINE == expected
    for current, target in zip(expected, expected[1:]):
        require_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (FactoryState.QUEUED, FactoryState.STRUCTURING),
        (FactoryState.INGESTING, FactoryState.AUTHORING),
        (FactoryState.AUTHORING, FactoryState.VERIFYING),
        (FactoryState.QUALITY_GATE, FactoryState.PUBLISHED),
        (FactoryState.PUBLISHED, FactoryState.QUEUED),
        (FactoryState.CANCELLED, FactoryState.INGESTING),
        (FactoryState.REJECTED, FactoryState.REVIEW_READY),
    ],
)
def test_skipped_reversed_and_terminal_transitions_are_rejected(
    current: FactoryState, target: FactoryState
) -> None:
    with pytest.raises(InvalidFactoryTransition):
        require_transition(current, target)


@pytest.mark.parametrize(
    "current",
    [
        FactoryState.QUEUED,
        FactoryState.INGESTING,
        FactoryState.STRUCTURING,
        FactoryState.AUTHORING,
        FactoryState.ASSESSING,
        FactoryState.VERIFYING,
        FactoryState.QUALITY_GATE,
        FactoryState.REVIEW_READY,
    ],
)
def test_active_attempts_can_fail_or_be_cancelled(current: FactoryState) -> None:
    require_transition(current, FactoryState.FAILED)
    require_transition(current, FactoryState.CANCELLED)


def test_only_review_ready_attempts_can_be_rejected() -> None:
    require_transition(FactoryState.REVIEW_READY, FactoryState.REJECTED)

    with pytest.raises(InvalidFactoryTransition):
        require_transition(FactoryState.AUTHORING, FactoryState.REJECTED)


@pytest.mark.parametrize(
    "state",
    [FactoryState.FAILED, FactoryState.CANCELLED, FactoryState.REJECTED],
)
def test_only_non_success_terminal_attempts_are_retryable(state: FactoryState) -> None:
    require_retryable(state)

    with pytest.raises(InvalidFactoryTransition):
        require_retryable(FactoryState.PUBLISHED)
