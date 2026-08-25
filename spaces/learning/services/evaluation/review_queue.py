from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from spaces.learning.services.adaptive_engine.models import (
    LearningEvaluation,
    LearningReviewItem,
)
from spaces.learning.services.course_factory.repository import SessionFactory
from spaces.learning.services.db.models import utc_now
from spaces.learning.services.db.repository import PersistenceConflict
from spaces.learning.services.evaluation.schemas import RubricDecision


@dataclass(frozen=True)
class ReviewItem:
    evaluation_id: str
    reason_code: str
    details: dict[str, object]


class ReviewQueue(Protocol):
    def enqueue(self, item: ReviewItem) -> None: ...


class InMemoryReviewQueue:
    def __init__(self) -> None:
        self.items: list[ReviewItem] = []

    def enqueue(self, item: ReviewItem) -> None:
        if any(value.evaluation_id == item.evaluation_id for value in self.items):
            return
        self.items.append(item)


class SqlEvaluationRecorder:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def record(self, value: RubricDecision) -> None:
        try:
            with self._session_factory() as session, session.begin():
                existing = session.get(LearningEvaluation, value.evaluation_id)
                if existing is not None:
                    if (
                        existing.response_id != value.response_id
                        or existing.score != value.score
                        or existing.confidence != value.confidence
                        or existing.accepted is not value.accepted
                    ):
                        raise PersistenceConflict("rubric evaluation is immutable")
                    return
                session.add(
                    LearningEvaluation(
                        id=str(UUID(value.evaluation_id)),
                        response_id=str(UUID(value.response_id)),
                        evaluator_type="rubric",
                        score=value.score,
                        confidence=value.confidence,
                        accepted=value.accepted,
                        rationale_codes=value.rationale_codes,
                        evaluator_versions=value.evaluator_versions,
                        source_refs=value.source_refs,
                        created_at=utc_now(),
                    )
                )
        except IntegrityError as error:
            raise PersistenceConflict("rubric evaluation conflict") from error


class SqlReviewQueue:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def enqueue(self, item: ReviewItem) -> None:
        request_ref = str(UUID(item.evaluation_id))
        try:
            with self._session_factory() as session, session.begin():
                existing = session.scalar(
                    select(LearningReviewItem).where(
                        LearningReviewItem.request_ref == request_ref,
                        LearningReviewItem.reason_code == item.reason_code,
                    )
                )
                if existing is not None:
                    return
                evaluation_id = (
                    request_ref
                    if session.get(LearningEvaluation, request_ref) is not None
                    else None
                )
                session.add(
                    LearningReviewItem(
                        id=str(uuid4()),
                        request_ref=request_ref,
                        evaluation_id=evaluation_id,
                        reason_code=item.reason_code,
                        status="pending",
                        details=item.details,
                        created_at=utc_now(),
                        resolved_at=None,
                    )
                )
        except IntegrityError as error:
            raise PersistenceConflict("evaluation review queue conflict") from error
