from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from spaces.learning.services.adaptive_engine.models import (
    ItemConcept,
    LearningAttempt,
    LearningEvaluation,
    LearningResponse,
    ReviewSchedule,
)
from spaces.learning.services.course_factory.repository import SessionFactory
from spaces.learning.services.db.models import utc_now
from spaces.learning.services.db.repository import PersistenceConflict


_INTERVAL_DAYS = (1, 3, 7, 14)
_MAINTENANCE_INDEX = len(_INTERVAL_DAYS)
_CONFIDENCE_THRESHOLD = 0.65
_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class ReviewState:
    interval_index: int
    due_at: datetime
    maintenance: bool

    def __post_init__(self) -> None:
        if self.interval_index < 0 or self.interval_index > _MAINTENANCE_INDEX:
            raise ValueError("review interval index is invalid")
        _require_timezone(self.due_at)
        if self.maintenance is not (self.interval_index == _MAINTENANCE_INDEX):
            raise ValueError("review maintenance state is inconsistent")


@dataclass(frozen=True)
class ReviewPlan(ReviewState):
    alternate_representation_required: bool


def schedule_review(
    current: ReviewState | None, *, successful: bool, now: datetime
) -> ReviewPlan:
    _require_timezone(now)
    if not successful:
        index = 0
        alternate = True
    elif current is None:
        index = 0
        alternate = False
    else:
        index = min(current.interval_index + 1, _MAINTENANCE_INDEX)
        alternate = False
    interval = _INTERVAL_DAYS[min(index, len(_INTERVAL_DAYS) - 1)]
    return ReviewPlan(
        interval_index=index,
        due_at=now + timedelta(days=interval),
        maintenance=index == _MAINTENANCE_INDEX,
        alternate_representation_required=alternate,
    )


def _require_timezone(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("review clock must be timezone-aware")


@dataclass(frozen=True)
class ReviewApplicationResult:
    evaluation_id: str
    applied: bool
    duplicate: bool
    plans: dict[str, ReviewPlan]


class ReviewScheduleService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def apply_evaluation(
        self,
        *,
        actor_id: str,
        evaluation_id: str,
        successful: bool,
        now: datetime,
    ) -> ReviewApplicationResult:
        if not _ACTOR.fullmatch(actor_id):
            raise ValueError("review actor ID is invalid")
        evaluation_id = str(UUID(evaluation_id))
        _require_timezone(now)
        try:
            with self._session_factory() as session, session.begin():
                evaluation = session.get(
                    LearningEvaluation, evaluation_id, with_for_update=True
                )
                if evaluation is None:
                    raise LookupError("learning evaluation not found")
                response = session.get(LearningResponse, evaluation.response_id)
                attempt = (
                    session.get(LearningAttempt, response.attempt_id)
                    if response is not None
                    else None
                )
                if response is None or attempt is None:
                    raise PersistenceConflict("evaluation lineage is incomplete")
                concept_ids = tuple(
                    sorted(
                        session.scalars(
                            select(ItemConcept.concept_id).where(
                                ItemConcept.item_id == attempt.item_id,
                                ItemConcept.course_id == attempt.course_id,
                                ItemConcept.course_revision == attempt.course_revision,
                            )
                        ).all()
                    )
                )
                if not concept_ids:
                    raise PersistenceConflict("evaluated item has no concepts")
                if (
                    not evaluation.accepted
                    or evaluation.confidence < _CONFIDENCE_THRESHOLD
                ):
                    return ReviewApplicationResult(
                        evaluation_id=evaluation.id,
                        applied=False,
                        duplicate=False,
                        plans={},
                    )
                rows = {
                    row.concept_id: row
                    for row in session.scalars(
                        select(ReviewSchedule)
                        .where(
                            ReviewSchedule.actor_id == actor_id,
                            ReviewSchedule.concept_id.in_(concept_ids),
                        )
                        .with_for_update()
                    ).all()
                }
                if rows and all(
                    row.last_evaluation_id == evaluation.id for row in rows.values()
                ) and set(rows) == set(concept_ids):
                    return ReviewApplicationResult(
                        evaluation_id=evaluation.id,
                        applied=False,
                        duplicate=True,
                        plans={},
                    )
                if any(
                    row.last_evaluation_id == evaluation.id for row in rows.values()
                ):
                    raise PersistenceConflict("partial review schedule update detected")
                plans: dict[str, ReviewPlan] = {}
                for concept_id in concept_ids:
                    row = rows.get(concept_id)
                    current = (
                        ReviewState(
                            interval_index=row.interval_index,
                            due_at=row.due_at,
                            maintenance=row.maintenance,
                        )
                        if row is not None
                        else None
                    )
                    plan = schedule_review(current, successful=successful, now=now)
                    plans[concept_id] = plan
                    if row is None:
                        session.add(
                            ReviewSchedule(
                                actor_id=actor_id,
                                concept_id=concept_id,
                                interval_index=plan.interval_index,
                                due_at=plan.due_at,
                                maintenance=plan.maintenance,
                                alternate_representation_required=(
                                    plan.alternate_representation_required
                                ),
                                last_evaluation_id=evaluation.id,
                                revision=1,
                                updated_at=utc_now(),
                            )
                        )
                    else:
                        row.interval_index = plan.interval_index
                        row.due_at = plan.due_at
                        row.maintenance = plan.maintenance
                        row.alternate_representation_required = (
                            plan.alternate_representation_required
                        )
                        row.last_evaluation_id = evaluation.id
                        row.revision += 1
                        row.updated_at = utc_now()
                session.flush()
                return ReviewApplicationResult(
                    evaluation_id=evaluation.id,
                    applied=True,
                    duplicate=False,
                    plans=plans,
                )
        except IntegrityError as error:
            raise PersistenceConflict("review schedule application conflict") from error
