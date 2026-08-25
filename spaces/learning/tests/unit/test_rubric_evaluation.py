from __future__ import annotations

from uuid import uuid4

import pytest

from spaces.learning.services.course_factory.model_gateway import (
    GatewayResult,
    GatewaySchemaError,
    GatewayUnavailable,
)
from spaces.learning.services.evaluation.review_queue import InMemoryReviewQueue
from spaces.learning.services.evaluation.rubric import (
    RubricEvaluationUnavailable,
    RubricEvaluator,
)
from spaces.learning.services.evaluation.schemas import (
    CriterionEvaluation,
    RubricCriterion,
    RubricEvaluationRequest,
    RubricModelOutput,
)


def _request() -> RubricEvaluationRequest:
    return RubricEvaluationRequest(
        evaluation_id=str(uuid4()),
        response_id=str(uuid4()),
        response={"text": "Provider execution stays behind OpenFang."},
        expected_answer="Explain the authorized provider boundary.",
        rubric=[
            RubricCriterion(
                criterion_id="authority",
                description="Identifies the authority boundary",
                points=2,
            ),
            RubricCriterion(
                criterion_id="readback",
                description="Requires terminal readback",
                points=1,
            ),
        ],
        source_refs=["source://authority/3", "source://readback/2"],
        model_version="openfang-learning-v1",
        prompt_version="rubric-v1",
        source_version="course-sources-r3",
    )


def _output(confidence: float = 0.9) -> RubricModelOutput:
    return RubricModelOutput(
        schema_version="rubric-evaluation-v1",
        criteria=[
            CriterionEvaluation(
                criterion_id="authority",
                awarded_points=2,
                feedback="The authority boundary is correct.",
                source_refs=["source://authority/3"],
            ),
            CriterionEvaluation(
                criterion_id="readback",
                awarded_points=0.5,
                feedback="The terminal readback is only implicit.",
                source_refs=["source://readback/2"],
            ),
        ],
        confidence=confidence,
        misconception_tags=["readback_implicit"],
    )


class _Gateway:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate(self, invocation, output_model):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert invocation.role == "rubric_evaluator"
        assert invocation.output_schema_version == "rubric-evaluation-v1"
        assert isinstance(outcome, output_model)
        return GatewayResult(
            output=outcome,
            evidence_ref=f"openfang://completion/rubric-{self.calls}",
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("confidence", "accepted", "review_count", "mastery_count"),
    [(0.649, False, 1, 0), (0.65, True, 0, 1)],
)
async def test_confidence_boundary_gates_mastery(
    confidence, accepted, review_count, mastery_count
) -> None:
    recorder = _Recorder()
    mastery = _Mastery()
    review = InMemoryReviewQueue()
    evaluator = RubricEvaluator(
        gateway=_Gateway([_output(confidence)]),
        recorder=recorder,
        review_queue=review,
        mastery=mastery,
    )

    decision = await evaluator.evaluate(_request())

    assert decision.score == pytest.approx(2.5 / 3)
    assert decision.confidence == confidence
    assert decision.accepted is accepted
    assert decision.evaluator_versions == {
        "model": "openfang-learning-v1",
        "prompt": "rubric-v1",
        "sources": "course-sources-r3",
    }
    assert len(recorder.values) == 1
    assert len(review.items) == review_count
    assert len(mastery.values) == mastery_count


@pytest.mark.asyncio
async def test_feedback_must_be_grounded_in_declared_sources() -> None:
    original = _output()
    output = original.model_copy(
        update={
            "criteria": [
                original.criteria[0].model_copy(
                    update={"source_refs": ["source://invented/99"]}
                ),
                original.criteria[1],
            ]
        }
    )
    review = InMemoryReviewQueue()
    mastery = _Mastery()
    evaluator = RubricEvaluator(
        gateway=_Gateway([output]),
        recorder=_Recorder(),
        review_queue=review,
        mastery=mastery,
    )

    decision = await evaluator.evaluate(_request())

    assert decision.accepted is False
    assert decision.rationale_codes == ["unsupported_feedback"]
    assert len(review.items) == 1
    assert mastery.values == []


@pytest.mark.asyncio
async def test_transient_gateway_failure_retries_once() -> None:
    gateway = _Gateway([GatewayUnavailable("offline"), _output()])
    evaluator = RubricEvaluator(
        gateway=gateway,
        recorder=_Recorder(),
        review_queue=InMemoryReviewQueue(),
        mastery=_Mastery(),
        max_attempts=2,
    )

    decision = await evaluator.evaluate(_request())

    assert decision.accepted is True
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_schema_failure_creates_review_without_mastery() -> None:
    review = InMemoryReviewQueue()
    mastery = _Mastery()
    evaluator = RubricEvaluator(
        gateway=_Gateway([GatewaySchemaError("invalid")]),
        recorder=_Recorder(),
        review_queue=review,
        mastery=mastery,
    )

    with pytest.raises(RubricEvaluationUnavailable, match="schema"):
        await evaluator.evaluate(_request())

    assert review.items[0].reason_code == "evaluator_schema_invalid"
    assert mastery.values == []


@pytest.mark.asyncio
async def test_criterion_contract_must_match_request_exactly() -> None:
    original = _output()
    output = original.model_copy(update={"criteria": original.criteria[:1]})
    review = InMemoryReviewQueue()
    mastery = _Mastery()
    evaluator = RubricEvaluator(
        gateway=_Gateway([output]),
        recorder=_Recorder(),
        review_queue=review,
        mastery=mastery,
    )

    decision = await evaluator.evaluate(_request())

    assert decision.accepted is False
    assert decision.rationale_codes == ["rubric_contract_mismatch"]
    assert mastery.values == []
