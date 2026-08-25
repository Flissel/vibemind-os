from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.course_factory.quality_gate import QualityGate
from spaces.learning.services.course_factory.repository import (
    CourseFactoryRepository,
    SourceProvenance,
    StageArtifactInput,
)
from spaces.learning.services.course_factory.roles.schemas import QualityReviewOutput
from spaces.learning.services.course_factory.state_machine import FactoryState
from spaces.learning.services.db.models import Base
from spaces.learning.tests.unit.test_source_verification import _catalog, _draft, _ids


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture()
def session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'quality.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _quality_job(repository: CourseFactoryRepository, course_id: str):
    job = repository.create_job(
        course_id=course_id,
        request_hash=_hash("request"),
        provenance=(
            SourceProvenance(
                source_id=str(uuid4()), revision=1, content_hash=_hash("source")
            ),
        ),
    )
    for current, target in (
        (FactoryState.QUEUED, FactoryState.INGESTING),
        (FactoryState.INGESTING, FactoryState.STRUCTURING),
        (FactoryState.STRUCTURING, FactoryState.AUTHORING),
        (FactoryState.AUTHORING, FactoryState.ASSESSING),
        (FactoryState.ASSESSING, FactoryState.VERIFYING),
        (FactoryState.VERIFYING, FactoryState.QUALITY_GATE),
    ):
        artifact = None
        if current is not FactoryState.QUEUED:
            artifact = StageArtifactInput(
                stage=current,
                input_hash=_hash(current.value + "-input"),
                output_hash=_hash(current.value + "-output"),
                evidence_refs=("test://evidence",),
            )
        job = repository.advance(
            job.id,
            expected_revision=job.revision,
            target=target,
            artifact=artifact,
        )
    repository.record_stage_artifact(
        job.id,
        expected_revision=job.revision,
        artifact=StageArtifactInput(
            stage=FactoryState.QUALITY_GATE,
            input_hash=_hash("quality-input"),
            output_hash=_hash("quality-output"),
            evidence_refs=("openfang://completion/quality",),
        ),
    )
    return job


def _review(score: float = 0.9, decision: str = "pass") -> QualityReviewOutput:
    return QualityReviewOutput(
        schema_version="quality-review-v1",
        decision=decision,
        issues=[] if decision == "pass" else ["Needs revision"],
        score=score,
    )


def test_valid_grounded_course_is_promoted_to_review_ready(session_factory) -> None:
    ids = _ids()
    repository = CourseFactoryRepository(session_factory)
    job = _quality_job(repository, str(uuid4()))
    gate = QualityGate(repository)
    draft = _draft(ids)
    draft.job_id = job.id

    result = gate.evaluate_and_promote(
        job.id,
        expected_revision=job.revision,
        draft=draft,
        sources=_catalog(ids),
        ai_review=_review(),
    )

    assert result.report.approved is True
    assert result.job.state is FactoryState.REVIEW_READY


def test_duplicate_content_and_missing_concept_coverage_remain_in_quality_gate(
    session_factory,
) -> None:
    ids = _ids()
    draft = _draft(ids)
    duplicate = draft.lessons[0].model_copy(deep=True)
    duplicate.lesson_id = "lesson-duplicate"
    draft.lessons.append(duplicate)
    draft.chapters[0].lesson_ids.append(duplicate.lesson_id)
    draft.concepts.append(
        draft.concepts[0].model_copy(
            update={"concept_id": "uncovered", "title": "Uncovered"}
        )
    )
    repository = CourseFactoryRepository(session_factory)
    job = _quality_job(repository, str(uuid4()))
    draft.job_id = job.id

    result = QualityGate(repository).evaluate_and_promote(
        job.id,
        expected_revision=job.revision,
        draft=draft,
        sources=_catalog(ids),
        ai_review=_review(),
    )

    assert result.job.state is FactoryState.QUALITY_GATE
    assert "duplicate_lesson" in result.report.issue_codes
    assert "concept_missing_lesson" in result.report.issue_codes
    assert "concept_missing_activity" in result.report.issue_codes


def test_task_distribution_rubric_and_solvability_are_required(session_factory) -> None:
    ids = _ids()
    draft = _draft(ids)
    draft.activities[0].rubric = []
    draft.activities[0].expected_answer = ""
    for index in range(3):
        clone = draft.activities[0].model_copy(deep=True)
        clone.activity_id = f"case-{index}"
        draft.activities.append(clone)
        draft.chapters[0].activity_ids.append(clone.activity_id)
    repository = CourseFactoryRepository(session_factory)
    job = _quality_job(repository, str(uuid4()))
    draft.job_id = job.id

    result = QualityGate(repository).evaluate_and_promote(
        job.id,
        expected_revision=job.revision,
        draft=draft,
        sources=_catalog(ids),
        ai_review=_review(),
    )

    assert result.report.approved is False
    assert "task_type_distribution" in result.report.issue_codes
    assert "rubric_missing" in result.report.issue_codes
    assert "expected_answer_missing" in result.report.issue_codes


@pytest.mark.parametrize(
    "review",
    [_review(score=0.6), _review(decision="revise")],
)
def test_low_confidence_or_revise_ai_review_cannot_promote(
    session_factory, review: QualityReviewOutput
) -> None:
    ids = _ids()
    repository = CourseFactoryRepository(session_factory)
    job = _quality_job(repository, str(uuid4()))
    draft = _draft(ids)
    draft.job_id = job.id

    result = QualityGate(repository).evaluate_and_promote(
        job.id,
        expected_revision=job.revision,
        draft=draft,
        sources=_catalog(ids),
        ai_review=review,
    )

    assert result.job.state is FactoryState.QUALITY_GATE
    assert "ai_review_not_approved" in result.report.issue_codes


def test_draft_must_belong_to_current_job_attempt(session_factory) -> None:
    ids = _ids()
    repository = CourseFactoryRepository(session_factory)
    job = _quality_job(repository, str(uuid4()))
    draft = _draft(ids)

    result = QualityGate(repository).evaluate_and_promote(
        job.id,
        expected_revision=job.revision,
        draft=draft,
        sources=_catalog(ids),
        ai_review=_review(),
    )

    assert result.job.state is FactoryState.QUALITY_GATE
    assert "draft_job_mismatch" in result.report.issue_codes


def test_duplicate_entity_ids_fail_closed(session_factory) -> None:
    ids = _ids()
    draft = _draft(ids)
    draft.concepts.append(draft.concepts[0].model_copy(deep=True))
    repository = CourseFactoryRepository(session_factory)
    job = _quality_job(repository, str(uuid4()))
    draft.job_id = job.id

    result = QualityGate(repository).evaluate_and_promote(
        job.id,
        expected_revision=job.revision,
        draft=draft,
        sources=_catalog(ids),
        ai_review=_review(),
    )

    assert result.job.state is FactoryState.QUALITY_GATE
    assert "duplicate_concept_id" in result.report.issue_codes
