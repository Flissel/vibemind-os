from __future__ import annotations

from uuid import UUID

from spaces.learning.contracts.outcomes import AggregateRefV1, TruthReadbackV1


class ReadbackError(RuntimeError):
    """A terminal application result could not be correlated and verified."""


def verify_readback(
    readback: TruthReadbackV1,
    *,
    invocation_id: UUID,
    correlation_id: UUID,
    aggregate: AggregateRefV1,
) -> TruthReadbackV1:
    if readback.invocation_id != invocation_id:
        raise ReadbackError("invocation_id mismatch")
    if readback.correlation_id != correlation_id:
        raise ReadbackError("correlation_id mismatch")
    if readback.aggregate != aggregate:
        raise ReadbackError("aggregate mismatch")
    if readback.terminal_state != "completed":
        raise ReadbackError(f"terminal_state is {readback.terminal_state}")
    return readback
