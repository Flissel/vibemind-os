from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.bridge.dispatcher import InMemoryReceiptStore, LearningDispatcher
from spaces.learning.bridge.penecho_client import PenEchoExportV1, PenEchoSessionV1
from spaces.learning.contracts.events import LearningEventType, LearningToolName
from spaces.learning.contracts.mcp_models import ActorV1, ConfirmationV1, EventEnvelopeV1, ToolRequestV1
from spaces.learning.contracts.penecho import (
    CanvasArtifactV1, CanvasDocumentV1, CanvasHelpPolicyV1, CanvasObjectV1,
    CanvasSaveV1, PenEchoLaunchContextV1, SourceReferenceV1,
)
from spaces.learning.mcp.tools.canvas import (
    CanvasAdaptiveSubmissionService, CanvasGateway, build_canvas_gateways,
)
from spaces.learning.services.adaptive_engine.models import (
    AdaptiveConcept, AdaptiveItem, ItemConcept, LearningEvaluation,
    LearningReviewItem, MasteryEvidence,
)
from spaces.learning.services.adaptive_engine.repository import SessionInput
from spaces.learning.services.adaptive_engine.session_service import (
    AdaptiveSessionService,
    AnswerCommand,
)
from spaces.learning.services.course_factory.model_gateway import GatewayResult
from spaces.learning.services.db.models import Base
from spaces.learning.services.evaluation.canvas_artifacts import CanvasArtifactStore, encode_canvas_document
from spaces.learning.services.evaluation.schemas import CriterionEvaluation, RubricModelOutput


PNG = b"\x89PNG\r\n\x1a\ncanvas-golden-path"


class _Gateway:
    async def generate(self, invocation, output_model):
        assert invocation.image_inputs[0].content == PNG
        return GatewayResult(
            output=output_model.model_validate(RubricModelOutput(
                schema_version="rubric-evaluation-v1",
                criteria=[CriterionEvaluation(
                    criterion_id="authority", awarded_points=1,
                    feedback="The authority boundary is explicit.",
                    source_refs=["source://authority/1"],
                )],
                confidence=0.9, misconception_tags=[],
            )),
            evidence_ref="openfang://completion/canvas-golden-1",
        )


class _Tutor:
    async def ask(self, **kwargs):
        return {
            "answer": "Pruefe, ob die Autoritaetsgrenze verbunden ist.",
            "source_refs": ["source://authority/1"],
            "next_step": "Markiere den Readback.",
        }


class _PenEcho:
    def __init__(self, launch: PenEchoLaunchContextV1, document: CanvasDocumentV1) -> None:
        self.launch, self.document, self.revision = launch, document, 0

    def open_session(self, session_id, session_ref):
        assert session_id == self.launch.session_id
        assert session_ref == self.launch.bridge_session_ref
        return PenEchoSessionV1(
            id=session_id, revision=self.revision, launch=self.launch,
            document=self.document, artifacts=(),
        )

    def save_session(self, session_id, session_ref, save: CanvasSaveV1, *, snapshot_png_base64):
        assert base64.b64decode(snapshot_png_base64) == PNG
        assert save.expected_revision == self.revision
        self.revision += 1
        self.document = save.document.model_copy(update={"revision": self.revision})
        return self.open_session(session_id, session_ref)

    def export_session(self, session_id, session_ref):
        self.open_session(session_id, session_ref)
        structured = encode_canvas_document(self.document)
        return PenEchoExportV1(
            id=session_id, revision=self.revision, document=self.document,
            artifacts=(
                CanvasArtifactV1(
                    artifact_id=uuid4(), artifact_kind="structured_canvas",
                    media_type="application/json", sha256=hashlib.sha256(structured).hexdigest(),
                    size_bytes=len(structured), locator="artifact://penecho/structured.json",
                    canvas_revision=self.revision,
                ),
                CanvasArtifactV1(
                    artifact_id=uuid4(), artifact_kind="rendered_snapshot",
                    media_type="image/png", sha256=hashlib.sha256(PNG).hexdigest(),
                    size_bytes=len(PNG), locator="artifact://penecho/snapshot.png",
                    canvas_revision=self.revision,
                ),
            ),
            snapshot_png_base64=base64.b64encode(PNG).decode(),
        )


def _event(
    event_type, *, session_id, course_id, payload, expected_revision=None,
    key=None, confirmation=None,
):
    return EventEnvelopeV1(
        event_type=event_type, invocation_id=uuid4(), correlation_id=uuid4(),
        actor=ActorV1(actor_id="student-1", actor_type="local_user"),
        course_id=course_id, session_id=session_id, payload=payload,
        expected_revision=expected_revision, idempotency_key=key,
        confirmation=confirmation,
    )


def _call(dispatcher, tool, event):
    return dispatcher.dispatch(ToolRequestV1(tool=tool, event=event))


def test_canvas_open_draw_save_reload_hint_submit_review_and_next_task(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'canvas-e2e.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, connection_record) -> None:
        del connection_record
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    course_id, concept_id = uuid4(), uuid4()
    canvas_item, next_item = UUID(int=1), UUID(int=2)
    with factory() as db, db.begin():
        db.add(AdaptiveConcept(
            id=str(concept_id), course_id=str(course_id), course_revision=1,
            concept_key="authority.boundary", title="Authority boundary",
        ))
        db.add_all([
            AdaptiveItem(
                id=str(canvas_item), course_id=str(course_id), course_revision=1,
                activity_id=str(uuid4()), item_type="penecho_canvas", difficulty=0.5,
                quality_status="approved", expected_answer={"text": "Show authority and readback."},
                scoring_config={
                    "prompt": "Visualisiere die Autoritaetskette.", "options": [],
                    "source_refs": ["source://authority/1"], "hints": ["Pruefe die Grenze."],
                    "representation": "penecho_canvas", "remediation_tags": [],
                    "model_version": "openfang-canvas-v1", "prompt_version": "canvas-rubric-v1",
                    "source_version": "course-sources-r1",
                    "presentation": {"canvas_href": "http://127.0.0.1:3888"},
                },
                rubric=[{"criterion_id": "authority", "description": "Shows authority", "points": 1}],
            ),
            AdaptiveItem(
                id=str(next_item), course_id=str(course_id), course_revision=1,
                activity_id=str(uuid4()), item_type="single_choice", difficulty=0.9,
                quality_status="approved", expected_answer={"choice_id": "yes"},
                scoring_config={
                    "prompt": "Welcher Pfad ist autorisiert?", "options": [{"id": "yes", "label": "OpenFang"}],
                    "source_refs": ["source://authority/1"], "hints": ["Nutze den Vertrag."],
                    "representation": "quiz", "remediation_tags": [],
                }, rubric=[],
            ),
        ])
        db.add(ItemConcept(
            item_id=str(canvas_item), concept_id=str(concept_id),
            course_id=str(course_id), course_revision=1,
        ))
        db.add(ItemConcept(
            item_id=str(next_item), concept_id=str(concept_id),
            course_id=str(course_id), course_revision=1,
        ))
    adaptive = AdaptiveSessionService(
        factory,
        external_rubric_types=frozenset({"penecho_canvas"}),
    )
    started = adaptive.start(SessionInput(
        course_id=str(course_id), course_revision=1, actor_id="student-1",
        mode="training", blueprint={"max_attempts": 2},
    ))
    assert started.task and started.task.id == str(canvas_item)
    with pytest.raises(ValueError, match="no admitted evaluator"):
        adaptive.answer(AnswerCommand(
            session_id=started.session_id,
            actor_id="student-1",
            expected_session_revision=started.session_revision,
            answer={"kind": "penecho_canvas"},
        ))
    activity_id = UUID(started.task.id)
    launch = PenEchoLaunchContextV1(
        activity_id=activity_id, session_id=UUID(started.session_id), course_id=course_id,
        course_revision=1, actor_id="student-1", mode="training",
        help_policy=CanvasHelpPolicyV1(
            hint_mode="selected_region", tutor_allowed=True, max_hints=2,
        ),
        source_refs=(SourceReferenceV1(
            source_id="authority", revision=1, locator="source://authority/1", title="Authority",
        ),),
        penecho_origin="http://127.0.0.1:3888", bridge_session_ref="bridge-e2e",
    )
    document = CanvasDocumentV1(
        project_id=uuid4(), canvas_id=uuid4(), revision=0, background="white", objects=(),
    )
    penecho = _PenEcho(launch, document)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    evaluations = CanvasAdaptiveSubmissionService(session_factory=factory, gateway=_Gateway())
    gateway = CanvasGateway(
        penecho=penecho, artifacts=CanvasArtifactStore(artifact_root=artifact_root),
        evaluations=evaluations, tutor=_Tutor(),
    )
    dispatcher = LearningDispatcher(
        gateways=build_canvas_gateways(gateway), receipts=InMemoryReceiptStore(),
    )
    common = {"session_ref": "bridge-e2e"}

    opened = _call(dispatcher, LearningToolName.CANVAS_OPEN, _event(
        LearningEventType.CANVAS_OPEN, session_id=launch.session_id, course_id=course_id,
        payload={**common, "activity_id": str(activity_id)},
    ))
    assert opened.state == "completed"

    drawn = document.model_copy(update={"objects": (CanvasObjectV1(
        object_id="authority", object_type="text", x=10, y=10,
        width=240, height=40, rotation=0, text="OpenFang authorizes provider execution",
    ),)})
    saved = _call(dispatcher, LearningToolName.CANVAS_SAVE, _event(
        LearningEventType.CANVAS_SAVE, session_id=launch.session_id, course_id=course_id,
        expected_revision=0, key="canvas-save-e2e",
        payload={**common, "document": drawn.model_dump(mode="json"),
                 "snapshot_png_base64": base64.b64encode(PNG).decode()},
    ))
    assert saved.state == "completed" and saved.aggregate and saved.aggregate.revision == 1

    reloaded = _call(dispatcher, LearningToolName.CANVAS_OPEN, _event(
        LearningEventType.CANVAS_OPEN, session_id=launch.session_id, course_id=course_id,
        payload={**common, "activity_id": str(activity_id)},
    ))
    assert reloaded.result and reloaded.result["canvas"]["document"]["objects"]

    hinted = _call(dispatcher, LearningToolName.CANVAS_HINT, _event(
        LearningEventType.CANVAS_HINT, session_id=launch.session_id, course_id=course_id,
        expected_revision=1,
        payload={**common, "selection": {"object_ids": ["authority"]}},
    ))
    assert hinted.state == "completed" and hinted.result and hinted.result["hint"]["source_refs"]

    submitted = _call(dispatcher, LearningToolName.CANVAS_SUBMIT, _event(
        LearningEventType.CANVAS_SUBMIT, session_id=launch.session_id, course_id=course_id,
        expected_revision=1, key="canvas-submit-e2e",
        confirmation=ConfirmationV1(confirmed=True, approval_ref="canvas-approval-1"),
        payload={**common, "submission_id": str(uuid4()),
                 "adaptive_session_revision": started.session_revision},
    ))
    assert submitted.state == "completed"
    assert submitted.result and submitted.result["session"]["task"]["id"] == str(next_item)
    evaluation_id = submitted.result["evaluation_id"]

    reviewed = _call(dispatcher, LearningToolName.CANVAS_REVIEW, _event(
        LearningEventType.CANVAS_REVIEW, session_id=launch.session_id, course_id=course_id,
        key="canvas-review-e2e", payload={**common, "evaluation_id": evaluation_id},
    ))
    assert reviewed.state == "completed"
    assert reviewed.result and reviewed.result["review"]["review_status"] == "not_required"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(LearningEvaluation)) == 1
        assert db.scalar(select(func.count()).select_from(MasteryEvidence)) == 1
        assert db.scalar(select(func.count()).select_from(LearningReviewItem)) == 0
    engine.dispose()
