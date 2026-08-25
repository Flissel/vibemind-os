from __future__ import annotations

from typing import Protocol
from uuid import UUID

from spaces.learning.services.course_factory.model_gateway import (
    GatewaySchemaError,
    GatewayUnavailable,
    ModelGateway,
    ModelInvocation,
)
from spaces.learning.services.evaluation.review_queue import ReviewItem, ReviewQueue
from spaces.learning.services.evaluation.schemas import (
    RubricDecision,
    RubricEvaluationRequest,
    RubricModelOutput,
)


_CONFIDENCE_THRESHOLD = 0.65


class RubricEvaluationUnavailable(RuntimeError):
    pass


class EvaluationRecorder(Protocol):
    def record(self, value: RubricDecision) -> None: ...


class MasteryApplication(Protocol):
    def apply(self, value: RubricDecision) -> None: ...


class RubricEvaluator:
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        recorder: EvaluationRecorder,
        review_queue: ReviewQueue,
        mastery: MasteryApplication,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("rubric evaluator attempts must be between one and three")
        self._gateway = gateway
        self._recorder = recorder
        self._review_queue = review_queue
        self._mastery = mastery
        self._max_attempts = max_attempts

    async def evaluate(self, request: RubricEvaluationRequest) -> RubricDecision:
        UUID(request.evaluation_id)
        invocation = ModelInvocation(
            role="rubric_evaluator",
            stage="evaluation",
            correlation_id=request.evaluation_id,
            prompt_version="rubric-prompt-v1",
            output_schema_version="rubric-evaluation-v1",
            input_payload={
                "response": request.response,
                "expected_answer": request.expected_answer,
                "rubric": [item.model_dump(mode="json") for item in request.rubric],
                "source_refs": request.source_refs,
                "declared_versions": {
                    "model": request.model_version,
                    "prompt": request.prompt_version,
                    "sources": request.source_version,
                },
            },
        )
        result = None
        for attempt in range(self._max_attempts):
            try:
                result = await self._gateway.generate(invocation, RubricModelOutput)
                break
            except GatewayUnavailable as error:
                if attempt + 1 == self._max_attempts:
                    self._enqueue_failure(request, "evaluator_unavailable")
                    raise RubricEvaluationUnavailable(
                        "rubric evaluator is unavailable"
                    ) from error
            except GatewaySchemaError as error:
                self._enqueue_failure(request, "evaluator_schema_invalid")
                raise RubricEvaluationUnavailable(
                    "rubric evaluator returned schema-invalid output"
                ) from error
        if result is None:
            raise RubricEvaluationUnavailable("rubric evaluator produced no result")

        output = result.output
        criteria_by_id = {item.criterion_id: item for item in request.rubric}
        output_ids = [item.criterion_id for item in output.criteria]
        contract_matches = (
            len(output_ids) == len(set(output_ids))
            and set(output_ids) == set(criteria_by_id)
            and all(
                item.awarded_points <= criteria_by_id[item.criterion_id].points
                for item in output.criteria
                if item.criterion_id in criteria_by_id
            )
        )
        allowed_sources = set(request.source_refs)
        feedback_grounded = all(
            item.source_refs and set(item.source_refs) <= allowed_sources
            for item in output.criteria
        )
        if contract_matches:
            awarded = sum(item.awarded_points for item in output.criteria)
            maximum = sum(item.points for item in request.rubric)
            score = awarded / maximum
        else:
            score = 0.0

        if not contract_matches:
            rationale = "rubric_contract_mismatch"
        elif not feedback_grounded:
            rationale = "unsupported_feedback"
        elif output.confidence < _CONFIDENCE_THRESHOLD:
            rationale = "low_confidence"
        else:
            rationale = "rubric_accepted"
        accepted = rationale == "rubric_accepted"
        decision = RubricDecision(
            evaluation_id=request.evaluation_id,
            response_id=request.response_id,
            score=score,
            confidence=output.confidence,
            accepted=accepted,
            rationale_codes=[rationale],
            criterion_results=output.criteria,
            misconception_tags=output.misconception_tags,
            evaluator_versions={
                "model": request.model_version,
                "prompt": request.prompt_version,
                "sources": request.source_version,
            },
            source_refs=request.source_refs,
            evidence_ref=result.evidence_ref,
        )
        self._recorder.record(decision)
        if accepted:
            self._mastery.apply(decision)
        else:
            self._review_queue.enqueue(
                ReviewItem(
                    evaluation_id=request.evaluation_id,
                    reason_code=rationale,
                    details={
                        "confidence": output.confidence,
                        "evidence_ref": result.evidence_ref,
                    },
                )
            )
        return decision

    def _enqueue_failure(
        self, request: RubricEvaluationRequest, reason_code: str
    ) -> None:
        self._review_queue.enqueue(
            ReviewItem(
                evaluation_id=request.evaluation_id,
                reason_code=reason_code,
                details={
                    "model_version": request.model_version,
                    "prompt_version": request.prompt_version,
                    "source_version": request.source_version,
                },
            )
        )
