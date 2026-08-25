from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from spaces.learning.services.adaptive_engine.eligibility import (
    CandidateItem,
    SelectionContext,
)
from spaces.learning.services.adaptive_engine.mastery import (
    MasteryState,
    calculate_mastery_update,
)
from spaces.learning.services.adaptive_engine.models import (
    AdaptiveConcept,
    AdaptiveItem,
    ConceptMastery,
    ItemConcept,
    LearningAttempt,
    LearningEvaluation,
    LearningResponse,
    LearningSession,
    MasteryEvidence,
    Misconception,
    ReviewSchedule,
    LearningReviewItem,
)
from spaces.learning.services.adaptive_engine.review_schedule import (
    ReviewState,
    schedule_review,
)
from spaces.learning.services.adaptive_engine.selector import select_next_item
from spaces.learning.services.course_factory.repository import SessionFactory
from spaces.learning.services.db.repository import PersistenceConflict
from spaces.learning.services.evaluation.deterministic import score_response
from spaces.learning.services.evaluation.schemas import (
    ChoiceOption,
    DeterministicItem,
    RubricCriterion,
    RubricDecision,
    RubricEvaluationRequest,
)


_DETERMINISTIC_TYPES = frozenset(
    {"single_choice", "multiple_choice", "matching", "ordering", "numeric", "fill_in"}
)
_RUBRIC_TYPES = frozenset({"open", "case", "code"})


class SessionRubricBoundary(Protocol):
    def evaluate(self, request: RubricEvaluationRequest) -> RubricDecision: ...


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionStartCommand(_Command):
    course_id: str
    course_revision: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=128)
    mode: Literal["training", "exam"]
    blueprint: dict[str, object]

    @field_validator("course_id")
    @classmethod
    def validate_course_id(cls, value: str) -> str:
        return str(UUID(value))


class AnswerCommand(_Command):
    session_id: str
    actor_id: str = Field(min_length=1, max_length=128)
    expected_session_revision: int = Field(ge=1)
    answer: dict[str, object]

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        return str(UUID(value))


class PublicTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    type: str
    prompt: str
    options: tuple[ChoiceOption, ...]
    difficulty: float = Field(ge=0, le=1)
    representation: str
    source_refs: tuple[str, ...]
    presentation: dict[str, object]
    expected_answer: None = None


class SessionProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completed: int = Field(ge=0)
    total: int = Field(ge=1)


class TurnFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["correct", "incorrect", "partial", "needs_review"]
    score: float = Field(ge=0, le=1)
    rationale_codes: tuple[str, ...]
    source_refs: tuple[str, ...]


class AuditReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response_id: str | None = None
    evaluation_id: str | None = None
    mastery_applied: bool = False
    readback_verified: bool = False


class ExamResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int
    item_id: str
    score: float


class SessionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempted: int
    correct: int
    score: float = Field(ge=0, le=1)
    results: tuple[ExamResult, ...]


class SessionTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    course_id: str
    session_revision: int
    state: Literal["active", "completed", "cancelled"]
    mode: Literal["training", "exam"]
    task: PublicTask | None
    progress: SessionProgress
    feedback: TurnFeedback | None = None
    summary: SessionSummary | None = None
    audit: AuditReceipt = AuditReceipt()


@dataclass(frozen=True)
class _AnswerAudit:
    response_id: str
    evaluation_id: str
    mastery_applied: bool


class AdaptiveSessionService:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        rubric_evaluator: SessionRubricBoundary | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._rubric_evaluator = rubric_evaluator

    def start(self, command: SessionStartCommand) -> SessionTurn:
        now = self._aware_now()
        with self._session_factory() as db, db.begin():
            items = self._catalog(db, command.course_id, command.course_revision)
            if not items:
                raise LookupError("adaptive course revision has no approved items")
            blueprint = self._normalize_blueprint(command, items)
            row = LearningSession(
                id=str(uuid4()),
                course_id=command.course_id,
                course_revision=command.course_revision,
                actor_id=command.actor_id,
                mode=command.mode,
                state="active",
                revision=1,
                blueprint=blueprint,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            db.add(row)
            db.flush()
            self._append_next(db, row, items)
            turn = self._turn(db, row)
        return self._with_readback(turn)

    def resume(
        self, *, course_id: str, course_revision: int, actor_id: str
    ) -> SessionTurn | None:
        course_id = str(UUID(course_id))
        with self._session_factory() as db:
            row = db.scalar(
                select(LearningSession)
                .where(
                    LearningSession.course_id == course_id,
                    LearningSession.course_revision == course_revision,
                    LearningSession.actor_id == actor_id,
                    LearningSession.state == "active",
                )
                .order_by(LearningSession.created_at.desc(), LearningSession.id.desc())
                .limit(1)
            )
            return self._with_readback(self._turn(db, row)) if row is not None else None

    def current(self, session_id: str, *, actor_id: str) -> SessionTurn:
        with self._session_factory() as db:
            row = db.get(LearningSession, str(UUID(session_id)))
            self._assert_owned(row, actor_id)
            assert row is not None
            return self._with_readback(self._turn(db, row))

    def has_revision(self, session_id: str, revision: int) -> bool:
        with self._session_factory() as db:
            row = db.get(LearningSession, str(UUID(session_id)))
            return row is not None and row.revision == revision

    def answer(self, command: AnswerCommand) -> SessionTurn:
        now = self._aware_now()
        audit: _AnswerAudit
        with self._session_factory() as db, db.begin():
            row = db.get(LearningSession, command.session_id, with_for_update=True)
            self._assert_owned_active(row, command.actor_id)
            assert row is not None
            if row.revision != command.expected_session_revision:
                raise PersistenceConflict("adaptive session revision conflict")
            attempt = self._current_attempt(db, row.id)
            if attempt is None:
                raise PersistenceConflict("adaptive session has no current task")
            existing = db.scalar(
                select(LearningResponse.id).where(
                    LearningResponse.attempt_id == attempt.id
                )
            )
            if existing is not None:
                raise PersistenceConflict("adaptive task was already answered")
            item = db.get(AdaptiveItem, attempt.item_id)
            if item is None:
                raise LookupError("adaptive item is unavailable")
            if item.item_type in _DETERMINISTIC_TYPES:
                evaluation, audit = self._evaluate_deterministic(
                    db, row, attempt, item, command.answer, now
                )
            elif item.item_type in _RUBRIC_TYPES and self._rubric_evaluator is not None:
                evaluation, audit = self._evaluate_rubric(
                    db, row, attempt, item, command.answer, now
                )
            else:
                raise ValueError("adaptive item has no admitted evaluator")
            row.revision += 1
            row.updated_at = now
            total = self._total(row)
            if attempt.ordinal >= total:
                row.state = "completed"
                row.completed_at = now
            else:
                self._append_next(db, row, self._catalog(db, row.course_id, row.course_revision))
            db.flush()
            feedback = (
                self._feedback(evaluation)
                if row.mode == "training"
                else None
            )
            turn = self._turn(db, row, feedback=feedback, answer_audit=audit)
        return self._with_readback(turn)

    def hint(self, session_id: str, *, actor_id: str) -> str:
        with self._session_factory() as db:
            row = db.get(LearningSession, str(UUID(session_id)))
            self._assert_owned_active(row, actor_id)
            assert row is not None
            if row.mode == "exam":
                raise PermissionError("exam sessions do not expose hints")
            attempt = self._current_attempt(db, row.id)
            item = db.get(AdaptiveItem, attempt.item_id) if attempt else None
            hints = item.scoring_config.get("hints", []) if item is not None else []
            if not isinstance(hints, list) or not hints or not isinstance(hints[0], str):
                raise LookupError("no approved hint is available")
            return hints[0]

    def progress(self, session_id: str, *, actor_id: str) -> SessionProgress:
        with self._session_factory() as db:
            row = db.get(LearningSession, str(UUID(session_id)))
            self._assert_owned(row, actor_id)
            assert row is not None
            return self._progress(db, row)

    def _evaluate_deterministic(
        self,
        db: Session,
        session_row: LearningSession,
        attempt: LearningAttempt,
        item: AdaptiveItem,
        answer: dict[str, object],
        now: datetime,
    ) -> tuple[LearningEvaluation, _AnswerAudit]:
        prompt = self._string_config(item, "prompt")
        options = self._options(item)
        scored = score_response(
            DeterministicItem(
                item_id=item.id,
                item_type=item.item_type,
                prompt=prompt,
                options=options,
                expected_answer=item.expected_answer,
                scoring_config=item.scoring_config,
            ),
            answer,
        )
        encoded = json.dumps(answer, sort_keys=True, separators=(",", ":"))
        response = LearningResponse(
            id=str(uuid4()),
            attempt_id=attempt.id,
            response_revision=1,
            answer=answer,
            answer_hash=hashlib.sha256(encoded.encode()).hexdigest(),
            created_at=now,
        )
        db.add(response)
        db.flush()
        source_refs = self._string_list(item.scoring_config.get("source_refs", []))
        evaluation = LearningEvaluation(
            id=str(uuid4()),
            response_id=response.id,
            evaluator_type="deterministic",
            score=scored.score,
            confidence=scored.confidence,
            accepted=True,
            rationale_codes=list(scored.rationale_codes),
            evaluator_versions={"scorer": "deterministic-v1"},
            source_refs=list(source_refs),
            created_at=now,
        )
        db.add(evaluation)
        db.flush()
        applied = self._apply_mastery_and_review(
            db,
            actor_id=session_row.actor_id,
            attempt=attempt,
            evaluation=evaluation,
            successful=scored.score >= 0.65,
            now=now,
        )
        self._update_misconceptions(
            db,
            actor_id=session_row.actor_id,
            item=item,
            evaluation=evaluation,
            successful=scored.score >= 0.65,
        )
        return evaluation, _AnswerAudit(response.id, evaluation.id, applied)

    def _evaluate_rubric(
        self,
        db: Session,
        session_row: LearningSession,
        attempt: LearningAttempt,
        item: AdaptiveItem,
        answer: dict[str, object],
        now: datetime,
    ) -> tuple[LearningEvaluation, _AnswerAudit]:
        assert self._rubric_evaluator is not None
        response = LearningResponse(
            id=str(uuid4()),
            attempt_id=attempt.id,
            response_revision=1,
            answer=answer,
            answer_hash=hashlib.sha256(
                json.dumps(answer, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            created_at=now,
        )
        db.add(response)
        db.flush()
        evaluation_id = str(uuid4())
        expected = item.expected_answer.get("text")
        if not isinstance(expected, str) or not expected:
            raise ValueError("rubric item expected answer is invalid")
        source_refs = self._string_list(item.scoring_config.get("source_refs", []))
        criteria = [RubricCriterion.model_validate(value) for value in item.rubric]
        decision = self._rubric_evaluator.evaluate(
            RubricEvaluationRequest(
                evaluation_id=evaluation_id,
                response_id=response.id,
                response=answer,
                expected_answer=expected,
                rubric=criteria,
                source_refs=list(source_refs),
                model_version=self._version(item, "model_version", "openfang-learning"),
                prompt_version=self._version(item, "prompt_version", "rubric-prompt-v1"),
                source_version=self._version(item, "source_version", "course-revision"),
            )
        )
        evaluation = LearningEvaluation(
            id=decision.evaluation_id,
            response_id=response.id,
            evaluator_type="rubric",
            score=decision.score,
            confidence=decision.confidence,
            accepted=decision.accepted,
            rationale_codes=list(decision.rationale_codes),
            evaluator_versions={
                **decision.evaluator_versions,
                "evidence": decision.evidence_ref,
            },
            source_refs=list(decision.source_refs),
            created_at=now,
        )
        db.add(evaluation)
        db.flush()
        applied = False
        if decision.accepted:
            applied = self._apply_mastery_and_review(
                db,
                actor_id=session_row.actor_id,
                attempt=attempt,
                evaluation=evaluation,
                successful=decision.score >= 0.65,
                now=now,
            )
        else:
            db.add(
                LearningReviewItem(
                    id=str(uuid4()),
                    request_ref=f"session:{session_row.id}:{attempt.ordinal}",
                    evaluation_id=evaluation.id,
                    reason_code=decision.rationale_codes[0],
                    status="pending",
                    details={
                        "confidence": decision.confidence,
                        "evidence_ref": decision.evidence_ref,
                    },
                    created_at=now,
                    resolved_at=None,
                )
            )
        self._update_misconceptions(
            db,
            actor_id=session_row.actor_id,
            item=item,
            evaluation=evaluation,
            successful=decision.accepted and decision.score >= 0.65,
        )
        return evaluation, _AnswerAudit(response.id, evaluation.id, applied)

    def _apply_mastery_and_review(
        self,
        db: Session,
        *,
        actor_id: str,
        attempt: LearningAttempt,
        evaluation: LearningEvaluation,
        successful: bool,
        now: datetime,
    ) -> bool:
        concept_ids = tuple(
            sorted(
                db.scalars(
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
        for concept_id in concept_ids:
            mastery = db.get(
                ConceptMastery,
                {"actor_id": actor_id, "concept_id": concept_id},
                with_for_update=True,
            )
            before = MasteryState(mastery.alpha, mastery.beta) if mastery else MasteryState()
            update = calculate_mastery_update(
                before,
                score=evaluation.score,
                confidence=evaluation.confidence,
                concept_count=len(concept_ids),
                accepted=evaluation.accepted,
            )
            if update is None:
                continue
            if mastery is None:
                mastery = ConceptMastery(
                    actor_id=actor_id,
                    concept_id=concept_id,
                    alpha=update.after.alpha,
                    beta=update.after.beta,
                    revision=2,
                    updated_at=now,
                )
                db.add(mastery)
            else:
                mastery.alpha = update.after.alpha
                mastery.beta = update.after.beta
                mastery.revision += 1
                mastery.updated_at = now
            db.add(
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
                    created_at=now,
                )
            )
            review = db.get(
                ReviewSchedule,
                {"actor_id": actor_id, "concept_id": concept_id},
                with_for_update=True,
            )
            current = (
                ReviewState(review.interval_index, self._aware(review.due_at), review.maintenance)
                if review is not None
                else None
            )
            plan = schedule_review(current, successful=successful, now=now)
            if review is None:
                db.add(
                    ReviewSchedule(
                        actor_id=actor_id,
                        concept_id=concept_id,
                        interval_index=plan.interval_index,
                        due_at=plan.due_at,
                        maintenance=plan.maintenance,
                        alternate_representation_required=plan.alternate_representation_required,
                        last_evaluation_id=evaluation.id,
                        revision=1,
                        updated_at=now,
                    )
                )
            else:
                review.interval_index = plan.interval_index
                review.due_at = plan.due_at
                review.maintenance = plan.maintenance
                review.alternate_representation_required = plan.alternate_representation_required
                review.last_evaluation_id = evaluation.id
                review.revision += 1
                review.updated_at = now
        return True

    def _update_misconceptions(
        self,
        db: Session,
        *,
        actor_id: str,
        item: AdaptiveItem,
        evaluation: LearningEvaluation,
        successful: bool,
    ) -> None:
        tags = self._string_list(item.scoring_config.get("remediation_tags", []))
        concept_ids = db.scalars(
            select(ItemConcept.concept_id).where(ItemConcept.item_id == item.id)
        ).all()
        for concept_id in concept_ids:
            for tag in tags:
                row = db.scalar(
                    select(Misconception).where(
                        Misconception.actor_id == actor_id,
                        Misconception.concept_id == concept_id,
                        Misconception.tag == tag,
                    )
                )
                if successful:
                    if row is not None and row.status == "unresolved":
                        row.status = "resolved"
                        row.latest_evaluation_id = evaluation.id
                        row.resolved_at = self._aware_now()
                elif row is None:
                    db.add(
                        Misconception(
                            id=str(uuid4()),
                            actor_id=actor_id,
                            concept_id=concept_id,
                            tag=tag,
                            status="unresolved",
                            first_evaluation_id=evaluation.id,
                            latest_evaluation_id=evaluation.id,
                            created_at=self._aware_now(),
                            resolved_at=None,
                        )
                    )
                else:
                    row.status = "unresolved"
                    row.latest_evaluation_id = evaluation.id
                    row.resolved_at = None

    def _append_next(
        self, db: Session, row: LearningSession, items: tuple[AdaptiveItem, ...]
    ) -> LearningAttempt:
        ordinal = (
            db.scalar(
                select(func.max(LearningAttempt.ordinal)).where(
                    LearningAttempt.session_id == row.id
                )
            )
            or 0
        ) + 1
        item, reason = self._select_item(db, row, items, ordinal)
        attempt = LearningAttempt(
            id=str(uuid4()),
            session_id=row.id,
            item_id=item.id,
            course_id=row.course_id,
            course_revision=row.course_revision,
            ordinal=ordinal,
            selection_reason=reason,
            selected_at=self._aware_now(),
        )
        db.add(attempt)
        row.revision += 1
        row.updated_at = self._aware_now()
        db.flush()
        return attempt

    def _select_item(
        self,
        db: Session,
        row: LearningSession,
        items: tuple[AdaptiveItem, ...],
        ordinal: int,
    ) -> tuple[AdaptiveItem, dict[str, object]]:
        if row.mode == "exam":
            item_ids = row.blueprint.get("selected_item_ids")
            if not isinstance(item_ids, list) or ordinal > len(item_ids):
                raise PersistenceConflict("exam blueprint is exhausted")
            selected_id = item_ids[ordinal - 1]
            item = next((candidate for candidate in items if candidate.id == selected_id), None)
            if item is None:
                raise PersistenceConflict("exam blueprint item is unavailable")
            return item, {"mode": "exam_blueprint", "ordinal": ordinal}

        concepts_by_item = self._concepts_by_item(db, row.course_id, row.course_revision)
        concept_ids = sorted({value for values in concepts_by_item.values() for value in values})
        mastery_rows = db.scalars(
            select(ConceptMastery).where(
                ConceptMastery.actor_id == row.actor_id,
                ConceptMastery.concept_id.in_(concept_ids),
            )
        ).all()
        mastery = {concept_id: 0.5 for concept_id in concept_ids}
        mastery.update(
            {item.concept_id: item.alpha / (item.alpha + item.beta) for item in mastery_rows}
        )
        reviews = db.scalars(
            select(ReviewSchedule).where(
                ReviewSchedule.actor_id == row.actor_id,
                ReviewSchedule.concept_id.in_(concept_ids),
            )
        ).all()
        misconceptions: dict[str, list[str]] = {}
        for value in db.scalars(
            select(Misconception).where(
                Misconception.actor_id == row.actor_id,
                Misconception.concept_id.in_(concept_ids),
                Misconception.status == "unresolved",
            )
        ).all():
            misconceptions.setdefault(value.concept_id, []).append(value.tag)
        recent_attempts = db.scalars(
            select(LearningAttempt)
            .where(LearningAttempt.session_id == row.id)
            .order_by(LearningAttempt.ordinal.desc())
            .limit(10)
        ).all()
        item_by_id = {item.id: item for item in items}
        recent_representations: dict[str, str] = {}
        for attempt in recent_attempts:
            recent_item = item_by_id.get(attempt.item_id)
            if recent_item is None:
                continue
            representation = self._representation(recent_item)
            for concept_id in concepts_by_item.get(recent_item.id, ()):
                recent_representations.setdefault(concept_id, representation)
        context = SelectionContext(
            mastery=mastery,
            review_due_at={item.concept_id: self._aware(item.due_at) for item in reviews},
            unresolved_misconceptions={
                key: tuple(sorted(values)) for key, values in misconceptions.items()
            },
            recent_item_ids=tuple(item.item_id for item in recent_attempts),
            alternate_representation_concepts=frozenset(
                item.concept_id
                for item in reviews
                if item.alternate_representation_required
            ),
            recent_representations=recent_representations,
            now=self._aware_now(),
        )
        candidates = tuple(
            CandidateItem(
                item_id=item.id,
                difficulty=item.difficulty,
                concept_ids=concepts_by_item.get(item.id, ()),
                quality_status=item.quality_status,
                remediation_tags=self._string_list(
                    item.scoring_config.get("remediation_tags", [])
                ),
                representation=self._representation(item),
            )
            for item in items
        )
        result = select_next_item(candidates, context)
        if result.selected is None:
            raise LookupError("no eligible adaptive item is available")
        selected = item_by_id[result.selected.item_id]
        reason = asdict(result.selected)
        reason["reason_codes"] = list(result.selected.reason_codes)
        return selected, reason

    def _turn(
        self,
        db: Session,
        row: LearningSession,
        *,
        feedback: TurnFeedback | None = None,
        answer_audit: _AnswerAudit | None = None,
    ) -> SessionTurn:
        task = None
        if row.state == "active":
            attempt = self._current_attempt(db, row.id)
            item = db.get(AdaptiveItem, attempt.item_id) if attempt else None
            task = self._public_task(item) if item is not None else None
        summary = self._summary(db, row) if row.state == "completed" else None
        return SessionTurn(
            session_id=row.id,
            course_id=row.course_id,
            session_revision=row.revision,
            state=row.state,
            mode=row.mode,
            task=task,
            progress=self._progress(db, row),
            feedback=feedback,
            summary=summary,
            audit=AuditReceipt(
                response_id=answer_audit.response_id if answer_audit else None,
                evaluation_id=answer_audit.evaluation_id if answer_audit else None,
                mastery_applied=answer_audit.mastery_applied if answer_audit else False,
                readback_verified=False,
            ),
        )

    def _summary(self, db: Session, row: LearningSession) -> SessionSummary:
        values = db.execute(
            select(LearningAttempt.ordinal, LearningAttempt.item_id, LearningEvaluation.score)
            .join(LearningResponse, LearningResponse.attempt_id == LearningAttempt.id)
            .join(LearningEvaluation, LearningEvaluation.response_id == LearningResponse.id)
            .where(LearningAttempt.session_id == row.id)
            .order_by(LearningAttempt.ordinal)
        ).all()
        results = tuple(ExamResult(ordinal=a, item_id=b, score=c) for a, b, c in values)
        score = sum(item.score for item in results) / len(results) if results else 0
        return SessionSummary(
            attempted=len(results),
            correct=sum(item.score >= 0.65 for item in results),
            score=score,
            results=results,
        )

    def _progress(self, db: Session, row: LearningSession) -> SessionProgress:
        completed = db.scalar(
            select(func.count(LearningResponse.id))
            .join(LearningAttempt, LearningAttempt.id == LearningResponse.attempt_id)
            .where(LearningAttempt.session_id == row.id)
        ) or 0
        return SessionProgress(completed=completed, total=self._total(row))

    def _with_readback(self, turn: SessionTurn) -> SessionTurn:
        with self._session_factory() as db:
            row = db.get(LearningSession, turn.session_id)
            verified = row is not None and row.revision == turn.session_revision
            if turn.audit.evaluation_id:
                evaluation = db.get(LearningEvaluation, turn.audit.evaluation_id)
                evidence_count = db.scalar(
                    select(func.count(MasteryEvidence.id)).where(
                        MasteryEvidence.evaluation_id == turn.audit.evaluation_id
                    )
                )
                review_count = db.scalar(
                    select(func.count(LearningReviewItem.id)).where(
                        LearningReviewItem.evaluation_id == turn.audit.evaluation_id
                    )
                )
                verified = (
                    verified
                    and evaluation is not None
                    and bool(evidence_count or review_count)
                )
        if not verified:
            raise PersistenceConflict("adaptive session readback failed")
        return turn.model_copy(
            update={"audit": turn.audit.model_copy(update={"readback_verified": True})}
        )

    def _normalize_blueprint(
        self, command: SessionStartCommand, items: tuple[AdaptiveItem, ...]
    ) -> dict[str, object]:
        if command.mode == "training":
            maximum = command.blueprint.get("max_attempts", 20)
            if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 500:
                raise ValueError("training max_attempts must be between 1 and 500")
            return {"max_attempts": maximum}
        count = command.blueprint.get("item_count")
        types = command.blueprint.get("item_types", [])
        bands = command.blueprint.get("difficulty_bands", [])
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 200:
            raise ValueError("exam item_count is invalid")
        if not isinstance(types, list) or any(not isinstance(value, str) for value in types):
            raise ValueError("exam item_types are invalid")
        if not isinstance(bands, list):
            raise ValueError("exam difficulty bands are invalid")
        eligible = [item for item in items if not types or item.item_type in types]
        selected: list[AdaptiveItem] = []
        for band in bands:
            if (
                not isinstance(band, list)
                or len(band) != 2
                or any(isinstance(value, bool) or not isinstance(value, int | float) for value in band)
            ):
                raise ValueError("exam difficulty band is invalid")
            low, high = float(band[0]), float(band[1])
            candidates = [item for item in eligible if low <= item.difficulty <= high and item not in selected]
            if candidates:
                midpoint = (low + high) / 2
                selected.append(min(candidates, key=lambda item: (abs(item.difficulty - midpoint), item.id)))
            if len(selected) == count:
                break
        for item in sorted(eligible, key=lambda value: (value.difficulty, value.id)):
            if item not in selected and len(selected) < count:
                selected.append(item)
        if len(selected) != count:
            raise LookupError("exam blueprint cannot be satisfied")
        return {
            "item_count": count,
            "item_types": list(types),
            "difficulty_bands": bands,
            "selected_item_ids": [item.id for item in selected],
        }

    def _catalog(self, db: Session, course_id: str, revision: int) -> tuple[AdaptiveItem, ...]:
        concept = db.scalar(
            select(AdaptiveConcept.id).where(
                AdaptiveConcept.course_id == course_id,
                AdaptiveConcept.course_revision == revision,
            ).limit(1)
        )
        if concept is None:
            raise LookupError("adaptive course revision has no concepts")
        admitted_types = set(_DETERMINISTIC_TYPES)
        if self._rubric_evaluator is not None:
            admitted_types.update(_RUBRIC_TYPES)
        return tuple(
            db.scalars(
                select(AdaptiveItem)
                .where(
                    AdaptiveItem.course_id == course_id,
                    AdaptiveItem.course_revision == revision,
                    AdaptiveItem.quality_status == "approved",
                    AdaptiveItem.item_type.in_(admitted_types),
                )
                .order_by(AdaptiveItem.id)
            ).all()
        )

    @staticmethod
    def _version(item: AdaptiveItem, key: str, default: str) -> str:
        value = item.scoring_config.get(key, default)
        if not isinstance(value, str) or not value or len(value) > 200:
            raise ValueError(f"rubric item {key} is invalid")
        return value

    @staticmethod
    def _concepts_by_item(db: Session, course_id: str, revision: int) -> dict[str, tuple[str, ...]]:
        values: dict[str, list[str]] = {}
        for item_id, concept_id in db.execute(
            select(ItemConcept.item_id, ItemConcept.concept_id).where(
                ItemConcept.course_id == course_id,
                ItemConcept.course_revision == revision,
            )
        ):
            values.setdefault(item_id, []).append(concept_id)
        return {key: tuple(sorted(value)) for key, value in values.items()}

    @staticmethod
    def _current_attempt(db: Session, session_id: str) -> LearningAttempt | None:
        return db.scalar(
            select(LearningAttempt)
            .where(LearningAttempt.session_id == session_id)
            .order_by(LearningAttempt.ordinal.desc())
            .limit(1)
        )

    def _public_task(self, item: AdaptiveItem) -> PublicTask:
        return PublicTask(
            id=item.id,
            type=item.item_type,
            prompt=self._string_config(item, "prompt"),
            options=self._options(item),
            difficulty=item.difficulty,
            representation=self._representation(item),
            source_refs=self._string_list(item.scoring_config.get("source_refs", [])),
            presentation=self._presentation(item),
        )

    @staticmethod
    def _options(item: AdaptiveItem) -> tuple[ChoiceOption, ...]:
        raw = item.scoring_config.get("options", [])
        if not isinstance(raw, list):
            raise ValueError("adaptive item options are invalid")
        return tuple(ChoiceOption.model_validate(value) for value in raw)

    @staticmethod
    def _string_config(item: AdaptiveItem, key: str) -> str:
        value = item.scoring_config.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"adaptive item {key} is missing")
        return value

    @staticmethod
    def _string_list(value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("adaptive item string list is invalid")
        return tuple(value)

    def _representation(self, item: AdaptiveItem) -> str:
        value = item.scoring_config.get("representation", item.item_type)
        if not isinstance(value, str) or not value:
            raise ValueError("adaptive item representation is invalid")
        return value

    @staticmethod
    def _presentation(item: AdaptiveItem) -> dict[str, object]:
        allowed_by_type = {
            "matching": frozenset({"pairs"}),
            "numeric": frozenset({"unit"}),
            "penecho_canvas": frozenset({"canvas_href"}),
        }
        allowed = allowed_by_type.get(item.item_type, frozenset())
        raw = item.scoring_config.get("presentation", {})
        if not isinstance(raw, dict):
            raise ValueError("adaptive item presentation is invalid")
        return {key: value for key, value in raw.items() if key in allowed}

    @staticmethod
    def _feedback(evaluation: LearningEvaluation) -> TurnFeedback:
        state = (
            "needs_review"
            if not evaluation.accepted
            else "correct"
            if evaluation.score >= 0.999
            else "partial"
            if evaluation.score >= 0.65
            else "incorrect"
        )
        return TurnFeedback(
            state=state,
            score=evaluation.score,
            rationale_codes=tuple(evaluation.rationale_codes),
            source_refs=tuple(evaluation.source_refs),
        )

    @staticmethod
    def _total(row: LearningSession) -> int:
        key = "max_attempts" if row.mode == "training" else "item_count"
        value = row.blueprint.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise PersistenceConflict("adaptive session blueprint is invalid")
        return value

    @staticmethod
    def _assert_owned(row: LearningSession | None, actor_id: str) -> None:
        if row is None:
            raise LookupError("adaptive session not found")
        if row.actor_id != actor_id:
            raise PermissionError("adaptive session belongs to another actor")

    def _assert_owned_active(self, row: LearningSession | None, actor_id: str) -> None:
        self._assert_owned(row, actor_id)
        assert row is not None
        if row.state != "active":
            raise PersistenceConflict("adaptive session is not active")

    def _aware_now(self) -> datetime:
        return self._aware(self._clock())

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
