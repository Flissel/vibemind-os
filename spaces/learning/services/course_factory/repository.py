from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field, field_validator

from spaces.learning.services.course_factory.models import (
    CourseFactoryAttempt,
    CourseFactoryJob,
    CourseFactoryStageArtifact,
    CourseFactoryTransition,
)
from spaces.learning.services.course_factory.state_machine import (
    FACTORY_PIPELINE,
    TERMINAL_STATES,
    FactoryState,
    InvalidFactoryTransition,
    require_retryable,
    require_transition,
)
from spaces.learning.services.db.models import utc_now
from spaces.learning.services.db.models import LearningArtifact
from spaces.learning.services.db.repository import PersistenceConflict


SessionFactory = Callable[[], Session]
_HASH = re.compile(r"^[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_ARTIFACT_STAGES = frozenset(FACTORY_PIPELINE[1:-2])


@dataclass(frozen=True)
class SourceProvenance:
    source_id: str
    revision: int
    content_hash: str


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["generation-request-v1"] = "generation-request-v1"
    correlation_id: str = Field(min_length=36, max_length=36)
    audience: str = Field(min_length=1, max_length=1_000)
    target_outcome: str = Field(min_length=1, max_length=2_000)

    @field_validator("correlation_id")
    @classmethod
    def validate_correlation_id(cls, value: str) -> str:
        return str(UUID(value))


@dataclass(frozen=True)
class OutputArtifactInput:
    artifact_id: str
    relative_path: str
    content_hash: str
    media_type: str
    size_bytes: int


@dataclass(frozen=True)
class StageArtifactInput:
    stage: FactoryState
    input_hash: str
    output_hash: str
    evidence_refs: tuple[str, ...]
    output_artifact: OutputArtifactInput | None = None


@dataclass(frozen=True)
class FactoryJobRecord:
    id: str
    course_id: str
    state: FactoryState
    attempt_number: int
    revision: int
    terminal_at: datetime | None


@dataclass(frozen=True)
class FactoryAttemptRecord:
    job_id: str
    attempt_number: int
    request_hash: str
    generation_request: GenerationRequest | None
    provenance: tuple[SourceProvenance, ...]


class CourseFactoryRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create_job(
        self,
        *,
        course_id: str,
        request_hash: str,
        provenance: tuple[SourceProvenance, ...],
        generation_request: GenerationRequest | None = None,
    ) -> FactoryJobRecord:
        course_id = str(UUID(course_id))
        _validate_hash(request_hash)
        provenance_json = _provenance_json(provenance)
        job_id = str(uuid4())
        attempt_id = str(uuid4())
        now = utc_now()
        try:
            with self._session_factory() as session, session.begin():
                job = CourseFactoryJob(
                    id=job_id,
                    course_id=course_id,
                    state=FactoryState.QUEUED.value,
                    current_attempt=1,
                    revision=1,
                    terminal_at=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(job)
                session.flush()
                session.add(
                    CourseFactoryAttempt(
                        id=attempt_id,
                        job_id=job_id,
                        attempt_number=1,
                        retry_of_attempt_id=None,
                        request_hash=request_hash,
                        request_json=(
                            generation_request.model_dump(mode="json")
                            if generation_request is not None
                            else None
                        ),
                        provenance_json=provenance_json,
                        terminal_state=None,
                        terminal_at=None,
                        created_at=now,
                    )
                )
            return _record(job)
        except IntegrityError as error:
            raise PersistenceConflict("course factory job conflict") from error

    def get(self, job_id: str) -> FactoryJobRecord:
        job_id = str(UUID(job_id))
        with self._session_factory() as session:
            job = session.get(CourseFactoryJob, job_id)
            if job is None:
                raise LookupError("course factory job not found")
            return _record(job)

    def get_current_attempt(self, job_id: str) -> FactoryAttemptRecord:
        job_id = str(UUID(job_id))
        with self._session_factory() as session:
            job = session.get(CourseFactoryJob, job_id)
            if job is None:
                raise LookupError("course factory job not found")
            return _attempt_record(_current_attempt(session, job))

    def advance(
        self,
        job_id: str,
        *,
        expected_revision: int,
        target: FactoryState,
        artifact: StageArtifactInput | None = None,
        reason_code: str | None = None,
    ) -> FactoryJobRecord:
        job_id = str(UUID(job_id))
        try:
            with self._session_factory() as session, session.begin():
                job = session.get(CourseFactoryJob, job_id, with_for_update=True)
                if job is None:
                    raise LookupError("course factory job not found")
                if job.revision != expected_revision:
                    raise PersistenceConflict("course factory revision conflict")
                current = FactoryState(job.state)
                require_transition(current, target)
                _validate_terminal_reason(target, reason_code)
                attempt = _current_attempt(session, job)
                if artifact is not None:
                    self._add_stage_artifact(session, job, attempt, artifact, current)
                elif _requires_stage_artifact(current, target) and not _has_stage_artifact(
                    session, attempt.id, current
                ):
                    raise InvalidFactoryTransition(
                        f"stage {current.value} requires immutable output evidence"
                    )

                next_revision = expected_revision + 1
                now = utc_now()
                session.add(
                    CourseFactoryTransition(
                        id=str(uuid4()),
                        job_id=job.id,
                        attempt_id=attempt.id,
                        job_revision=next_revision,
                        from_state=current.value,
                        to_state=target.value,
                        reason_code=reason_code,
                        created_at=now,
                    )
                )
                job.state = target.value
                job.revision = next_revision
                job.updated_at = now
                if target in TERMINAL_STATES:
                    job.terminal_at = now
                    attempt.terminal_state = target.value
                    attempt.terminal_at = now
                session.flush()
                return _record(job)
        except IntegrityError as error:
            raise PersistenceConflict("course factory transition conflict") from error

    def retry(
        self,
        job_id: str,
        *,
        expected_revision: int,
        request_hash: str,
        provenance: tuple[SourceProvenance, ...],
        generation_request: GenerationRequest | None = None,
    ) -> FactoryJobRecord:
        job_id = str(UUID(job_id))
        _validate_hash(request_hash)
        provenance_json = _provenance_json(provenance)
        try:
            with self._session_factory() as session, session.begin():
                job = session.get(CourseFactoryJob, job_id, with_for_update=True)
                if job is None:
                    raise LookupError("course factory job not found")
                if job.revision != expected_revision:
                    raise PersistenceConflict("course factory revision conflict")
                require_retryable(FactoryState(job.state))
                previous = _current_attempt(session, job)
                if previous.terminal_at is None:
                    raise PersistenceConflict("prior factory attempt is not terminal")
                now = utc_now()
                attempt_number = job.current_attempt + 1
                session.add(
                    CourseFactoryAttempt(
                        id=(attempt_id := str(uuid4())),
                        job_id=job.id,
                        attempt_number=attempt_number,
                        retry_of_attempt_id=previous.id,
                        request_hash=request_hash,
                        request_json=(
                            generation_request.model_dump(mode="json")
                            if generation_request is not None
                            else None
                        ),
                        provenance_json=provenance_json,
                        terminal_state=None,
                        terminal_at=None,
                        created_at=now,
                    )
                )
                session.flush()
                session.add(
                    CourseFactoryTransition(
                        id=str(uuid4()),
                        job_id=job.id,
                        attempt_id=attempt_id,
                        job_revision=expected_revision + 1,
                        from_state=job.state,
                        to_state=FactoryState.QUEUED.value,
                        reason_code="retry_requested",
                        created_at=now,
                    )
                )
                job.state = FactoryState.QUEUED.value
                job.current_attempt = attempt_number
                job.revision = expected_revision + 1
                job.terminal_at = None
                job.updated_at = now
                session.flush()
                return _record(job)
        except IntegrityError as error:
            raise PersistenceConflict("course factory retry conflict") from error

    def record_stage_artifact(
        self,
        job_id: str,
        *,
        expected_revision: int,
        artifact: StageArtifactInput,
    ) -> str:
        job_id = str(UUID(job_id))
        try:
            with self._session_factory() as session, session.begin():
                job = session.get(CourseFactoryJob, job_id, with_for_update=True)
                if job is None:
                    raise LookupError("course factory job not found")
                if job.revision != expected_revision:
                    raise PersistenceConflict("course factory revision conflict")
                attempt = _current_attempt(session, job)
                existing = session.scalar(
                    select(CourseFactoryStageArtifact).where(
                        CourseFactoryStageArtifact.attempt_id == attempt.id,
                        CourseFactoryStageArtifact.stage == artifact.stage.value,
                    )
                )
                if existing is not None:
                    raise PersistenceConflict("course factory artifact is immutable")
                return self._add_stage_artifact(
                    session, job, attempt, artifact, FactoryState(job.state)
                ).id
        except IntegrityError as error:
            raise PersistenceConflict("course factory artifact conflict") from error

    @staticmethod
    def _add_stage_artifact(
        session: Session,
        job: CourseFactoryJob,
        attempt: CourseFactoryAttempt,
        artifact: StageArtifactInput,
        current: FactoryState,
    ) -> CourseFactoryStageArtifact:
        _validate_stage_artifact(artifact)
        if artifact.stage is not current:
            raise InvalidFactoryTransition(
                f"artifact stage {artifact.stage.value} does not match {current.value}"
            )
        existing = session.scalar(
            select(CourseFactoryStageArtifact).where(
                CourseFactoryStageArtifact.attempt_id == attempt.id,
                CourseFactoryStageArtifact.stage == artifact.stage.value,
            )
        )
        if existing is not None:
            raise PersistenceConflict("course factory artifact is immutable")
        row = CourseFactoryStageArtifact(
            id=str(uuid4()),
            job_id=job.id,
            attempt_id=attempt.id,
            stage=artifact.stage.value,
            input_hash=artifact.input_hash,
            output_hash=artifact.output_hash,
            output_artifact_id=(
                artifact.output_artifact.artifact_id
                if artifact.output_artifact is not None
                else None
            ),
            evidence_refs=list(artifact.evidence_refs),
            created_at=utc_now(),
        )
        if artifact.output_artifact is not None:
            output = artifact.output_artifact
            session.add(
                LearningArtifact(
                    id=output.artifact_id,
                    relative_path=output.relative_path,
                    content_hash=output.content_hash,
                    media_type=output.media_type,
                    size_bytes=output.size_bytes,
                    aggregate_type="course_factory_stage",
                    aggregate_id=f"{job.id}:{attempt.id}:{artifact.stage.value}",
                    revision=attempt.attempt_number,
                    created_at=utc_now(),
                )
            )
        session.add(row)
        session.flush()
        return row


def _current_attempt(session: Session, job: CourseFactoryJob) -> CourseFactoryAttempt:
    attempt = session.scalar(
        select(CourseFactoryAttempt).where(
            CourseFactoryAttempt.job_id == job.id,
            CourseFactoryAttempt.attempt_number == job.current_attempt,
        )
    )
    if attempt is None:
        raise PersistenceConflict("current course factory attempt is missing")
    return attempt


def _requires_stage_artifact(current: FactoryState, target: FactoryState) -> bool:
    return current in _ARTIFACT_STAGES and target not in {
        FactoryState.FAILED,
        FactoryState.CANCELLED,
    }


def _has_stage_artifact(
    session: Session, attempt_id: str, stage: FactoryState
) -> bool:
    return session.scalar(
        select(CourseFactoryStageArtifact.id).where(
            CourseFactoryStageArtifact.attempt_id == attempt_id,
            CourseFactoryStageArtifact.stage == stage.value,
        )
    ) is not None


def _validate_hash(value: str) -> None:
    if not _HASH.fullmatch(value):
        raise ValueError("factory hashes must be lowercase SHA-256 values")


def _provenance_json(provenance: tuple[SourceProvenance, ...]) -> list[dict]:
    if not provenance or len(provenance) > 256:
        raise ValueError("factory provenance must contain bounded source revisions")
    normalized: list[dict] = []
    seen: set[str] = set()
    for item in provenance:
        source_id = str(UUID(item.source_id))
        if source_id in seen or item.revision < 1:
            raise ValueError("factory provenance source revisions must be unique")
        _validate_hash(item.content_hash)
        seen.add(source_id)
        normalized.append(
            {
                "source_id": source_id,
                "revision": item.revision,
                "content_hash": item.content_hash,
            }
        )
    return sorted(normalized, key=lambda item: item["source_id"])


def _validate_stage_artifact(artifact: StageArtifactInput) -> None:
    if artifact.stage not in _ARTIFACT_STAGES:
        raise ValueError("factory artifact stage is invalid")
    _validate_hash(artifact.input_hash)
    _validate_hash(artifact.output_hash)
    if (
        not artifact.evidence_refs
        or len(artifact.evidence_refs) > 128
        or any(not ref or len(ref) > 512 for ref in artifact.evidence_refs)
    ):
        raise ValueError("factory artifact evidence references are invalid")
    if artifact.output_artifact is not None:
        output = artifact.output_artifact
        UUID(output.artifact_id)
        _validate_hash(output.content_hash)
        if output.content_hash != artifact.output_hash:
            raise ValueError("factory output artifact hash mismatch")
        if (
            not output.relative_path
            or "\\" in output.relative_path
            or output.relative_path.startswith("/")
            or ".." in output.relative_path.split("/")
            or output.media_type != "application/json"
            or output.size_bytes < 2
        ):
            raise ValueError("factory output artifact metadata is invalid")
        expected_ref = f"learning-artifact://{output.artifact_id}"
        if expected_ref not in artifact.evidence_refs:
            raise ValueError("factory output artifact evidence reference is missing")


def _validate_terminal_reason(
    target: FactoryState, reason_code: str | None
) -> None:
    requires_reason = target in {
        FactoryState.FAILED,
        FactoryState.CANCELLED,
        FactoryState.REJECTED,
    }
    if requires_reason and (reason_code is None or not _REASON.fullmatch(reason_code)):
        raise ValueError("terminal factory transitions require a reason code")
    if not requires_reason and reason_code is not None:
        raise ValueError("reason code is only valid for non-success terminal states")


def _record(job: CourseFactoryJob) -> FactoryJobRecord:
    return FactoryJobRecord(
        id=job.id,
        course_id=job.course_id,
        state=FactoryState(job.state),
        attempt_number=job.current_attempt,
        revision=job.revision,
        terminal_at=job.terminal_at,
    )


def _attempt_record(attempt: CourseFactoryAttempt) -> FactoryAttemptRecord:
    request = (
        GenerationRequest.model_validate(attempt.request_json)
        if attempt.request_json is not None
        else None
    )
    provenance = tuple(
        SourceProvenance(
            source_id=item["source_id"],
            revision=item["revision"],
            content_hash=item["content_hash"],
        )
        for item in attempt.provenance_json
    )
    return FactoryAttemptRecord(
        job_id=attempt.job_id,
        attempt_number=attempt.attempt_number,
        request_hash=attempt.request_hash,
        generation_request=request,
        provenance=provenance,
    )
