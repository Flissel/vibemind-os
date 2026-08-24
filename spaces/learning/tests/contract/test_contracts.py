from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from spaces.learning.contracts.events import (
    EVENT_TOOL_MAP,
    LearningEventType,
    LearningToolName,
)
from spaces.learning.contracts.mcp_models import (
    ActorV1,
    ConfirmationV1,
    EventEnvelopeV1,
    ToolRequestV1,
)
from spaces.learning.contracts.outcomes import (
    AggregateRefV1,
    EvidenceRefV1,
    ToolResultV1,
    TruthReadbackV1,
)
from spaces.learning.contracts.ui_intents import (
    OpenCanvasIntentV1,
    OpenCourseIntentV1,
    ShowErrorIntentV1,
    UiIntentV1,
    validate_ui_intent,
)


SNAPSHOT_DIR = Path(__file__).with_name("schemas")

EXPECTED_EVENTS = {
    "learning.status",
    "learning.course.list",
    "learning.course.create",
    "learning.course.open",
    "learning.material.import",
    "learning.course.generate",
    "learning.generation.status",
    "learning.course.review",
    "learning.course.publish",
    "learning.chapter.open",
    "learning.session.start",
    "learning.task.next",
    "learning.task.answer",
    "learning.hint.request",
    "learning.tutor.ask",
    "learning.progress.show",
    "learning.canvas.open",
    "learning.canvas.save",
    "learning.canvas.hint",
    "learning.canvas.submit",
    "learning.canvas.review",
}

EXPECTED_TOOLS = {
    "learning_status",
    "learning_course_list",
    "learning_course_create",
    "learning_course_open",
    "learning_material_import",
    "learning_course_generate",
    "learning_generation_status",
    "learning_course_review",
    "learning_course_publish",
    "learning_chapter_open",
    "learning_session_start",
    "learning_task_next",
    "learning_task_answer",
    "learning_hint_request",
    "learning_tutor_ask",
    "learning_progress_show",
    "learning_canvas_open",
    "learning_canvas_save",
    "learning_canvas_hint",
    "learning_canvas_submit",
    "learning_canvas_review",
}


def _event(
    event_type: LearningEventType = LearningEventType.COURSE_CREATE,
    **overrides: object,
) -> EventEnvelopeV1:
    values: dict[str, object] = {
        "event_type": event_type,
        "invocation_id": uuid4(),
        "correlation_id": uuid4(),
        "actor": ActorV1(actor_id="local-owner", actor_type="local_user"),
        "idempotency_key": "course-create-1",
        "payload": {"title": "AI Safety"},
    }
    values.update(overrides)
    return EventEnvelopeV1.model_validate(values)


def test_event_and_tool_catalogs_are_exact_and_bijective() -> None:
    assert {item.value for item in LearningEventType} == EXPECTED_EVENTS
    assert {item.value for item in LearningToolName} == EXPECTED_TOOLS
    assert set(EVENT_TOOL_MAP) == set(LearningEventType)
    assert set(EVENT_TOOL_MAP.values()) == set(LearningToolName)


def test_models_reject_extra_fields_and_mismatched_tool_event() -> None:
    event = _event()
    with pytest.raises(ValidationError):
        EventEnvelopeV1.model_validate({**event.model_dump(), "surprise": True})
    with pytest.raises(ValidationError, match="does not match event"):
        ToolRequestV1(tool="learning_status", event=event)


@pytest.mark.parametrize(
    "event_type",
    [
        LearningEventType.COURSE_CREATE,
        LearningEventType.MATERIAL_IMPORT,
        LearningEventType.COURSE_GENERATE,
        LearningEventType.COURSE_REVIEW,
        LearningEventType.SESSION_START,
        LearningEventType.TASK_ANSWER,
        LearningEventType.CANVAS_SAVE,
        LearningEventType.CANVAS_SUBMIT,
        LearningEventType.CANVAS_REVIEW,
    ],
)
def test_write_events_require_an_idempotency_key(event_type: LearningEventType) -> None:
    with pytest.raises(ValidationError, match="idempotency_key"):
        _event(event_type, idempotency_key=None)


def test_publish_requires_confirmation_and_expected_revision() -> None:
    with pytest.raises(ValidationError, match="confirmation"):
        _event(LearningEventType.COURSE_PUBLISH, expected_revision=4)
    with pytest.raises(ValidationError, match="expected_revision"):
        _event(
            LearningEventType.COURSE_PUBLISH,
            confirmation=ConfirmationV1(confirmed=True, approval_ref="approval-1"),
        )

    event = _event(
        LearningEventType.COURSE_PUBLISH,
        expected_revision=4,
        confirmation=ConfirmationV1(confirmed=True, approval_ref="approval-1"),
    )
    assert event.expected_revision == 4


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "not-allowed"},
        {"nested": {"provider": "openai"}},
        {"file_path": "C:/private/source.pdf"},
        {"source": "file:///private/source.pdf"},
        {"source": "C:\\private\\source.pdf"},
        {"source": "/home/user/private/source.pdf"},
    ],
)
def test_public_event_boundary_rejects_secrets_and_host_paths(payload: object) -> None:
    with pytest.raises(ValidationError, match="forbidden public payload"):
        _event(payload=payload)


def test_terminal_success_requires_aggregate_and_truth_evidence() -> None:
    invocation_id = uuid4()
    correlation_id = uuid4()
    with pytest.raises(ValidationError, match="completed results require"):
        ToolResultV1(
            invocation_id=invocation_id,
            correlation_id=correlation_id,
            state="completed",
            result={"course_id": "course-1"},
        )

    aggregate = AggregateRefV1(
        aggregate_type="course", aggregate_id="course-1", revision=3
    )
    evidence = EvidenceRefV1(
        owner="learnhouse", evidence_id="readback-1", evidence_type="application_readback"
    )
    result = ToolResultV1(
        invocation_id=invocation_id,
        correlation_id=correlation_id,
        state="completed",
        aggregate=aggregate,
        evidence=evidence,
        result={"course_id": "course-1"},
        ui_intent=OpenCourseIntentV1(
            aggregate_id="course-1",
            aggregate_revision=3,
            course_id=uuid4(),
        ),
    )
    assert result.aggregate.revision == 3

    readback = TruthReadbackV1(
        invocation_id=invocation_id,
        correlation_id=correlation_id,
        owner="learnhouse",
        terminal_state="completed",
        aggregate=aggregate,
        evidence=evidence,
    )
    assert readback.evidence.owner == readback.owner


def test_ui_intents_are_discriminated_and_reject_unsafe_routes() -> None:
    intent = validate_ui_intent(
        {
            "kind": "open_canvas",
            "aggregate_id": "submission-1",
            "aggregate_revision": 2,
            "activity_id": str(uuid4()),
            "canvas_revision": 7,
        }
    )
    assert isinstance(intent, OpenCanvasIntentV1)
    assert isinstance(intent, UiIntentV1)

    with pytest.raises(ValidationError):
        validate_ui_intent(
            {
                "kind": "navigate",
                "aggregate_id": "course-1",
                "aggregate_revision": 1,
                "route": "file:///C:/private",
            }
        )

    error = ShowErrorIntentV1(
        aggregate_id="invocation-1",
        aggregate_revision=0,
        error_code="learning_unavailable",
    )
    assert error.kind == "show_error"


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("event-envelope-v1.json", EventEnvelopeV1),
        ("tool-request-v1.json", ToolRequestV1),
        ("tool-result-v1.json", ToolResultV1),
        ("truth-readback-v1.json", TruthReadbackV1),
    ],
)
def test_json_schemas_match_committed_snapshots(name: str, model: object) -> None:
    expected = json.loads((SNAPSHOT_DIR / name).read_text(encoding="utf-8"))
    assert model.model_json_schema() == expected
