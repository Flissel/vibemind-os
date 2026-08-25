from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from spaces.learning.services.db.models import (
    LearningOutboxRecord,
    LearningTerminalEvidence,
)
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceChunk,
    LearningSourceRevision,
)
from spaces.learning.services.ingestion.qdrant_index import (
    QdrantPoint,
    QdrantUnavailable,
)
from spaces.learning.services.ingestion.retrieval import (
    EmbeddingGateway,
    GroundedRetrievalUnavailable,
)


SessionFactory = Callable[[], Session]
_DEAD_LETTER_NAMESPACE = UUID("1485ce44-52dd-5af4-94b2-723c748e18ad")


class ProjectionIndex(Protocol):
    def ensure_schema(self) -> None: ...

    def upsert(self, points: list[QdrantPoint]) -> None: ...

    def tombstone(
        self, point_ids: list[str], *, source_id: str, source_revision: int
    ) -> None: ...


@dataclass(frozen=True)
class ProjectionChunk:
    chunk_id: str
    ordinal: int
    content: str
    content_hash: str
    locator: dict[str, object]
    metadata: dict[str, object]


@dataclass(frozen=True)
class ProjectionTask:
    outbox_id: str
    claim_token: str
    source_id: str
    source_revision: int
    course_id: str
    source_title: str
    artifact_id: str
    ingestion_spec_version: str
    chunks: tuple[ProjectionChunk, ...]
    previous_chunk_ids: tuple[str, ...]


class QdrantOutboxWorker:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        index: ProjectionIndex,
        embedder: EmbeddingGateway,
        maximum_attempts: int = 5,
        retry_delay_seconds: float = 2.0,
        lease_seconds: float = 60.0,
    ) -> None:
        if maximum_attempts < 1 or min(retry_delay_seconds, lease_seconds) < 0:
            raise ValueError("worker retry and lease settings are invalid")
        self._session_factory = session_factory
        self._index = index
        self._embedder = embedder
        self._maximum_attempts = maximum_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._lease_seconds = lease_seconds

    def run_once(self) -> bool:
        task = self._claim()
        if task is None:
            return False
        try:
            self._index.ensure_schema()
            if task.previous_chunk_ids:
                self._index.tombstone(
                    list(task.previous_chunk_ids),
                    source_id=task.source_id,
                    source_revision=task.source_revision,
                )
            vectors = self._embedder.embed([chunk.content for chunk in task.chunks])
            if len(vectors) != len(task.chunks):
                raise RuntimeError("embedding count mismatch")
            self._index.upsert(
                [
                    QdrantPoint(
                        point_id=chunk.chunk_id,
                        vector=vectors[index],
                        payload={
                            "chunk_id": chunk.chunk_id,
                            "course_id": task.course_id,
                            "source_id": task.source_id,
                            "source_revision": task.source_revision,
                            "source_title": task.source_title,
                            "artifact_id": task.artifact_id,
                            "ordinal": chunk.ordinal,
                            "content": chunk.content,
                            "content_hash": chunk.content_hash,
                            "locator": chunk.locator,
                            "metadata": chunk.metadata,
                            "ingestion_spec_version": task.ingestion_spec_version,
                            "tombstone": False,
                        },
                    )
                    for index, chunk in enumerate(task.chunks)
                ]
            )
        except Exception as error:
            self._fail(task, error_code=_error_code(error))
            return True
        self._complete(task)
        return True

    def _claim(self) -> ProjectionTask | None:
        now = datetime.now(timezone.utc)
        with self._session_factory() as session, session.begin():
            candidates = _candidate_rows(
                session, maximum_attempts=self._maximum_attempts
            )
            outbox = None
            for candidate in candidates:
                if not _claim_is_due(
                    candidate,
                    now=now,
                    retry_delay_seconds=self._retry_delay_seconds,
                    lease_seconds=self._lease_seconds,
                ) or _has_prior_open_projection(session, candidate):
                    continue
                locked = session.scalar(
                    select(LearningOutboxRecord)
                    .where(LearningOutboxRecord.id == candidate.id)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
                if (
                    locked is not None
                    and _eligible_for_claim(
                        locked, maximum_attempts=self._maximum_attempts
                    )
                    and _claim_is_due(
                        locked,
                        now=now,
                        retry_delay_seconds=self._retry_delay_seconds,
                        lease_seconds=self._lease_seconds,
                    )
                    and not _has_prior_open_projection(session, locked)
                ):
                    outbox = locked
                    break
            if outbox is None:
                return None
            recovering_lease = outbox.status == "processing"
            outbox.status = "processing"
            if not recovering_lease:
                outbox.attempts += 1
            outbox.updated_at = now
            session.flush()
            revision_number = int(outbox.payload["source_revision"])
            revision = session.get(
                LearningSourceRevision, (outbox.aggregate_id, revision_number)
            )
            source = session.get(LearningSource, outbox.aggregate_id)
            if revision is None or source is None or revision.status not in {
                "stored",
                "indexed",
            }:
                raise RuntimeError("outbox source revision is unavailable")
            chunks = session.scalars(
                select(LearningSourceChunk)
                .where(
                    LearningSourceChunk.source_id == outbox.aggregate_id,
                    LearningSourceChunk.source_revision == revision_number,
                )
                .order_by(LearningSourceChunk.ordinal)
            ).all()
            previous_chunks = session.scalars(
                select(LearningSourceChunk).where(
                    LearningSourceChunk.source_id == outbox.aggregate_id,
                    LearningSourceChunk.source_revision < revision_number,
                )
            ).all()
            if not chunks:
                raise RuntimeError("outbox source revision has no chunks")
            return ProjectionTask(
                outbox_id=outbox.id,
                claim_token=_timestamp_token(outbox.updated_at),
                source_id=outbox.aggregate_id,
                source_revision=revision_number,
                course_id=source.course_id,
                source_title=source.title,
                artifact_id=revision.artifact_id,
                ingestion_spec_version=revision.ingestion_spec_version,
                chunks=tuple(
                    ProjectionChunk(
                        chunk_id=chunk.id,
                        ordinal=chunk.ordinal,
                        content=chunk.content,
                        content_hash=chunk.content_hash,
                        locator=dict(chunk.locator),
                        metadata=dict(chunk.metadata_json),
                    )
                    for chunk in chunks
                ),
                previous_chunk_ids=tuple(chunk.id for chunk in previous_chunks),
            )

    def _complete(self, task: ProjectionTask) -> bool:
        with self._session_factory() as session, session.begin():
            outbox = session.get(LearningOutboxRecord, task.outbox_id)
            revision = session.get(
                LearningSourceRevision, (task.source_id, task.source_revision)
            )
            if (
                outbox is None
                or revision is None
                or outbox.status != "processing"
                or _timestamp_token(outbox.updated_at) != task.claim_token
            ):
                return False
            outbox.status = "completed"
            revision.status = "indexed"
            return True

    def _fail(self, task: ProjectionTask, *, error_code: str) -> bool:
        with self._session_factory() as session, session.begin():
            outbox = session.get(LearningOutboxRecord, task.outbox_id)
            if (
                outbox is None
                or outbox.status != "processing"
                or _timestamp_token(outbox.updated_at) != task.claim_token
            ):
                return False
            if outbox.attempts < self._maximum_attempts:
                outbox.status = "retry"
                return True
            outbox.status = "dead_letter"
            invocation_id = str(uuid5(_DEAD_LETTER_NAMESPACE, outbox.id))
            existing = session.scalar(
                select(LearningTerminalEvidence).where(
                    LearningTerminalEvidence.invocation_id == invocation_id
                )
            )
            if existing is None:
                session.add(
                    LearningTerminalEvidence(
                        id=str(uuid4()),
                        invocation_id=invocation_id,
                        correlation_id=str(
                            uuid5(_DEAD_LETTER_NAMESPACE, f"correlation:{outbox.id}")
                        ),
                        owner="learning.qdrant_projection",
                        aggregate_type="source",
                        aggregate_id=task.source_id,
                        aggregate_revision=task.source_revision,
                        terminal_state="failed",
                        evidence_type="qdrant_dead_letter",
                        payload={
                            "outbox_id": outbox.id,
                            "attempts": outbox.attempts,
                            "error_code": error_code,
                        },
                    )
                )
            return True


def _claim_is_due(
    outbox: LearningOutboxRecord,
    *,
    now: datetime,
    retry_delay_seconds: float,
    lease_seconds: float,
) -> bool:
    updated_at = outbox.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if outbox.status == "processing":
        return now - updated_at >= timedelta(seconds=lease_seconds)
    if outbox.status == "retry":
        delay = retry_delay_seconds * (2 ** max(outbox.attempts - 1, 0))
        return now - updated_at >= timedelta(seconds=delay)
    return True


def _timestamp_token(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _has_prior_open_projection(
    session: Session, candidate: LearningOutboxRecord
) -> bool:
    candidate_revision = int(candidate.payload["source_revision"])
    open_rows = session.scalars(
        select(LearningOutboxRecord).where(
            LearningOutboxRecord.topic
            == "learning.qdrant.source_revision.upsert",
            LearningOutboxRecord.aggregate_id == candidate.aggregate_id,
            LearningOutboxRecord.status.not_in(("completed", "dead_letter")),
        )
    ).all()
    return any(
        int(row.payload["source_revision"]) < candidate_revision
        or (
            int(row.payload["source_revision"]) == candidate_revision
            and (row.created_at, row.id) < (candidate.created_at, candidate.id)
        )
        for row in open_rows
        if row.id != candidate.id
    )


def _candidate_rows(
    session: Session, *, maximum_attempts: int
) -> list[LearningOutboxRecord]:
    base_conditions = (
        LearningOutboxRecord.topic == "learning.qdrant.source_revision.upsert",
    )
    rows = list(
        session.scalars(
            select(LearningOutboxRecord)
            .where(
                *base_conditions,
                LearningOutboxRecord.status == "processing",
                LearningOutboxRecord.attempts <= maximum_attempts,
            )
            .order_by(LearningOutboxRecord.updated_at, LearningOutboxRecord.id)
            .limit(100)
        ).all()
    )
    for attempts in range(1, maximum_attempts):
        rows.extend(
            session.scalars(
                select(LearningOutboxRecord)
                .where(
                    *base_conditions,
                    LearningOutboxRecord.status == "retry",
                    LearningOutboxRecord.attempts == attempts,
                )
                .order_by(LearningOutboxRecord.updated_at, LearningOutboxRecord.id)
                .limit(100)
            ).all()
        )
    rows.extend(
        session.scalars(
            select(LearningOutboxRecord)
            .where(
                *base_conditions,
                LearningOutboxRecord.status == "pending",
                LearningOutboxRecord.attempts < maximum_attempts,
            )
            .order_by(LearningOutboxRecord.created_at, LearningOutboxRecord.id)
            .limit(100)
        ).all()
    )
    return rows


def _eligible_for_claim(
    outbox: LearningOutboxRecord, *, maximum_attempts: int
) -> bool:
    if outbox.status in {"pending", "retry"}:
        return outbox.attempts < maximum_attempts
    if outbox.status == "processing":
        return outbox.attempts <= maximum_attempts
    return False


def _error_code(error: Exception) -> str:
    if isinstance(error, QdrantUnavailable):
        return "qdrant_unavailable"
    if isinstance(error, GroundedRetrievalUnavailable):
        return "embedding_unavailable"
    if isinstance(error, ValueError):
        return "projection_validation_failed"
    return "projection_worker_failed"
