from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

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
    TruthReadbackV1,
)
from spaces.learning.deployment.health import HealthService


@dataclass
class _UnavailableBoundary:
    name: str

    def execute(self, request) -> ApplicationOutcomeV1:
        del request
        raise RuntimeError(f"{self.name} unavailable")

    def readback(self, request, outcome):
        del request, outcome
        raise AssertionError("unavailable boundary cannot produce readback")


def _request(tool: LearningToolName, event_type: LearningEventType) -> ToolRequestV1:
    expected_revision = 1 if event_type in {
        LearningEventType.COURSE_GENERATE,
        LearningEventType.CANVAS_SAVE,
    } else None
    course_id = uuid4() if event_type is LearningEventType.COURSE_GENERATE else None
    session_id = uuid4() if event_type is LearningEventType.CANVAS_SAVE else None
    return ToolRequestV1(
        tool=tool,
        event=EventEnvelopeV1(
            event_type=event_type,
            invocation_id=uuid4(),
            correlation_id=uuid4(),
            actor=ActorV1(actor_id="failure-audit", actor_type="system"),
            course_id=course_id,
            session_id=session_id,
            expected_revision=expected_revision,
            idempotency_key=f"failure-{tool.value}",
            payload={"title": "Failure audit"},
        ),
    )


@pytest.mark.parametrize(
    ("boundary", "tool", "event_type"),
    [
        ("learnhouse", LearningToolName.COURSE_CREATE, LearningEventType.COURSE_CREATE),
        ("openfang", LearningToolName.COURSE_GENERATE, LearningEventType.COURSE_GENERATE),
        ("penecho", LearningToolName.CANVAS_SAVE, LearningEventType.CANVAS_SAVE),
    ],
)
def test_unavailable_application_boundaries_fail_closed(
    boundary: str,
    tool: LearningToolName,
    event_type: LearningEventType,
) -> None:
    request = _request(tool, event_type)
    dispatcher = LearningDispatcher(
        gateways={tool: _UnavailableBoundary(boundary)},
        receipts=InMemoryReceiptStore(),
    )

    result = dispatcher.dispatch(request)

    assert result.state == "unavailable"
    assert result.error is not None
    assert result.error.code == "learning_backend_unavailable"
    assert result.evidence is None


@pytest.mark.parametrize("failed_dependency", ["qdrant", "redis", "embedding"])
def test_infrastructure_failure_is_degraded_not_live(
    failed_dependency: str,
) -> None:
    probes = {
        name: (lambda selected=name: selected != failed_dependency)
        for name in ("postgres", "redis", "qdrant", "embedding")
    }
    health = HealthService(
        dependency_probes=probes,
        migration_probe=lambda: True,
        structural_probe=lambda: [],
        golden_path_probe=lambda: None,
    )

    readiness = health.readiness()

    assert readiness["status"] == "degraded"
    assert readiness["components"][failed_dependency] == "unavailable"
    assert readiness["live_claim"] is False
    assert health.golden_path()["status"] == "unverified"


def test_ui_bridge_failure_does_not_change_backend_truth() -> None:
    class _UiFailure:
        def try_deliver(self, intent, *, correlation_id):
            del intent, correlation_id
            raise RuntimeError("renderer unavailable")

    from spaces.learning.contracts.ui_intents import NavigateIntentV1

    aggregate = AggregateRefV1(
        aggregate_type="course", aggregate_id="course-1", revision=1
    )

    class _CompletedBoundary:
        def execute(self, request) -> ApplicationOutcomeV1:
            del request
            return ApplicationOutcomeV1(
                state="completed",
                aggregate=aggregate,
                result={"course_id": "course-1"},
                ui_intent=NavigateIntentV1(
                    aggregate_id="course-1", aggregate_revision=1, route="/learning"
                ),
            )

        def readback(self, request, outcome) -> TruthReadbackV1:
            del outcome
            return TruthReadbackV1(
                invocation_id=request.event.invocation_id,
                correlation_id=request.event.correlation_id,
                owner="learnhouse",
                terminal_state="completed",
                aggregate=aggregate,
                evidence=EvidenceRefV1(
                    owner="learnhouse",
                    evidence_id="failure-matrix-readback",
                    evidence_type="application_readback",
                ),
            )

    dispatcher = LearningDispatcher(
        gateways={LearningToolName.COURSE_CREATE: _CompletedBoundary()},
        receipts=InMemoryReceiptStore(),
        ui_delivery=_UiFailure(),
    )

    result = dispatcher.dispatch(
        _request(LearningToolName.COURSE_CREATE, LearningEventType.COURSE_CREATE)
    )

    assert result.state == "completed"
    assert result.evidence is not None
    assert result.ui_intent is not None
    assert result.ui_delivery is not None
    assert result.ui_delivery.delivered is False
    assert result.ui_delivery.error_code == "unexpected_delivery_error"
