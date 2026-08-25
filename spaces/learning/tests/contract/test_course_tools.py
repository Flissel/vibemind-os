from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from spaces.learning.bridge.dispatcher import (
    ApplicationOutcomeV1,
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
from spaces.learning.mcp import server
from spaces.learning.services.db.models import Base
from spaces.learning.services.db.repository import SqlReceiptStore


COURSE_ID = UUID("00000000-0000-0000-0000-000000000101")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000103")


def _event(event_type: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "event_type": event_type,
        "invocation_id": str(uuid4()),
        "correlation_id": str(uuid4()),
        "actor": {"actor_id": "local-owner", "actor_type": "local_user"},
        "payload": {},
    }
    values.update(overrides)
    return values


def _call(
    dispatcher: LearningDispatcher, name: str, arguments: dict[str, object]
) -> dict:
    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        dispatcher=dispatcher,
    )
    assert response is not None
    return response


def _payload(response: dict) -> dict:
    import json

    return json.loads(response["result"]["content"][0]["text"])


@dataclass
class RecordingLearnHouseGateway:
    backend_failure: bool = False
    readback_failure: bool = False
    revision_conflict: bool = False
    execute_calls: int = 0
    readback_calls: int = 0
    call_order: list[str] = field(default_factory=list)

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        self.execute_calls += 1
        self.call_order.append("execute")
        if self.backend_failure:
            raise RuntimeError("Bearer upstream-secret")
        if self.revision_conflict:
            return ApplicationOutcomeV1(
                state="rejected",
                error=ToolErrorV1(
                    code="revision_conflict", message="current revision is 5"
                ),
            )
        aggregate, result = self._success(request.tool)
        return ApplicationOutcomeV1(
            state="completed", aggregate=aggregate, result=result
        )

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None:
        self.readback_calls += 1
        self.call_order.append("readback")
        if self.readback_failure or outcome.aggregate is None:
            return None
        return TruthReadbackV1(
            invocation_id=request.event.invocation_id,
            correlation_id=request.event.correlation_id,
            owner="learnhouse",
            terminal_state="completed",
            aggregate=outcome.aggregate,
            evidence=EvidenceRefV1(
                owner="learnhouse",
                evidence_id=(
                    f"readback-{outcome.aggregate.aggregate_type}-"
                    f"{outcome.aggregate.aggregate_id}"
                ),
                evidence_type="application_readback",
            ),
        )

    @staticmethod
    def _success(tool: LearningToolName) -> tuple[AggregateRefV1, dict[str, object]]:
        if tool is LearningToolName.COURSE_LIST:
            return (
                AggregateRefV1(
                    aggregate_type="course_collection",
                    aggregate_id="local-learning",
                    revision=5,
                ),
                {
                    "courses": [
                        {
                            "course_id": str(COURSE_ID),
                            "title": "Typed learning",
                            "description": "A semantic course.",
                            "revision": 5,
                        }
                    ]
                },
            )
        if tool is LearningToolName.MATERIAL_IMPORT:
            return (
                AggregateRefV1(
                    aggregate_type="source", aggregate_id=str(SOURCE_ID), revision=5
                ),
                {
                    "source": {
                        "source_id": str(SOURCE_ID),
                        "title": "Source",
                        "revision": 5,
                    }
                },
            )
        return (
            AggregateRefV1(
                aggregate_type="course", aggregate_id=str(COURSE_ID), revision=5
            ),
            {
                "course": {
                    "course_id": str(COURSE_ID),
                    "title": "Typed learning",
                    "description": "A semantic course.",
                    "revision": 5,
                }
            },
        )


def _dispatcher(monkeypatch, gateway: RecordingLearnHouseGateway) -> LearningDispatcher:
    monkeypatch.setattr(server, "LearnHouseClient", lambda: gateway, raising=False)
    return server.build_default_dispatcher()


def test_course_list_returns_only_semantic_data_and_navigates_after_readback(
    monkeypatch,
) -> None:
    gateway = RecordingLearnHouseGateway()
    response = _call(
        _dispatcher(monkeypatch, gateway),
        "learning_course_list",
        _event(
            "learning.course.list",
            payload={"org_slug": "local-learning", "limit": 20},
        ),
    )
    payload = _payload(response)

    assert response["result"]["isError"] is False
    assert payload["state"] == "completed"
    assert payload["result"] == {
        "courses": [
            {
                "course_id": str(COURSE_ID),
                "title": "Typed learning",
                "description": "A semantic course.",
                "revision": 5,
            }
        ]
    }
    assert payload["ui_intent"] == {
        "version": "1",
        "kind": "navigate",
        "aggregate_id": "local-learning",
        "aggregate_revision": 5,
        "route": "/learning",
    }
    assert gateway.call_order == ["execute", "readback"]


def test_course_writes_read_back_before_returning_a_revisioned_intent(
    monkeypatch,
) -> None:
    cases = [
        (
            "learning_course_create",
            _event(
                "learning.course.create",
                idempotency_key="course-create-1",
                payload={
                    "org_id": 1,
                    "title": "Typed learning",
                    "description": "A semantic course.",
                    "about": "Course authoring.",
                },
            ),
            "open_course",
        ),
        (
            "learning_material_import",
            _event(
                "learning.material.import",
                idempotency_key="source-import-1",
                payload={
                    "org_id": 1,
                    "title": "Source",
                    "source_url": "https://example.invalid/source",
                },
            ),
            "navigate",
        ),
        (
            "learning_course_review",
            _event(
                "learning.course.review",
                course_id=str(COURSE_ID),
                expected_revision=4,
                idempotency_key="course-review-1",
                payload={"review": "approved"},
            ),
            "navigate",
        ),
        (
            "learning_course_publish",
            _event(
                "learning.course.publish",
                course_id=str(COURSE_ID),
                expected_revision=4,
                confirmation={"confirmed": True, "approval_ref": "publish-approved"},
                idempotency_key="course-publish-1",
            ),
            "navigate",
        ),
    ]

    for tool, event, expected_intent in cases:
        gateway = RecordingLearnHouseGateway()
        response = _call(_dispatcher(monkeypatch, gateway), tool, event)
        payload = _payload(response)

        assert response["result"]["isError"] is False
        assert payload["state"] == "completed"
        assert payload["evidence"]["evidence_type"] == "application_readback"
        assert payload["ui_intent"]["kind"] == expected_intent
        assert gateway.call_order == ["execute", "readback"]


def test_publish_requires_confirmation_and_current_revision_before_backend_call(
    monkeypatch,
) -> None:
    gateway = RecordingLearnHouseGateway()
    response = _call(
        _dispatcher(monkeypatch, gateway),
        "learning_course_publish",
        _event(
            "learning.course.publish",
            course_id=str(COURSE_ID),
            idempotency_key="course-publish-1",
        ),
    )

    assert response["result"]["isError"] is True
    assert _payload(response) == {"error": "invalid_arguments"}
    assert gateway.execute_calls == 0
    assert gateway.readback_calls == 0


def test_duplicate_course_write_returns_the_original_durable_receipt_without_mutation(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'learning.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    gateway = RecordingLearnHouseGateway()
    request = ToolRequestV1(
        tool=LearningToolName.COURSE_CREATE,
        event=EventEnvelopeV1(
            event_type=LearningEventType.COURSE_CREATE,
            invocation_id=uuid4(),
            correlation_id=uuid4(),
            actor=ActorV1(actor_id="local-owner", actor_type="local_user"),
            idempotency_key="course-create-1",
            payload={
                "org_id": 1,
                "title": "Typed learning",
                "description": "A semantic course.",
                "about": "Course authoring.",
            },
        ),
    )
    first_dispatcher = LearningDispatcher(
        gateways={LearningToolName.COURSE_CREATE: gateway},
        receipts=SqlReceiptStore(session_factory),
    )
    first = first_dispatcher.dispatch(request)
    replay = request.model_copy(
        update={
            "event": request.event.model_copy(
                update={"invocation_id": uuid4(), "correlation_id": uuid4()}
            )
        }
    )
    second = LearningDispatcher(
        gateways={LearningToolName.COURSE_CREATE: gateway},
        receipts=SqlReceiptStore(session_factory),
    ).dispatch(replay)

    assert first.state == "completed"
    assert second.model_dump() == first.model_dump()
    assert gateway.execute_calls == 1
    assert gateway.readback_calls == 1


def test_course_revision_conflict_is_returned_without_readback_or_intent(
    monkeypatch,
) -> None:
    gateway = RecordingLearnHouseGateway(revision_conflict=True)
    response = _call(
        _dispatcher(monkeypatch, gateway),
        "learning_course_review",
        _event(
            "learning.course.review",
            course_id=str(COURSE_ID),
            expected_revision=4,
            idempotency_key="course-review-1",
            payload={"review": "approved"},
        ),
    )
    payload = _payload(response)

    assert response["result"]["isError"] is True
    assert payload["state"] == "rejected"
    assert payload["error"]["code"] == "revision_conflict"
    assert "ui_intent" not in payload
    assert gateway.call_order == ["execute"]


def test_course_backend_failure_is_typed_and_never_emits_an_unread_intent(
    monkeypatch,
) -> None:
    gateway = RecordingLearnHouseGateway(backend_failure=True)
    response = _call(
        _dispatcher(monkeypatch, gateway),
        "learning_course_create",
        _event(
            "learning.course.create",
            idempotency_key="course-create-1",
            payload={
                "org_id": 1,
                "title": "Typed learning",
                "description": "A semantic course.",
                "about": "Course authoring.",
            },
        ),
    )
    payload = _payload(response)

    assert response["result"]["isError"] is True
    assert payload["state"] == "unavailable"
    assert payload["error"]["code"] == "learning_backend_unavailable"
    assert "upstream-secret" not in str(payload)
    assert "ui_intent" not in payload
    assert gateway.call_order == ["execute"]
