from __future__ import annotations

import base64
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from spaces.learning.bridge.dispatcher import InMemoryReceiptStore, LearningDispatcher
from spaces.learning.bridge.penecho_client import PenEchoRevisionConflict
from spaces.learning.bridge.ui_bridge import UiDeliveryResult
from spaces.learning.contracts.events import LearningEventType, LearningToolName
from spaces.learning.contracts.mcp_models import ActorV1, ConfirmationV1, EventEnvelopeV1, ToolRequestV1
from spaces.learning.contracts.penecho import (
    CanvasDocumentV1, CanvasHelpPolicyV1, CanvasObjectV1,
    PenEchoLaunchContextV1, SourceReferenceV1,
)
from spaces.learning.mcp.tools.canvas import CanvasGateway, build_canvas_gateways


PNG = base64.b64encode(b"\x89PNG\r\n\x1a\ncanvas").decode()
SESSION_ID = UUID("123e4567-e89b-42d3-a456-426614174700")
ACTIVITY_ID = UUID("123e4567-e89b-42d3-a456-426614174701")
COURSE_ID = UUID("123e4567-e89b-42d3-a456-426614174702")


def _launch(mode: str = "training") -> PenEchoLaunchContextV1:
    help_policy = CanvasHelpPolicyV1(
        hint_mode="selected_region" if mode == "training" else "none",
        tutor_allowed=mode == "training",
        max_hints=2 if mode == "training" else 0,
    )
    return PenEchoLaunchContextV1(
        activity_id=ACTIVITY_ID, session_id=SESSION_ID, course_id=COURSE_ID,
        course_revision=1, actor_id="student-1", mode=mode,
        help_policy=help_policy,
        source_refs=(SourceReferenceV1(
            source_id="authority", revision=1, locator="source://authority/1",
            title="Authority",
        ),),
        penecho_origin="http://127.0.0.1:3888", bridge_session_ref="bridge-1",
    )


def _document(revision: int) -> CanvasDocumentV1:
    return CanvasDocumentV1(
        project_id=uuid4(), canvas_id=uuid4(), revision=revision, background="white",
        objects=(CanvasObjectV1(
            object_id="authority", object_type="text", x=0, y=0,
            width=100, height=30, rotation=0, text="OpenFang",
        ),),
    )


class _Session:
    def __init__(self, revision=0, mode="training") -> None:
        self.id, self.revision = SESSION_ID, revision
        self.launch, self.document = _launch(mode), _document(revision)

    def model_dump(self, *, mode):
        assert mode == "json"
        return {
            "version": "1", "id": str(self.id), "revision": self.revision,
            "launch": self.launch.model_dump(mode="json"),
            "document": self.document.model_dump(mode="json"), "artifacts": [],
        }


class _PenEcho:
    def __init__(self, *, conflict: bool = False, mode: str = "training") -> None:
        self.session, self.conflict = _Session(mode=mode), conflict
        self.save_calls = 0

    def open_session(self, session_id, session_ref):
        assert session_id == SESSION_ID and session_ref == "bridge-1"
        return self.session

    def save_session(self, session_id, session_ref, save, *, snapshot_png_base64):
        if self.conflict:
            raise PenEchoRevisionConflict("stale")
        assert save.expected_revision == self.session.revision
        assert snapshot_png_base64 == PNG
        self.save_calls += 1
        self.session = _Session(revision=self.session.revision + 1)
        return self.session


class _Artifacts:
    def import_export(self, export):
        return export.submission


class _Evaluations:
    def has_evaluation(self, evaluation_id: str) -> bool:
        return evaluation_id == "123e4567-e89b-42d3-a456-426614174799"


class _Tutor:
    async def ask(self, **kwargs):
        return {"answer": "Pruefe die markierte Autoritaetsgrenze.", "source_refs": ["source://authority/1"]}


def _event(
    event_type: LearningEventType, *, expected_revision=None, payload=None,
    key=None, confirmation=None, actor_id="student-1",
) -> EventEnvelopeV1:
    return EventEnvelopeV1(
        event_type=event_type, invocation_id=uuid4(), correlation_id=uuid4(),
        actor=ActorV1(actor_id=actor_id, actor_type="local_user"),
        course_id=COURSE_ID, session_id=SESSION_ID, expected_revision=expected_revision,
        idempotency_key=key, confirmation=confirmation, payload=payload or {},
    )


def _dispatch(
    gateway: CanvasGateway,
    event: EventEnvelopeV1,
    *,
    ui_delivery=None,
):
    tool = {
        LearningEventType.CANVAS_OPEN: LearningToolName.CANVAS_OPEN,
        LearningEventType.CANVAS_SAVE: LearningToolName.CANVAS_SAVE,
        LearningEventType.CANVAS_HINT: LearningToolName.CANVAS_HINT,
        LearningEventType.CANVAS_SUBMIT: LearningToolName.CANVAS_SUBMIT,
        LearningEventType.CANVAS_REVIEW: LearningToolName.CANVAS_REVIEW,
    }[event.event_type]
    dispatcher = LearningDispatcher(
        gateways={tool: gateway},
        receipts=InMemoryReceiptStore(),
        ui_delivery=ui_delivery,
    )
    return dispatcher.dispatch(ToolRequestV1(tool=tool, event=event))


def test_canvas_open_emits_only_readback_verified_canvas_intent() -> None:
    gateway = CanvasGateway(penecho=_PenEcho(), artifacts=_Artifacts(), evaluations=_Evaluations(), tutor=_Tutor())
    result = _dispatch(gateway, _event(
        LearningEventType.CANVAS_OPEN,
        payload={"session_ref": "bridge-1", "activity_id": str(ACTIVITY_ID)},
    ))
    assert result.state == "completed"
    assert result.evidence and result.evidence.owner == "penecho"
    assert result.ui_intent and result.ui_intent.kind == "open_canvas"
    assert result.aggregate and result.aggregate.revision == 0


def test_canvas_ui_projection_failure_preserves_verified_application_success() -> None:
    class _UnavailableUi:
        calls = 0

        def try_deliver(self, intent, *, correlation_id):
            self.calls += 1
            return UiDeliveryResult(delivered=False, error_code="transport_error")

    delivery = _UnavailableUi()
    result = _dispatch(
        CanvasGateway(
            penecho=_PenEcho(),
            artifacts=_Artifacts(),
            evaluations=_Evaluations(),
            tutor=_Tutor(),
        ),
        _event(
            LearningEventType.CANVAS_OPEN,
            payload={"session_ref": "bridge-1", "activity_id": str(ACTIVITY_ID)},
        ),
        ui_delivery=delivery,
    )

    assert result.state == "completed"
    assert result.evidence and result.evidence.owner == "penecho"
    assert result.ui_intent and result.ui_intent.kind == "open_canvas"
    assert delivery.calls == 1


@pytest.mark.parametrize("event_type", [LearningEventType.CANVAS_OPEN, LearningEventType.CANVAS_SAVE])
def test_canvas_open_and_save_enforce_launch_actor_ownership(event_type) -> None:
    payload = {"session_ref": "bridge-1", "activity_id": str(ACTIVITY_ID)}
    expected_revision, key = None, None
    if event_type is LearningEventType.CANVAS_SAVE:
        expected_revision, key = 0, "foreign-save"
        payload = {
            "session_ref": "bridge-1",
            "document": _document(0).model_dump(mode="json"),
            "snapshot_png_base64": PNG,
        }
    denied = _dispatch(
        CanvasGateway(
            penecho=_PenEcho(), artifacts=_Artifacts(),
            evaluations=_Evaluations(), tutor=_Tutor(),
        ),
        _event(
            event_type, expected_revision=expected_revision, key=key,
            payload=payload, actor_id="student-2",
        ),
    )
    assert denied.state == "rejected"
    assert denied.error and denied.error.code == "canvas_help_denied"


def test_canvas_save_is_idempotent_and_maps_revision_conflict() -> None:
    penecho = _PenEcho()
    gateway = CanvasGateway(penecho=penecho, artifacts=_Artifacts(), evaluations=_Evaluations(), tutor=_Tutor())
    event = _event(
        LearningEventType.CANVAS_SAVE, expected_revision=0, key="canvas-save-1",
        payload={"session_ref": "bridge-1", "document": _document(0).model_dump(mode="json"), "snapshot_png_base64": PNG},
    )
    tool = LearningToolName.CANVAS_SAVE
    dispatcher = LearningDispatcher(gateways=build_canvas_gateways(gateway), receipts=InMemoryReceiptStore())
    first = dispatcher.dispatch(ToolRequestV1(tool=tool, event=event))
    replay = dispatcher.dispatch(ToolRequestV1(tool=tool, event=event))
    assert first == replay and penecho.save_calls == 1

    conflict = _dispatch(CanvasGateway(
        penecho=_PenEcho(conflict=True), artifacts=_Artifacts(),
        evaluations=_Evaluations(), tutor=_Tutor(),
    ), event.model_copy(update={"idempotency_key": "canvas-save-conflict"}))
    assert conflict.state == "rejected"
    assert conflict.error and conflict.error.code == "canvas_revision_conflict"


def test_canvas_hint_obeys_launch_help_policy() -> None:
    denied = _dispatch(CanvasGateway(
        penecho=_PenEcho(mode="exam"), artifacts=_Artifacts(),
        evaluations=_Evaluations(), tutor=_Tutor(),
    ), _event(
        LearningEventType.CANVAS_HINT, expected_revision=0,
        payload={"session_ref": "bridge-1", "selection": {"object_ids": ["authority"]}},
    ))
    assert denied.state == "rejected"
    assert denied.error and denied.error.code == "canvas_help_denied"


def test_canvas_submit_requires_confirmation_and_revision() -> None:
    with pytest.raises(ValidationError, match="confirmation"):
        _event(
            LearningEventType.CANVAS_SUBMIT, expected_revision=0, key="submit-1",
            payload={"session_ref": "bridge-1"},
        )
    with pytest.raises(ValidationError, match="expected_revision"):
        _event(
            LearningEventType.CANVAS_SUBMIT, key="submit-2",
            confirmation=ConfirmationV1(confirmed=True, approval_ref="approval-1"),
            payload={"session_ref": "bridge-1"},
        )


def test_canvas_dependency_failure_has_no_success_intent() -> None:
    class _Broken(_PenEcho):
        def open_session(self, session_id, session_ref):
            raise RuntimeError("offline")

    result = _dispatch(CanvasGateway(
        penecho=_Broken(), artifacts=_Artifacts(), evaluations=_Evaluations(), tutor=_Tutor(),
    ), _event(
        LearningEventType.CANVAS_OPEN,
        payload={"session_ref": "bridge-1", "activity_id": str(ACTIVITY_ID)},
    ))
    assert result.state == "unavailable"
    assert result.ui_intent is None
