from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from spaces.learning.services.adaptive_engine.models import (
    ConceptMastery,
    ItemConcept,
    LearningAttempt,
    LearningEvaluation,
    LearningResponse,
    MasteryEvidence,
)
from spaces.learning.services.course_factory.repository import SessionFactory
from spaces.learning.services.db.models import utc_now
from spaces.learning.services.db.repository import PersistenceConflict


_CONFIDENCE_THRESHOLD = 0.65
_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class MasteryState:
    alpha: float = 2.0
    beta: float = 2.0

    def __post_init__(self) -> None:
        if self.alpha <= 0 or self.beta <= 0:
            raise ValueError("mastery parameters must be positive")

    @property
    def posterior_mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


@dataclass(frozen=True)
class MasteryUpdate:
    before: MasteryState
    after: MasteryState
    weight: float
    score: float


@dataclass(frozen=True)
class MasteryApplicationResult:
    evaluation_id: str
    applied: bool
    duplicate: bool
    concept_ids: tuple[str, ...]


def calculate_mastery_update(
    state: MasteryState,
    *,
    score: float,
    confidence: float,
    concept_count: int,
    accepted: bool,
) -> MasteryUpdate | None:
    if score < 0 or score > 1:
        raise ValueError("mastery score must be between zero and one")
    if confidence < 0 or confidence > 1:
        raise ValueError("mastery confidence must be between zero and one")
    if concept_count < 1:
        raise ValueError("mastery concept_count must be positive")
    if not accepted or confidence < _CONFIDENCE_THRESHOLD:
        return None
    weight = confidence / concept_count
    after = MasteryState(
        alpha=state.alpha + weight * score,
        beta=state.beta + weight * (1 - score),
    )
    return MasteryUpdate(before=state, after=after, weight=weight, score=score)


class MasteryService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def apply_evaluation(
        self, *, actor_id: str, evaluation_id: str
    ) -> MasteryApplicationResult:
        if not _ACTOR.fullmatch(actor_id):
            raise ValueError("mastery actor ID is invalid")
        evaluation_id = str(UUID(evaluation_id))
        try:
            with self._session_factory() as session, session.begin():
                evaluation = session.get(
                    LearningEvaluation, evaluation_id, with_for_update=True
                )
                if evaluation is None:
                    raise LookupError("learning evaluation not found")
                response = session.get(LearningResponse, evaluation.response_id)
                if response is None:
                    raise PersistenceConflict("evaluation response is missing")
                attempt = session.get(LearningAttempt, response.attempt_id)
                if attempt is None:
                    raise PersistenceConflict("evaluation attempt is missing")
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
                existing = set(
                    session.scalars(
                        select(MasteryEvidence.concept_id).where(
                            MasteryEvidence.evaluation_id == evaluation.id
                        )
                    ).all()
                )
                if existing:
                    if existing != set(concept_ids):
                        raise PersistenceConflict("partial mastery evidence detected")
                    return MasteryApplicationResult(
                        evaluation_id=evaluation.id,
                        applied=False,
                        duplicate=True,
                        concept_ids=concept_ids,
                    )
                if (
                    not evaluation.accepted
                    or evaluation.confidence < _CONFIDENCE_THRESHOLD
                ):
                    return MasteryApplicationResult(
                        evaluation_id=evaluation.id,
                        applied=False,
                        duplicate=False,
                        concept_ids=concept_ids,
                    )
                for concept_id in concept_ids:
                    mastery = session.get(
                        ConceptMastery,
                        {"actor_id": actor_id, "concept_id": concept_id},
                        with_for_update=True,
                    )
                    before = (
                        MasteryState(mastery.alpha, mastery.beta)
                        if mastery is not None
                        else MasteryState()
                    )
                    update = calculate_mastery_update(
                        before,
                        score=evaluation.score,
                        confidence=evaluation.confidence,
                        concept_count=len(concept_ids),
                        accepted=evaluation.accepted,
                    )
                    assert update is not None
                    if mastery is None:
                        mastery = ConceptMastery(
                            actor_id=actor_id,
                            concept_id=concept_id,
                            alpha=update.after.alpha,
                            beta=update.after.beta,
                            revision=2,
                            updated_at=utc_now(),
                        )
                        session.add(mastery)
                    else:
                        mastery.alpha = update.after.alpha
                        mastery.beta = update.after.beta
                        mastery.revision += 1
                        mastery.updated_at = utc_now()
                    session.add(
                        MasteryEvidence(
                            id=str(uuid4()),
                            evaluation_id=evaluation.id,
                            concept_id=concept_id,
                            actor_id=actor_id,
                            applied_weight=update.weight,
                            applied_score=update.score,
                            alpha_before=update.before.alpha,
                            beta_before=update.before.beta,
                            alpha_after=update.after.alpha,
                            beta_after=update.after.beta,
                            created_at=utc_now(),
                        )
                    )
                session.flush()
                return MasteryApplicationResult(
                    evaluation_id=evaluation.id,
                    applied=True,
                    duplicate=False,
                    concept_ids=concept_ids,
                )
        except IntegrityError as error:
            raise PersistenceConflict("mastery application conflict") from error
