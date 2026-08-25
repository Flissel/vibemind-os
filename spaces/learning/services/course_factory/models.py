from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from spaces.learning.services.db.models import Base, utc_now


_STATE_VALUES = (
    "'queued','ingesting','structuring','authoring','assessing','verifying',"
    "'quality_gate','review_ready','published','failed','cancelled','rejected'"
)
_TERMINAL_VALUES = "'published','failed','cancelled','rejected'"


class CourseFactoryJob(Base):
    __tablename__ = "learning_course_factory_jobs"
    __table_args__ = (
        CheckConstraint(f"state IN ({_STATE_VALUES})"),
        CheckConstraint("current_attempt >= 1"),
        CheckConstraint("revision >= 1"),
        CheckConstraint(
            f"(state IN ({_TERMINAL_VALUES}) AND terminal_at IS NOT NULL) OR "
            f"(state NOT IN ({_TERMINAL_VALUES}) AND terminal_at IS NULL)"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    current_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class CourseFactoryAttempt(Base):
    __tablename__ = "learning_course_factory_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number"),
        UniqueConstraint("job_id", "id"),
        CheckConstraint("attempt_number >= 1"),
        CheckConstraint(
            f"(terminal_state IN ({_TERMINAL_VALUES}) AND terminal_at IS NOT NULL) OR "
            "(terminal_state IS NULL AND terminal_at IS NULL)"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_course_factory_jobs.id"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_of_attempt_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("learning_course_factory_attempts.id")
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict | None] = mapped_column(JSON)
    provenance_json: Mapped[list[dict]] = mapped_column("provenance", JSON, nullable=False)
    terminal_state: Mapped[str | None] = mapped_column(String(24))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class CourseFactoryTransition(Base):
    __tablename__ = "learning_course_factory_transitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "attempt_id"],
            ["learning_course_factory_attempts.job_id", "learning_course_factory_attempts.id"],
        ),
        UniqueConstraint("job_id", "job_revision"),
        CheckConstraint("job_revision >= 2"),
        CheckConstraint(f"from_state IN ({_STATE_VALUES})"),
        CheckConstraint(f"to_state IN ({_STATE_VALUES})"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    job_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str] = mapped_column(String(24), nullable=False)
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class CourseFactoryStageArtifact(Base):
    __tablename__ = "learning_course_factory_stage_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "attempt_id"],
            ["learning_course_factory_attempts.job_id", "learning_course_factory_attempts.id"],
        ),
        UniqueConstraint("attempt_id", "stage"),
        CheckConstraint(
            "stage IN ('ingesting','structuring','authoring','assessing',"
            "'verifying','quality_gate')"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_artifact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("learning_artifacts.id"), unique=True
    )
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
