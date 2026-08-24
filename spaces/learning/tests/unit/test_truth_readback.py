from __future__ import annotations

from uuid import uuid4

import pytest

from spaces.learning.bridge.truth_readback import ReadbackError, verify_readback
from spaces.learning.contracts.outcomes import (
    AggregateRefV1,
    EvidenceRefV1,
    TruthReadbackV1,
)


def _readback(**overrides: object) -> TruthReadbackV1:
    values: dict[str, object] = {
        "invocation_id": uuid4(),
        "correlation_id": uuid4(),
        "owner": "learnhouse",
        "terminal_state": "completed",
        "aggregate": AggregateRefV1(
            aggregate_type="course", aggregate_id="course-1", revision=2
        ),
        "evidence": EvidenceRefV1(
            owner="learnhouse",
            evidence_id="readback-1",
            evidence_type="application_readback",
        ),
    }
    values.update(overrides)
    return TruthReadbackV1.model_validate(values)


def test_readback_requires_matching_request_and_aggregate() -> None:
    readback = _readback()

    assert verify_readback(
        readback,
        invocation_id=readback.invocation_id,
        correlation_id=readback.correlation_id,
        aggregate=readback.aggregate,
    ) == readback


@pytest.mark.parametrize("field", ["invocation_id", "correlation_id"])
def test_readback_rejects_mismatched_correlation(field: str) -> None:
    readback = _readback()
    expected = {
        "invocation_id": readback.invocation_id,
        "correlation_id": readback.correlation_id,
    }
    expected[field] = uuid4()

    with pytest.raises(ReadbackError, match=field):
        verify_readback(readback, aggregate=readback.aggregate, **expected)


def test_readback_rejects_nonmatching_revision_and_non_success_terminal() -> None:
    readback = _readback()
    changed = AggregateRefV1(
        aggregate_type="course", aggregate_id="course-1", revision=3
    )
    with pytest.raises(ReadbackError, match="aggregate"):
        verify_readback(
            readback,
            invocation_id=readback.invocation_id,
            correlation_id=readback.correlation_id,
            aggregate=changed,
        )

    failed = _readback(terminal_state="failed")
    with pytest.raises(ReadbackError, match="terminal_state"):
        verify_readback(
            failed,
            invocation_id=failed.invocation_id,
            correlation_id=failed.correlation_id,
            aggregate=failed.aggregate,
        )
