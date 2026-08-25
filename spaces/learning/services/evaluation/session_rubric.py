from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from spaces.learning.services.course_factory.model_gateway import ModelGateway
from spaces.learning.services.evaluation.review_queue import ReviewItem
from spaces.learning.services.evaluation.rubric import RubricEvaluator
from spaces.learning.services.evaluation.schemas import (
    RubricDecision,
    RubricEvaluationRequest,
)


@dataclass
class _DecisionRecorder:
    decision: RubricDecision | None = None

    def record(self, value: RubricDecision) -> None:
        self.decision = value


@dataclass
class _ReviewCapture:
    items: list[ReviewItem] = field(default_factory=list)

    def enqueue(self, item: ReviewItem) -> None:
        self.items.append(item)


class _DeferredMastery:
    def apply(self, value: RubricDecision) -> None:
        del value


class SessionRubricEvaluator:
    """Runs the validated rubric boundary while deferring persistence to the session UoW."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    def evaluate(self, request: RubricEvaluationRequest) -> RubricDecision:
        recorder = _DecisionRecorder()
        evaluator = RubricEvaluator(
            gateway=self._gateway,
            recorder=recorder,
            review_queue=_ReviewCapture(),
            mastery=_DeferredMastery(),
        )
        decision = asyncio.run(evaluator.evaluate(request))
        if recorder.decision != decision:
            raise RuntimeError("rubric decision capture failed")
        return decision
