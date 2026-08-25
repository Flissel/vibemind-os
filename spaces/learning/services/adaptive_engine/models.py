from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from spaces.learning.services.db.models import Base, utc_now


class AdaptiveConcept(Base):
    __tablename__ = "learning_adaptive_concepts"
    __table_args__ = (
        UniqueConstraint("course_id", "course_revision", "concept_key"),
        UniqueConstraint("id", "course_id", "course_revision"),
        CheckConstraint("course_revision >= 1"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    course_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    concept_key: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ConceptDependency(Base):
    __tablename__ = "learning_adaptive_concept_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["prerequisite_concept_id", "course_id", "course_revision"],
            [
                "learning_adaptive_concepts.id",
                "learning_adaptive_concepts.course_id",
                "learning_adaptive_concepts.course_revision",
            ],
        ),
        ForeignKeyConstraint(
            ["dependent_concept_id", "course_id", "course_revision"],
            [
                "learning_adaptive_concepts.id",
                "learning_adaptive_concepts.course_id",
                "learning_adaptive_concepts.course_revision",
            ],
        ),
        UniqueConstraint(
            "course_id",
            "course_revision",
            "prerequisite_concept_id",
            "dependent_concept_id",
        ),
        CheckConstraint("course_revision >= 1"),
        CheckConstraint("prerequisite_concept_id <> dependent_concept_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    prerequisite_concept_id: Mapped[str] = mapped_column(String(36), nullable=False)
    dependent_concept_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AdaptiveItem(Base):
    __tablename__ = "learning_adaptive_items"
    __table_args__ = (
        UniqueConstraint("course_id", "course_revision", "activity_id"),
        UniqueConstraint("id", "course_id", "course_revision"),
        CheckConstraint("course_revision >= 1"),
        CheckConstraint("difficulty >= 0 AND difficulty <= 1"),
        CheckConstraint(
            "item_type IN ('single_choice','multiple_choice','matching','ordering',"
            "'numeric','fill_in','open','case','code','penecho_canvas')"
        ),
        CheckConstraint("quality_status IN ('approved','review','rejected')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    course_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    activity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    difficulty: Mapped[float] = mapped_column(Float, nullable=False)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False)
    expected_answer: Mapped[dict] = mapped_column(JSON, nullable=False)
    scoring_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    rubric: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ItemConcept(Base):
    __tablename__ = "learning_adaptive_item_concepts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "course_id", "course_revision"],
            [
                "learning_adaptive_items.id",
                "learning_adaptive_items.course_id",
                "learning_adaptive_items.course_revision",
            ],
        ),
        ForeignKeyConstraint(
            ["concept_id", "course_id", "course_revision"],
            [
                "learning_adaptive_concepts.id",
                "learning_adaptive_concepts.course_id",
                "learning_adaptive_concepts.course_revision",
            ],
        ),
        CheckConstraint("course_revision >= 1"),
    )

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    concept_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class LearningSession(Base):
    __tablename__ = "learning_adaptive_sessions"
    __table_args__ = (
        UniqueConstraint("id", "course_id", "course_revision"),
        CheckConstraint("course_revision >= 1"),
        CheckConstraint("revision >= 1"),
        CheckConstraint("mode IN ('training','exam')"),
        CheckConstraint("state IN ('active','completed','cancelled')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    course_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    blueprint: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LearningAttempt(Base):
    __tablename__ = "learning_adaptive_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "course_id", "course_revision"],
            [
                "learning_adaptive_sessions.id",
                "learning_adaptive_sessions.course_id",
                "learning_adaptive_sessions.course_revision",
            ],
        ),
        ForeignKeyConstraint(
            ["item_id", "course_id", "course_revision"],
            [
                "learning_adaptive_items.id",
                "learning_adaptive_items.course_id",
                "learning_adaptive_items.course_revision",
            ],
        ),
        UniqueConstraint("session_id", "ordinal"),
        CheckConstraint("ordinal >= 1"),
        CheckConstraint("course_revision >= 1"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_reason: Mapped[dict] = mapped_column(JSON, nullable=False)
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class LearningResponse(Base):
    __tablename__ = "learning_adaptive_responses"
    __table_args__ = (
        UniqueConstraint("attempt_id", "response_revision"),
        CheckConstraint("response_revision >= 1"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_adaptive_attempts.id"), nullable=False
    )
    response_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    answer: Mapped[dict] = mapped_column(JSON, nullable=False)
    answer_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class LearningEvaluation(Base):
    __tablename__ = "learning_adaptive_evaluations"
    __table_args__ = (
        UniqueConstraint("response_id"),
        CheckConstraint("score >= 0 AND score <= 1"),
        CheckConstraint("confidence >= 0 AND confidence <= 1"),
        CheckConstraint("evaluator_type IN ('deterministic','rubric','manual')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    response_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_adaptive_responses.id"), nullable=False
    )
    evaluator_type: Mapped[str] = mapped_column(String(24), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rationale_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evaluator_versions: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ConceptMastery(Base):
    __tablename__ = "learning_adaptive_mastery"
    __table_args__ = (
        CheckConstraint("alpha > 0"),
        CheckConstraint("beta > 0"),
        CheckConstraint("revision >= 1"),
    )

    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    concept_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_adaptive_concepts.id"), primary_key=True
    )
    alpha: Mapped[float] = mapped_column(Float, nullable=False)
    beta: Mapped[float] = mapped_column(Float, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MasteryEvidence(Base):
    __tablename__ = "learning_adaptive_mastery_evidence"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "concept_id"),
        CheckConstraint("applied_weight > 0 AND applied_weight <= 1"),
        CheckConstraint("applied_score >= 0 AND applied_score <= 1"),
        CheckConstraint("alpha_before > 0 AND beta_before > 0"),
        CheckConstraint("alpha_after > 0 AND beta_after > 0"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_adaptive_evaluations.id"), nullable=False
    )
    concept_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_adaptive_concepts.id"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    applied_weight: Mapped[float] = mapped_column(Float, nullable=False)
    applied_score: Mapped[float] = mapped_column(Float, nullable=False)
    alpha_before: Mapped[float] = mapped_column(Float, nullable=False)
    beta_before: Mapped[float] = mapped_column(Float, nullable=False)
    alpha_after: Mapped[float] = mapped_column(Float, nullable=False)
    beta_after: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ReviewSchedule(Base):
    __tablename__ = "learning_adaptive_review_schedules"
    __table_args__ = (
        CheckConstraint("interval_index >= 0 AND interval_index <= 4"),
        CheckConstraint("revision >= 1"),
    )

    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    concept_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_adaptive_concepts.id"), primary_key=True
    )
    interval_index: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False)
    alternate_representation_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    last_evaluation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_adaptive_evaluations.id"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Misconception(Base):
    __tablename__ = "learning_adaptive_misconceptions"
    __table_args__ = (
        UniqueConstraint("actor_id", "concept_id", "tag"),
        CheckConstraint("status IN ('unresolved','resolved')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    concept_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_adaptive_concepts.id"), nullable=False
    )
    tag: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    first_evaluation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_adaptive_evaluations.id"), nullable=False
    )
    latest_evaluation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_adaptive_evaluations.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LearningReviewItem(Base):
    __tablename__ = "learning_adaptive_review_items"
    __table_args__ = (
        UniqueConstraint("request_ref", "reason_code"),
        CheckConstraint("status IN ('pending','resolved')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("learning_adaptive_evaluations.id")
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
