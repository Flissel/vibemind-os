from __future__ import annotations

import base64
import hashlib
from uuid import uuid4

import pytest

from spaces.learning.contracts.penecho import (
    CanvasArtifactV1,
    CanvasDocumentV1,
    CanvasObjectV1,
    CanvasSubmissionV1,
    SourceReferenceV1,
)
from spaces.learning.services.course_factory.model_gateway import (
    GatewayResult,
    GatewaySchemaError,
)
from spaces.learning.services.evaluation.canvas_artifacts import encode_canvas_document
from spaces.learning.services.evaluation.penecho import (
    CanvasEvaluationRequest,
    PenEchoEvaluator,
)
from spaces.learning.services.evaluation.review_queue import InMemoryReviewQueue
from spaces.learning.services.evaluation.schemas import (
    CriterionEvaluation,
    RubricCriterion,
    RubricModelOutput,
)


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _Gateway:
    def __init__(self, outcome: RubricModelOutput | Exception) -> None:
        self.outcome = outcome
        self.invocations = []

    async def generate(self, invocation, output_model):
        self.invocations.append(invocation)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        assert isinstance(self.outcome, output_model)
        return GatewayResult(
            output=self.outcome,
            evidence_ref="openfang://completion/canvas-eval-1",
        )


class _Recorder:
    def __init__(self) -> None:
        self.values = []

    def record(self, value) -> None:
        self.values.append(value)


class _Mastery:
    def __init__(self) -> None:
        self.values = []

    def apply(self, value) -> None:
        self.values.append(value)


def _request() -> CanvasEvaluationRequest:
    document = CanvasDocumentV1(
        project_id=uuid4(),
        canvas_id=uuid4(),
        revision=4,
        background="white",
        objects=(
            CanvasObjectV1(
                object_id="retrieval-box",
                object_type="shape",
                x=10,
                y=20,
                width=200,
                height=80,
                rotation=0,
            ),
            CanvasObjectV1(
                object_id="authority-label",
                object_type="text",
                x=25,
                y=40,
                width=160,
                height=30,
                rotation=0,
                text="OpenFang authorizes provider execution",
            ),
        ),
    )
    structured = encode_canvas_document(document)
    submission = CanvasSubmissionV1(
        document=document,
        structured_artifact=CanvasArtifactV1(
            artifact_id=uuid4(),
            artifact_kind="structured_canvas",
            media_type="application/json",
            sha256=hashlib.sha256(structured).hexdigest(),
            size_bytes=len(structured),
            locator="artifact://learning/canvas/structured.json",
            canvas_revision=4,
        ),
        rendered_snapshot=CanvasArtifactV1(
            artifact_id=uuid4(),
            artifact_kind="rendered_snapshot",
            media_type="image/png",
            sha256=hashlib.sha256(PNG).hexdigest(),
            size_bytes=len(PNG),
            locator="artifact://learning/canvas/snapshot.png",
            canvas_revision=4,
        ),
    )
    return CanvasEvaluationRequest(
        evaluation_id=str(uuid4()),
        response_id=str(uuid4()),
        submission_id=str(uuid4()),
        actor_id="student-1",
        submission=submission,
        expected_answer="Show retrieval, authorization, and verified readback.",
        rubric=(
            RubricCriterion(
                criterion_id="authority",
                description="Shows the provider authority boundary",
                points=2,
            ),
            RubricCriterion(
                criterion_id="readback",
                description="Shows terminal readback before success",
                points=1,
            ),
        ),
        source_refs=(
            SourceReferenceV1(
                source_id="authority",
                revision=3,
                locator="source://authority/3",
                title="Authority contract",
            ),
            SourceReferenceV1(
                source_id="readback",
                revision=2,
                locator="source://readback/2",
                title="Readback contract",
            ),
        ),
        model_version="openfang-canvas-v1",
        prompt_version="canvas-rubric-v1",
        source_version="course-sources-r4",
    )


def _output(confidence: float = 0.9) -> RubricModelOutput:
    return RubricModelOutput(
        schema_version="rubric-evaluation-v1",
        criteria=[
            CriterionEvaluation(
                criterion_id="authority",
                awarded_points=2,
                feedback="The authorization boundary is explicit.",
                source_refs=["source://authority/3"],
            ),
            CriterionEvaluation(
                criterion_id="readback",
                awarded_points=0.5,
                feedback="Readback is present but underspecified.",
                source_refs=["source://readback/2"],
            ),
        ],
        confidence=confidence,
        misconception_tags=["readback_implicit"],
    )


@pytest.mark.asyncio
async def test_canvas_evaluation_uses_structured_and_rendered_evidence() -> None:
    gateway = _Gateway(_output())
    recorder = _Recorder()
    mastery = _Mastery()
    evaluator = PenEchoEvaluator(
        gateway=gateway,
        recorder=recorder,
        review_queue=InMemoryReviewQueue(),
        mastery=mastery,
    )

    result = await evaluator.evaluate(_request(), snapshot_png=PNG)

    assert result.score == pytest.approx(2.5 / 3)
    assert result.accepted is True
    assert [criterion.score for criterion in result.criteria] == [1.0, 0.5]
    assert result.misconception_tags == ("readback_implicit",)
    invocation = gateway.invocations[0]
    assert invocation.role == "rubric_evaluator"
    assert len(invocation.image_inputs) == 1
    assert invocation.image_inputs[0].content == PNG
    assert invocation.input_payload["response"]["structured_canvas"]["objects"]
    assert invocation.input_payload["response"]["rendered_snapshot"]["sha256"]
    assert recorder.values[0].evidence_ref.startswith("openfang://completion/")
    assert len(mastery.values) == 1


@pytest.mark.asyncio
async def test_low_confidence_canvas_is_reviewed_without_mastery() -> None:
    review = InMemoryReviewQueue()
    mastery = _Mastery()
    evaluator = PenEchoEvaluator(
        gateway=_Gateway(_output(0.649)),
        recorder=_Recorder(),
        review_queue=review,
        mastery=mastery,
    )

    result = await evaluator.evaluate(_request(), snapshot_png=PNG)

    assert result.accepted is False
    assert review.items[0].reason_code == "low_confidence"
    assert mastery.values == []


@pytest.mark.asyncio
async def test_canvas_evaluation_rejects_tampered_snapshot_before_model_call() -> None:
    gateway = _Gateway(_output())
    evaluator = PenEchoEvaluator(
        gateway=gateway,
        recorder=_Recorder(),
        review_queue=InMemoryReviewQueue(),
        mastery=_Mastery(),
    )

    with pytest.raises(ValueError, match="snapshot.*integrity"):
        await evaluator.evaluate(_request(), snapshot_png=PNG + b"tampered")

    assert gateway.invocations == []


@pytest.mark.asyncio
async def test_canvas_schema_failure_creates_review_without_mastery() -> None:
    review = InMemoryReviewQueue()
    mastery = _Mastery()
    evaluator = PenEchoEvaluator(
        gateway=_Gateway(GatewaySchemaError("invalid")),
        recorder=_Recorder(),
        review_queue=review,
        mastery=mastery,
    )

    with pytest.raises(RuntimeError, match="schema"):
        await evaluator.evaluate(_request(), snapshot_png=PNG)

    assert review.items[0].reason_code == "evaluator_schema_invalid"
    assert mastery.values == []
