from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.course_factory.models import (
    CourseFactoryAttempt,
    CourseFactoryJob,
    CourseFactoryStageArtifact,
    CourseFactoryTransition,
)
from spaces.learning.services.course_factory.repository import (
    CourseFactoryRepository,
    SourceProvenance,
    StageArtifactInput,
)
from spaces.learning.services.course_factory.state_machine import (
    FactoryState,
    InvalidFactoryTransition,
)
from spaces.learning.services.db.models import Base
from spaces.learning.services.db.repository import PersistenceConflict
from spaces.learning.deployment.migrate import migrate


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture()
def session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'factory.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def postgres_session_factory():
    database_url = os.environ.get("TEST_LEARNING_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_LEARNING_DATABASE_URL is required for PostgreSQL evidence")
    if not database_url.startswith("postgresql+psycopg://") or "_test" not in database_url:
        raise RuntimeError("PostgreSQL integration requires a dedicated _test database")
    migrate(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


def _provenance() -> tuple[SourceProvenance, ...]:
    return (
        SourceProvenance(
            source_id=str(uuid4()),
            revision=2,
            content_hash=_hash("source-r2"),
        ),
    )


def _artifact(stage: FactoryState) -> StageArtifactInput:
    return StageArtifactInput(
        stage=stage,
        input_hash=_hash(f"{stage.value}-input"),
        output_hash=_hash(f"{stage.value}-output"),
        evidence_refs=(f"artifact://{stage.value}/1",),
    )


def _advance_to_review_ready(
    repository: CourseFactoryRepository, job_id: str, revision: int
):
    job = repository.advance(
        job_id,
        expected_revision=revision,
        target=FactoryState.INGESTING,
    )
    for current, target in zip(
        (
            FactoryState.INGESTING,
            FactoryState.STRUCTURING,
            FactoryState.AUTHORING,
            FactoryState.ASSESSING,
            FactoryState.VERIFYING,
            FactoryState.QUALITY_GATE,
        ),
        (
            FactoryState.STRUCTURING,
            FactoryState.AUTHORING,
            FactoryState.ASSESSING,
            FactoryState.VERIFYING,
            FactoryState.QUALITY_GATE,
            FactoryState.REVIEW_READY,
        ),
    ):
        job = repository.advance(
            job_id,
            expected_revision=job.revision,
            target=target,
            artifact=_artifact(current),
        )
    return job


def test_job_persists_provenance_and_every_stage_without_overwrite(
    session_factory,
) -> None:
    repository = CourseFactoryRepository(session_factory)
    job = repository.create_job(
        course_id=str(uuid4()),
        request_hash=_hash("request-v1"),
        provenance=_provenance(),
    )

    assert job.revision == 1
    assert job.state is FactoryState.QUEUED
    assert job.attempt_number == 1
    job = _advance_to_review_ready(repository, job.id, 1)

    assert job.state is FactoryState.REVIEW_READY
    with session_factory() as session:
        attempt = session.scalar(select(CourseFactoryAttempt))
        stages = session.scalars(
            select(CourseFactoryStageArtifact).order_by(
                CourseFactoryStageArtifact.created_at
            )
        ).all()
        transitions = session.scalars(
            select(CourseFactoryTransition).order_by(
                CourseFactoryTransition.job_revision
            )
        ).all()

    assert attempt is not None
    assert attempt.provenance_json[0]["revision"] == 2
    assert [row.stage for row in stages] == [
        "ingesting",
        "structuring",
        "authoring",
        "assessing",
        "verifying",
        "quality_gate",
    ]
    assert len(transitions) == 7
    assert transitions[-1].to_state == "review_ready"

    with pytest.raises(PersistenceConflict, match="artifact"):
        repository.record_stage_artifact(
            job.id,
            expected_revision=job.revision,
            artifact=_artifact(FactoryState.QUALITY_GATE),
        )


def test_retry_creates_new_attempt_and_preserves_terminal_lineage(
    session_factory,
) -> None:
    repository = CourseFactoryRepository(session_factory)
    first = repository.create_job(
        course_id=str(uuid4()),
        request_hash=_hash("request-v1"),
        provenance=_provenance(),
    )
    failed = repository.advance(
        first.id,
        expected_revision=1,
        target=FactoryState.FAILED,
        reason_code="provider_unavailable",
    )
    retried = repository.retry(
        first.id,
        expected_revision=2,
        request_hash=_hash("request-v2"),
        provenance=_provenance(),
    )

    assert failed.terminal_at is not None
    assert retried.state is FactoryState.QUEUED
    assert retried.attempt_number == 2
    assert retried.revision == 3
    assert retried.terminal_at is None

    with session_factory() as session:
        attempts = session.scalars(
            select(CourseFactoryAttempt).order_by(
                CourseFactoryAttempt.attempt_number
            )
        ).all()

    assert len(attempts) == 2
    assert attempts[0].terminal_state == "failed"
    assert attempts[0].terminal_at is not None
    assert attempts[1].retry_of_attempt_id == attempts[0].id
    assert attempts[1].request_hash == _hash("request-v2")
    with session_factory() as session:
        retry_transition = session.scalar(
            select(CourseFactoryTransition).where(
                CourseFactoryTransition.job_revision == 3
            )
        )
    assert retry_transition is not None
    assert retry_transition.from_state == "failed"
    assert retry_transition.to_state == "queued"
    assert retry_transition.attempt_id == attempts[1].id


def test_persisted_stage_output_can_be_advanced_without_rewriting_it(
    session_factory,
) -> None:
    repository = CourseFactoryRepository(session_factory)
    job = repository.create_job(
        course_id=str(uuid4()),
        request_hash=_hash("request"),
        provenance=_provenance(),
    )
    ingesting = repository.advance(
        job.id,
        expected_revision=1,
        target=FactoryState.INGESTING,
    )
    artifact_id = repository.record_stage_artifact(
        job.id,
        expected_revision=ingesting.revision,
        artifact=_artifact(FactoryState.INGESTING),
    )

    structured = repository.advance(
        job.id,
        expected_revision=ingesting.revision,
        target=FactoryState.STRUCTURING,
    )

    assert artifact_id
    assert structured.state is FactoryState.STRUCTURING
    with session_factory() as session:
        artifacts = session.scalars(select(CourseFactoryStageArtifact)).all()
    assert len(artifacts) == 1


def test_revision_conflicts_and_skipped_stages_leave_no_partial_rows(
    session_factory,
) -> None:
    repository = CourseFactoryRepository(session_factory)
    job = repository.create_job(
        course_id=str(uuid4()),
        request_hash=_hash("request"),
        provenance=_provenance(),
    )

    with pytest.raises(PersistenceConflict, match="revision"):
        repository.advance(
            job.id,
            expected_revision=0,
            target=FactoryState.INGESTING,
        )
    with pytest.raises(InvalidFactoryTransition):
        repository.advance(
            job.id,
            expected_revision=1,
            target=FactoryState.STRUCTURING,
        )

    with session_factory() as session:
        stored = session.get(CourseFactoryJob, job.id)
        transitions = session.scalars(select(CourseFactoryTransition)).all()
        artifacts = session.scalars(select(CourseFactoryStageArtifact)).all()

    assert stored is not None
    assert stored.state == "queued"
    assert stored.revision == 1
    assert transitions == []
    assert artifacts == []


@pytest.mark.parametrize(
    ("terminal", "reason"),
    [
        (FactoryState.CANCELLED, "user_cancelled"),
        (FactoryState.FAILED, "stage_failed"),
    ],
)
def test_terminal_attempts_require_reason_and_timestamp(
    session_factory, terminal: FactoryState, reason: str
) -> None:
    repository = CourseFactoryRepository(session_factory)
    job = repository.create_job(
        course_id=str(uuid4()),
        request_hash=_hash("request"),
        provenance=_provenance(),
    )

    with pytest.raises(ValueError, match="reason"):
        repository.advance(job.id, expected_revision=1, target=terminal)

    ended = repository.advance(
        job.id,
        expected_revision=1,
        target=terminal,
        reason_code=reason,
    )

    assert ended.terminal_at is not None


def test_rejection_only_occurs_after_human_review_boundary(session_factory) -> None:
    repository = CourseFactoryRepository(session_factory)
    job = repository.create_job(
        course_id=str(uuid4()),
        request_hash=_hash("request"),
        provenance=_provenance(),
    )

    with pytest.raises(InvalidFactoryTransition):
        repository.advance(
            job.id,
            expected_revision=1,
            target=FactoryState.REJECTED,
            reason_code="review_rejected",
        )

    ready = _advance_to_review_ready(repository, job.id, 1)
    rejected = repository.advance(
        job.id,
        expected_revision=ready.revision,
        target=FactoryState.REJECTED,
        reason_code="review_rejected",
    )
    retried = repository.retry(
        job.id,
        expected_revision=rejected.revision,
        request_hash=_hash("review-corrections"),
        provenance=_provenance(),
    )

    assert rejected.terminal_at is not None
    assert retried.attempt_number == 2
    assert retried.state is FactoryState.QUEUED


def test_postgresql_guards_prior_attempts_and_stage_evidence(
    postgres_session_factory,
) -> None:
    repository = CourseFactoryRepository(postgres_session_factory)
    job = repository.create_job(
        course_id=str(uuid4()),
        request_hash=_hash("postgres-request"),
        provenance=_provenance(),
    )
    job = repository.advance(
        job.id,
        expected_revision=1,
        target=FactoryState.INGESTING,
    )
    repository.record_stage_artifact(
        job.id,
        expected_revision=2,
        artifact=_artifact(FactoryState.INGESTING),
    )
    job = repository.advance(
        job.id,
        expected_revision=2,
        target=FactoryState.STRUCTURING,
    )
    failed = repository.advance(
        job.id,
        expected_revision=3,
        target=FactoryState.FAILED,
        reason_code="stage_failed",
    )
    repository.retry(
        job.id,
        expected_revision=failed.revision,
        request_hash=_hash("postgres-retry"),
        provenance=_provenance(),
    )

    with postgres_session_factory() as session:
        first_attempt = session.scalar(
            select(CourseFactoryAttempt)
            .where(CourseFactoryAttempt.job_id == job.id)
            .order_by(CourseFactoryAttempt.attempt_number)
        )
        stage = session.scalar(
            select(CourseFactoryStageArtifact).where(
                CourseFactoryStageArtifact.job_id == job.id
            )
        )
        assert first_attempt is not None
        assert stage is not None
        with pytest.raises(DBAPIError):
            session.execute(
                update(CourseFactoryAttempt)
                .where(CourseFactoryAttempt.id == first_attempt.id)
                .values(request_hash=_hash("forbidden-overwrite"))
            )
            session.commit()
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(
                update(CourseFactoryStageArtifact)
                .where(CourseFactoryStageArtifact.id == stage.id)
                .values(output_hash=_hash("forbidden-stage-overwrite"))
            )
            session.commit()
