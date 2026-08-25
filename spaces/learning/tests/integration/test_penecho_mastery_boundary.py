from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.contracts.penecho import (
    CanvasArtifactV1, CanvasDocumentV1, CanvasObjectV1, CanvasSubmissionV1,
    SourceReferenceV1,
)
from spaces.learning.services.adaptive_engine.mastery import MasteryService
from spaces.learning.services.adaptive_engine.models import (
    AdaptiveConcept, AdaptiveItem, ConceptMastery, ItemConcept, LearningEvaluation,
    LearningResponse, LearningReviewItem, MasteryEvidence,
)
from spaces.learning.services.adaptive_engine.repository import (
    AdaptiveRepository, AttemptInput, SessionInput,
)
from spaces.learning.services.course_factory.model_gateway import GatewayResult
from spaces.learning.services.db.models import Base
from spaces.learning.services.evaluation.canvas_artifacts import encode_canvas_document
from spaces.learning.services.evaluation.penecho import CanvasEvaluationRequest, PenEchoEvaluator
from spaces.learning.services.evaluation.review_queue import SqlEvaluationRecorder, SqlReviewQueue
from spaces.learning.services.evaluation.schemas import CriterionEvaluation, RubricCriterion, RubricModelOutput


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture()
def store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'canvas-mastery.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, connection_record) -> None:
        del connection_record
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class _Gateway:
    def __init__(self, confidence: float) -> None:
        self.confidence = confidence

    async def generate(self, invocation, output_model):
        assert invocation.image_inputs[0].content == PNG
        output = RubricModelOutput(
            schema_version="rubric-evaluation-v1",
            criteria=[CriterionEvaluation(
                criterion_id="authority", awarded_points=0.8,
                feedback="The boundary is visible.", source_refs=["source://authority/3"],
            )],
            confidence=self.confidence,
            misconception_tags=[],
        )
        return GatewayResult(
            output=output_model.model_validate(output),
            evidence_ref="openfang://completion/canvas-sql-1",
        )


class _SqlMastery:
    def __init__(self, store) -> None:
        self._service = MasteryService(store)
        self.results = []

    def apply(self, decision) -> None:
        self.results.append(self._service.apply_evaluation(
            actor_id="student-1", evaluation_id=decision.evaluation_id,
        ))


def _seed(store):
    course_id, concept_id, item_id = str(uuid4()), str(uuid4()), str(uuid4())
    with store() as session, session.begin():
        session.add(AdaptiveConcept(
            id=concept_id, course_id=course_id, course_revision=1,
            concept_key="authority.boundary", title="Authority boundary",
        ))
        session.add(AdaptiveItem(
            id=item_id, course_id=course_id, course_revision=1,
            activity_id="activity-canvas", item_type="case", difficulty=0.7,
            quality_status="approved", expected_answer={"text": "Show the boundary."},
            scoring_config={"mode": "rubric"},
            rubric=[{"criterion_id": "authority", "points": 1}],
        ))
        session.add(ItemConcept(
            item_id=item_id, concept_id=concept_id, course_id=course_id, course_revision=1,
        ))
    repository = AdaptiveRepository(store)
    learning_session = repository.start_session(SessionInput(
        course_id=course_id, course_revision=1, actor_id="student-1",
        mode="training", blueprint={},
    ))
    attempt = repository.append_attempt(
        learning_session.id, expected_session_revision=1,
        value=AttemptInput(item_id=item_id, selection_reason={"kind": "canvas"}),
    )
    response_id = str(uuid4())
    with store() as session, session.begin():
        session.add(LearningResponse(
            id=response_id, attempt_id=attempt.id, response_revision=1,
            answer={"kind": "penecho_canvas"}, answer_hash="f" * 64,
        ))
    return response_id, concept_id


def _request(response_id: str) -> CanvasEvaluationRequest:
    document = CanvasDocumentV1(
        project_id=uuid4(), canvas_id=uuid4(), revision=1, background="white",
        objects=(CanvasObjectV1(
            object_id="authority", object_type="text", x=0, y=0,
            width=200, height=40, rotation=0, text="OpenFang authority",
        ),),
    )
    structured = encode_canvas_document(document)
    return CanvasEvaluationRequest(
        evaluation_id=str(uuid4()), response_id=response_id,
        submission_id=str(uuid4()), actor_id="student-1",
        submission=CanvasSubmissionV1(
            document=document,
            structured_artifact=CanvasArtifactV1(
                artifact_id=uuid4(), artifact_kind="structured_canvas",
                media_type="application/json", sha256=hashlib.sha256(structured).hexdigest(),
                size_bytes=len(structured), locator="artifact://learning/canvas/structured.json",
                canvas_revision=1,
            ),
            rendered_snapshot=CanvasArtifactV1(
                artifact_id=uuid4(), artifact_kind="rendered_snapshot",
                media_type="image/png", sha256=hashlib.sha256(PNG).hexdigest(),
                size_bytes=len(PNG), locator="artifact://learning/canvas/snapshot.png",
                canvas_revision=1,
            ),
        ),
        expected_answer="Show the authority boundary.",
        rubric=(RubricCriterion(
            criterion_id="authority", description="Shows the authority boundary", points=1,
        ),),
        source_refs=(SourceReferenceV1(
            source_id="authority", revision=3, locator="source://authority/3",
            title="Authority contract",
        ),),
        model_version="openfang-canvas-v1", prompt_version="canvas-rubric-v1",
        source_version="course-sources-r3",
    )


@pytest.mark.asyncio
async def test_low_confidence_persists_review_and_zero_mastery(store) -> None:
    response_id, _ = _seed(store)
    mastery, request = _SqlMastery(store), _request(response_id)
    evaluator = PenEchoEvaluator(
        gateway=_Gateway(0.649), recorder=SqlEvaluationRecorder(store),
        review_queue=SqlReviewQueue(store), mastery=mastery,
    )

    result = await evaluator.evaluate(request, snapshot_png=PNG)

    assert result.accepted is False
    assert mastery.results == []
    with store() as session:
        evaluation = session.get(LearningEvaluation, request.evaluation_id)
        assert evaluation is not None
        assert evaluation.evaluator_versions["model"] == "openfang-canvas-v1"
        assert evaluation.source_refs == ["source://authority/3"]
        assert session.scalar(select(func.count()).select_from(MasteryEvidence)) == 0
        assert session.scalar(select(func.count()).select_from(LearningReviewItem)) == 1


@pytest.mark.asyncio
async def test_accepted_canvas_replay_applies_mastery_once(store) -> None:
    response_id, concept_id = _seed(store)
    mastery, request = _SqlMastery(store), _request(response_id)
    evaluator = PenEchoEvaluator(
        gateway=_Gateway(0.9), recorder=SqlEvaluationRecorder(store),
        review_queue=SqlReviewQueue(store), mastery=mastery,
    )

    first = await evaluator.evaluate(request, snapshot_png=PNG)
    replay = await evaluator.evaluate(request, snapshot_png=PNG)

    assert first == replay
    assert mastery.results[0].applied is True
    assert mastery.results[1].duplicate is True
    with store() as session:
        state = session.get(ConceptMastery, {"actor_id": "student-1", "concept_id": concept_id})
        assert state is not None
        assert session.scalar(select(func.count()).select_from(MasteryEvidence)) == 1
        assert session.scalar(select(func.count()).select_from(LearningReviewItem)) == 0
