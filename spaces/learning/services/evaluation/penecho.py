from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spaces.learning.contracts.penecho import (
    CanvasRubricCriterionV1,
    CanvasRubricResultV1,
    CanvasSubmissionV1,
    SourceReferenceV1,
)
from spaces.learning.services.course_factory.model_gateway import (
    ModelGateway,
    ModelImageInput,
    ModelInvocation,
)
from spaces.learning.services.evaluation.canvas_artifacts import encode_canvas_document
from spaces.learning.services.evaluation.review_queue import ReviewQueue
from spaces.learning.services.evaluation.review_queue import InMemoryReviewQueue
from spaces.learning.services.evaluation.rubric import (
    EvaluationRecorder,
    MasteryApplication,
    RubricEvaluator,
)
from spaces.learning.services.evaluation.schemas import (
    RubricCriterion,
    RubricDecision,
    RubricEvaluationRequest,
)


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CanvasEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_id: str = Field(min_length=36, max_length=36)
    response_id: str = Field(min_length=36, max_length=36)
    submission_id: str = Field(min_length=36, max_length=36)
    actor_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    submission: CanvasSubmissionV1
    expected_answer: str = Field(min_length=1, max_length=50_000)
    rubric: tuple[RubricCriterion, ...] = Field(min_length=1, max_length=100)
    source_refs: tuple[SourceReferenceV1, ...] = Field(min_length=1, max_length=256)
    model_version: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_identity_and_sources(self) -> "CanvasEvaluationRequest":
        UUID(self.evaluation_id)
        UUID(self.response_id)
        UUID(self.submission_id)
        locators = [item.locator for item in self.source_refs]
        if len(locators) != len(set(locators)):
            raise ValueError("canvas evaluation source references must be unique")
        return self


class _ImageBoundGateway:
    def __init__(self, gateway: ModelGateway, image: ModelImageInput) -> None:
        self._gateway = gateway
        self._image = image

    async def generate(self, invocation: ModelInvocation, output_model):
        return await self._gateway.generate(
            replace(invocation, image_inputs=(self._image,)), output_model
        )


class PenEchoEvaluator:
    """Evaluates one immutable canvas revision through the shared rubric boundary."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        recorder: EvaluationRecorder,
        review_queue: ReviewQueue,
        mastery: MasteryApplication,
    ) -> None:
        self._gateway = gateway
        self._recorder = recorder
        self._review_queue = review_queue
        self._mastery = mastery

    async def evaluate(
        self, request: CanvasEvaluationRequest, *, snapshot_png: bytes
    ) -> CanvasRubricResultV1:
        self._verify_artifacts(request.submission, snapshot_png)
        image = ModelImageInput(
            media_type="image/png",
            content=snapshot_png,
            sha256=request.submission.rendered_snapshot.sha256,
        )
        source_locators = [item.locator for item in request.source_refs]
        rubric_request = RubricEvaluationRequest(
            evaluation_id=request.evaluation_id,
            response_id=request.response_id,
            response={
                "kind": "penecho_canvas",
                "submission_id": request.submission_id,
                "structured_canvas": request.submission.document.model_dump(mode="json"),
                "structured_artifact": request.submission.structured_artifact.model_dump(mode="json"),
                "rendered_snapshot": request.submission.rendered_snapshot.model_dump(mode="json"),
            },
            expected_answer=request.expected_answer,
            rubric=list(request.rubric),
            source_refs=source_locators,
            model_version=request.model_version,
            prompt_version=request.prompt_version,
            source_version=request.source_version,
        )
        evaluator = RubricEvaluator(
            gateway=_ImageBoundGateway(self._gateway, image),
            recorder=self._recorder,
            review_queue=self._review_queue,
            mastery=self._mastery,
        )
        decision = await evaluator.evaluate(rubric_request)
        return self._result(request, decision)

    @staticmethod
    def _verify_artifacts(submission: CanvasSubmissionV1, snapshot_png: bytes) -> None:
        structured = encode_canvas_document(submission.document)
        if (
            len(structured) != submission.structured_artifact.size_bytes
            or hashlib.sha256(structured).hexdigest()
            != submission.structured_artifact.sha256
        ):
            raise ValueError("canvas structured artifact failed integrity verification")
        if (
            not snapshot_png.startswith(_PNG_SIGNATURE)
            or len(snapshot_png) != submission.rendered_snapshot.size_bytes
            or hashlib.sha256(snapshot_png).hexdigest()
            != submission.rendered_snapshot.sha256
        ):
            raise ValueError("canvas snapshot failed integrity verification")

    @staticmethod
    def _result(
        request: CanvasEvaluationRequest, decision: RubricDecision
    ) -> CanvasRubricResultV1:
        rubric = {item.criterion_id: item for item in request.rubric}
        sources = {item.locator: item for item in request.source_refs}
        criteria = tuple(
            CanvasRubricCriterionV1(
                criterion_id=item.criterion_id,
                score=item.awarded_points / rubric[item.criterion_id].points,
                feedback=item.feedback,
                source_refs=tuple(sources[locator] for locator in item.source_refs),
            )
            for item in decision.criterion_results
        )
        return CanvasRubricResultV1(
            evaluation_id=UUID(decision.evaluation_id),
            submission_id=UUID(request.submission_id),
            score=decision.score,
            confidence=decision.confidence,
            accepted=decision.accepted,
            criteria=criteria,
            misconception_tags=tuple(decision.misconception_tags),
            evaluator_versions=decision.evaluator_versions,
        )


@dataclass
class _DecisionCapture:
    value: RubricDecision | None = None

    def record(self, decision: RubricDecision) -> None:
        self.value = decision


class _DeferredMastery:
    def apply(self, decision: RubricDecision) -> None:
        del decision


class SessionPenEchoEvaluator:
    """Returns a validated decision while the adaptive session owns persistence."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def evaluate(
        self, request: CanvasEvaluationRequest, *, snapshot_png: bytes
    ) -> tuple[CanvasRubricResultV1, RubricDecision]:
        capture = _DecisionCapture()
        evaluator = PenEchoEvaluator(
            gateway=self._gateway,
            recorder=capture,
            review_queue=InMemoryReviewQueue(),
            mastery=_DeferredMastery(),
        )
        result = await evaluator.evaluate(request, snapshot_png=snapshot_png)
        if capture.value is None:
            raise RuntimeError("canvas rubric decision capture failed")
        return result, capture.value
