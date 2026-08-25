from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from spaces.learning.bridge.dispatcher import (
    ApplicationOutcomeV1,
    InMemoryReceiptStore,
    LearningDispatcher,
)
from spaces.learning.contracts.events import LearningEventType, LearningToolName
from spaces.learning.contracts.mcp_models import ActorV1, EventEnvelopeV1, ToolRequestV1
from spaces.learning.contracts.outcomes import (
    AggregateRefV1,
    EvidenceRefV1,
    ToolErrorV1,
    TruthReadbackV1,
)


def _request(
    *,
    key: str = "create-1",
    title: str = "AI Safety",
) -> ToolRequestV1:
    return ToolRequestV1(
        tool=LearningToolName.COURSE_CREATE,
        event=EventEnvelopeV1(
            event_type=LearningEventType.COURSE_CREATE,
            invocation_id=uuid4(),
            correlation_id=uuid4(),
            actor=ActorV1(actor_id="local-owner", actor_type="local_user"),
            idempotency_key=key,
            payload={"title": title},
        ),
    )


@dataclass
class FakeGateway:
    outcome: ApplicationOutcomeV1
    readback_value: TruthReadbackV1 | None = None
    execute_calls: int = 0
    readback_calls: int = 0

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        self.execute_calls += 1
        return self.outcome

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None:
        self.readback_calls += 1
        if self.readback_value is None:
            return None
        return self.readback_value.model_copy(
            update={
                "invocation_id": request.event.invocation_id,
                "correlation_id": request.event.correlation_id,
            }
        )


def _completed_gateway() -> FakeGateway:
    aggregate = AggregateRefV1(
        aggregate_type="course", aggregate_id="course-1", revision=1
    )
    return FakeGateway(
        outcome=ApplicationOutcomeV1(
            state="completed", aggregate=aggregate, result={"course_id": "course-1"}
        ),
        readback_value=TruthReadbackV1(
            invocation_id=uuid4(),
            correlation_id=uuid4(),
            owner="learnhouse",
            terminal_state="completed",
            aggregate=aggregate,
            evidence=EvidenceRefV1(
                owner="learnhouse",
                evidence_id="readback-1",
                evidence_type="application_readback",
            ),
        ),
    )


def test_duplicate_idempotency_key_returns_original_result_without_second_call() -> None:
    gateway = _completed_gateway()
    dispatcher = LearningDispatcher(
        gateways={LearningToolName.COURSE_CREATE: gateway},
        receipts=InMemoryReceiptStore(),
    )
    first = dispatcher.dispatch(_request())
    second = dispatcher.dispatch(_request())

    assert first.state == "completed"
    assert second.model_dump() == first.model_dump()
    assert gateway.execute_calls == 1
    assert gateway.readback_calls == 1


def test_idempotency_key_reuse_with_different_payload_fails_closed() -> None:
    gateway = _completed_gateway()
    dispatcher = LearningDispatcher(
        gateways={LearningToolName.COURSE_CREATE: gateway},
        receipts=InMemoryReceiptStore(),
    )
    dispatcher.dispatch(_request(title="First"))
    conflict = dispatcher.dispatch(_request(title="Different"))

    assert conflict.state == "rejected"
    assert conflict.error and conflict.error.code == "idempotency_conflict"
    assert gateway.execute_calls == 1


def test_missing_gateway_is_typed_unavailable() -> None:
    result = LearningDispatcher(gateways={}, receipts=InMemoryReceiptStore()).dispatch(
        _request()
    )

    assert result.state == "unavailable"
    assert result.error and result.error.code == "learning_backend_unavailable"


def test_completed_transport_without_aggregate_or_readback_fails_closed() -> None:
    no_aggregate = FakeGateway(outcome=ApplicationOutcomeV1(state="completed"))
    dispatcher = LearningDispatcher(
        gateways={LearningToolName.COURSE_CREATE: no_aggregate},
        receipts=InMemoryReceiptStore(),
    )
    result = dispatcher.dispatch(_request())
    assert result.state == "unavailable"
    assert result.error and result.error.code == "truth_readback_unverified"

    no_readback = _completed_gateway()
    no_readback.readback_value = None
    dispatcher = LearningDispatcher(
        gateways={LearningToolName.COURSE_CREATE: no_readback},
        receipts=InMemoryReceiptStore(),
    )
    result = dispatcher.dispatch(_request(key="create-2"))
    assert result.state == "unavailable"
    assert result.error and result.error.code == "truth_readback_unverified"
    assert result.error.retryable is False


def test_gateway_revision_conflict_is_preserved_without_readback() -> None:
    gateway = FakeGateway(
        outcome=ApplicationOutcomeV1(
            state="rejected",
            error=ToolErrorV1(
                code="revision_conflict", message="current revision is 4"
            ),
        )
    )
    dispatcher = LearningDispatcher(
        gateways={LearningToolName.COURSE_CREATE: gateway},
        receipts=InMemoryReceiptStore(),
    )
    result = dispatcher.dispatch(_request())

    assert result.state == "rejected"
    assert result.error and result.error.code == "revision_conflict"
    assert gateway.readback_calls == 0
