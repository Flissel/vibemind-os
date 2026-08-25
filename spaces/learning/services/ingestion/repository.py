from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from spaces.learning.services.db.models import (
    LearningArtifact,
    LearningInvocationReceipt,
    LearningOutboxRecord,
    LearningTerminalEvidence,
)
from spaces.learning.services.db.repository import PersistenceConflict
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceChunk,
    LearningSourceRevision,
)


SessionFactory = Callable[[], Session]
_CHUNK_NAMESPACE = UUID("79ef81aa-1cb7-5f90-a96c-0d89eb3f716d")
_FAILURE_NAMESPACE = UUID("dd7269b5-0d4a-5f58-a356-cd311c6123d0")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ArtifactInput:
    relative_path: str
    content_hash: str
    media_type: str
    size_bytes: int


@dataclass(frozen=True)
class SourceChunkInput:
    content: str
    content_hash: str
    locator: dict[str, object]
    metadata: dict[str, object]


@dataclass(frozen=True)
class SourceRevisionRecord:
    source_id: str
    revision: int
    artifact_id: str
    chunk_ids: tuple[str, ...]
    outbox_id: str


@dataclass(frozen=True)
class FailedSourceRevisionRecord:
    source_id: str
    revision: int
    artifact_id: str


def stable_chunk_id(
    *,
    source_id: str,
    source_revision: int,
    ordinal: int,
    content_hash: str,
    locator: dict[str, object],
    ingestion_spec_version: str,
) -> str:
    canonical_source_id = str(UUID(source_id))
    _validate_hash(content_hash)
    if source_revision < 1 or ordinal < 0:
        raise ValueError("source revision and chunk ordinal must be positive")
    if not ingestion_spec_version or len(ingestion_spec_version) > 64:
        raise ValueError("ingestion specification version is required")
    identity = _canonical_json(
        {
            "source_id": canonical_source_id,
            "source_revision": source_revision,
            "ordinal": ordinal,
            "content_hash": content_hash,
            "locator": locator,
            "ingestion_spec_version": ingestion_spec_version,
        }
    )
    return str(uuid5(_CHUNK_NAMESPACE, identity))


def source_request_digest(
    *,
    source_id: str,
    course_id: str,
    title: str,
    expected_revision: int,
    artifact: ArtifactInput,
    ingestion_spec_version: str,
) -> str:
    return _input_digest(
        source_id=str(UUID(source_id)),
        course_id=str(UUID(course_id)),
        title=title,
        expected_revision=expected_revision,
        artifact=artifact,
        ingestion_spec_version=ingestion_spec_version,
        operation="source_ingestion",
    )


class SourceRepository:
    def __init__(self, session_factory: SessionFactory, *, artifact_root: Path) -> None:
        self._session_factory = session_factory
        self._artifact_root = artifact_root.resolve(strict=True)
        if not self._artifact_root.is_dir():
            raise ValueError("Learning artifact root must be a directory")

    def persist_revision(
        self,
        *,
        source_id: str,
        course_id: str,
        title: str,
        expected_revision: int,
        idempotency_key: str,
        artifact: ArtifactInput,
        chunks: list[SourceChunkInput],
        ingestion_spec_version: str = "ingestion-v1",
        request_digest: str | None = None,
    ) -> SourceRevisionRecord:
        source_id = str(UUID(source_id))
        course_id = str(UUID(course_id))
        try:
            return self._persist_revision_once(
                source_id=source_id,
                course_id=course_id,
                title=title,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                artifact=artifact,
                chunks=chunks,
                ingestion_spec_version=ingestion_spec_version,
                request_digest=request_digest,
            )
        except (IntegrityError, PersistenceConflict) as error:
            input_digest = _input_digest(
                source_id=source_id,
                course_id=course_id,
                title=title,
                expected_revision=expected_revision,
                artifact=artifact,
                chunks=chunks,
                ingestion_spec_version=ingestion_spec_version,
            )
            with self._session_factory() as session:
                replay = session.scalar(
                    select(LearningOutboxRecord).where(
                        LearningOutboxRecord.idempotency_key == idempotency_key
                    )
                )
                if (
                    replay is not None
                    and replay.topic == "learning.qdrant.source_revision.upsert"
                    and replay.aggregate_id == source_id
                    and replay.payload.get("input_digest") == input_digest
                    and (
                        request_digest is None
                        or replay.payload.get("request_digest") == request_digest
                    )
                ):
                    return _record_from_outbox(session, replay)
            if isinstance(error, PersistenceConflict):
                raise
            raise PersistenceConflict("source revision conflict") from error

    def find_stored_revision_by_content(
        self,
        *,
        source_id: str,
        course_id: str,
        title: str,
        content_hash: str,
        ingestion_spec_version: str,
    ) -> SourceRevisionRecord | None:
        source_id = str(UUID(source_id))
        course_id = str(UUID(course_id))
        _validate_hash(content_hash)
        with self._session_factory() as session:
            source = session.get(LearningSource, source_id)
            if source is not None and (
                source.course_id != course_id or source.title != title
            ):
                raise PersistenceConflict("source identity is immutable")
            revision = session.scalar(
                select(LearningSourceRevision).where(
                    LearningSourceRevision.source_id == source_id,
                    LearningSourceRevision.content_hash == content_hash,
                    LearningSourceRevision.ingestion_spec_version
                    == ingestion_spec_version,
                    LearningSourceRevision.status == "stored",
                )
            )
            if revision is None:
                return None
            outboxes = session.scalars(
                select(LearningOutboxRecord).where(
                    LearningOutboxRecord.topic
                    == "learning.qdrant.source_revision.upsert",
                    LearningOutboxRecord.aggregate_id == source_id,
                )
            ).all()
            for outbox in outboxes:
                if outbox.payload.get("source_revision") == revision.revision:
                    return _record_from_outbox(session, outbox)
        raise PersistenceConflict("stored source revision is missing its outbox")

    def find_revision_by_content(
        self,
        *,
        source_id: str,
        course_id: str,
        title: str,
        content_hash: str,
        ingestion_spec_version: str,
    ) -> SourceRevisionRecord | FailedSourceRevisionRecord | None:
        stored = self.find_stored_revision_by_content(
            source_id=source_id,
            course_id=course_id,
            title=title,
            content_hash=content_hash,
            ingestion_spec_version=ingestion_spec_version,
        )
        if stored is not None:
            return stored
        with self._session_factory() as session:
            revision = session.scalar(
                select(LearningSourceRevision).where(
                    LearningSourceRevision.source_id == source_id,
                    LearningSourceRevision.content_hash == content_hash,
                    LearningSourceRevision.ingestion_spec_version
                    == ingestion_spec_version,
                    LearningSourceRevision.status == "failed",
                )
            )
            if revision is None:
                return None
            evidence = session.scalar(
                select(LearningTerminalEvidence).where(
                    LearningTerminalEvidence.aggregate_type == "source",
                    LearningTerminalEvidence.aggregate_id == source_id,
                    LearningTerminalEvidence.aggregate_revision == revision.revision,
                    LearningTerminalEvidence.evidence_type == "parser_failure",
                )
            )
            if evidence is None:
                raise PersistenceConflict(
                    "failed source revision is missing terminal evidence"
                )
            return FailedSourceRevisionRecord(
                source_id=source_id,
                revision=revision.revision,
                artifact_id=revision.artifact_id,
            )

    def assert_request_key_compatible(
        self,
        *,
        idempotency_key: str,
        request_digest: str,
    ) -> None:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency key is required")
        _validate_hash(request_digest)
        with self._session_factory() as session:
            receipt = session.get(LearningInvocationReceipt, idempotency_key)
            if receipt is not None and receipt.request_digest != request_digest:
                raise PersistenceConflict("source idempotency conflict")

    def bind_duplicate_request(
        self,
        *,
        idempotency_key: str,
        request_digest: str,
        record: SourceRevisionRecord | FailedSourceRevisionRecord,
    ) -> None:
        result_json = {
            "kind": "learning.source.content_duplicate",
            "source_id": record.source_id,
            "source_revision": record.revision,
            "artifact_id": record.artifact_id,
            "status": (
                "stored" if isinstance(record, SourceRevisionRecord) else "failed"
            ),
        }
        try:
            with self._session_factory() as session, session.begin():
                if session.scalar(
                    select(LearningOutboxRecord).where(
                        LearningOutboxRecord.idempotency_key == idempotency_key
                    )
                ) is not None:
                    raise PersistenceConflict("source idempotency conflict")
                invocation_id = str(uuid5(_FAILURE_NAMESPACE, idempotency_key))
                if session.scalar(
                    select(LearningTerminalEvidence).where(
                        LearningTerminalEvidence.invocation_id == invocation_id
                    )
                ) is not None:
                    raise PersistenceConflict("source idempotency conflict")
                receipt = session.get(LearningInvocationReceipt, idempotency_key)
                if receipt is not None:
                    if (
                        receipt.request_digest != request_digest
                        or receipt.result_json != result_json
                        or not receipt.terminal
                    ):
                        raise PersistenceConflict("source idempotency conflict")
                    return
                session.add(
                    LearningInvocationReceipt(
                        idempotency_key=idempotency_key,
                        request_digest=request_digest,
                        result_json=result_json,
                        terminal=True,
                    )
                )
        except IntegrityError as error:
            with self._session_factory() as session:
                receipt = session.get(LearningInvocationReceipt, idempotency_key)
                if (
                    receipt is not None
                    and receipt.request_digest == request_digest
                    and receipt.result_json == result_json
                    and receipt.terminal
                ):
                    return
            raise PersistenceConflict("source idempotency conflict") from error

    def idempotency_key_belongs_to(
        self,
        idempotency_key: str,
        record: SourceRevisionRecord | FailedSourceRevisionRecord,
        request_digest: str,
    ) -> bool:
        _validate_hash(request_digest)
        with self._session_factory() as session:
            if isinstance(record, SourceRevisionRecord):
                outbox = session.scalar(
                    select(LearningOutboxRecord).where(
                        LearningOutboxRecord.idempotency_key == idempotency_key
                    )
                )
                if (
                    outbox is not None
                    and outbox.id == record.outbox_id
                    and outbox.aggregate_id == record.source_id
                    and outbox.payload.get("source_revision") == record.revision
                    and outbox.payload.get("request_digest") == request_digest
                ):
                    return True
            else:
                evidence = session.scalar(
                    select(LearningTerminalEvidence).where(
                        LearningTerminalEvidence.invocation_id
                        == str(uuid5(_FAILURE_NAMESPACE, idempotency_key))
                    )
                )
                if (
                    evidence is not None
                    and evidence.aggregate_id == record.source_id
                    and evidence.aggregate_revision == record.revision
                    and evidence.payload.get("artifact_id") == record.artifact_id
                    and evidence.payload.get("request_digest") == request_digest
                ):
                    return True
            receipt = session.get(LearningInvocationReceipt, idempotency_key)
            return bool(
                receipt is not None
                and receipt.terminal
                and receipt.result_json.get("source_id") == record.source_id
                and receipt.result_json.get("source_revision") == record.revision
                and receipt.result_json.get("artifact_id") == record.artifact_id
                and receipt.request_digest == request_digest
            )

    def _persist_revision_once(
        self,
        *,
        source_id: str,
        course_id: str,
        title: str,
        expected_revision: int,
        idempotency_key: str,
        artifact: ArtifactInput,
        chunks: list[SourceChunkInput],
        ingestion_spec_version: str = "ingestion-v1",
        request_digest: str | None = None,
    ) -> SourceRevisionRecord:
        _validate_input(
            source_id=source_id,
            course_id=course_id,
            title=title,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            artifact=artifact,
            chunks=chunks,
            ingestion_spec_version=ingestion_spec_version,
            artifact_root=self._artifact_root,
        )
        input_digest = _input_digest(
            source_id=source_id,
            course_id=course_id,
            title=title,
            expected_revision=expected_revision,
            artifact=artifact,
            chunks=chunks,
            ingestion_spec_version=ingestion_spec_version,
        )
        if request_digest is None:
            request_digest = input_digest
        _validate_hash(request_digest)

        with self._session_factory() as session, session.begin():
            replay = session.scalar(
                select(LearningOutboxRecord).where(
                    LearningOutboxRecord.idempotency_key == idempotency_key
                )
            )
            if replay is not None:
                if (
                    replay.topic != "learning.qdrant.source_revision.upsert"
                    or replay.aggregate_type != "source"
                    or replay.aggregate_id != source_id
                    or replay.payload.get("input_digest") != input_digest
                    or replay.payload.get("request_digest") != request_digest
                ):
                    raise PersistenceConflict("source idempotency conflict")
                return _record_from_outbox(session, replay)

            source = session.get(LearningSource, source_id)
            if source is None:
                if expected_revision != 0:
                    raise PersistenceConflict("source revision conflict")
                source = LearningSource(
                    id=source_id,
                    course_id=course_id,
                    title=title,
                    current_revision=1,
                )
                session.add(source)
            elif source.course_id != course_id or source.title != title:
                raise PersistenceConflict("source identity is immutable")
            elif source.current_revision != expected_revision:
                raise PersistenceConflict("source revision conflict")

            revision = expected_revision + 1
            if expected_revision > 0:
                advanced = session.execute(
                    update(LearningSource)
                    .where(
                        LearningSource.id == source_id,
                        LearningSource.current_revision == expected_revision,
                    )
                    .values(current_revision=revision)
                )
                if advanced.rowcount != 1:
                    raise PersistenceConflict("source revision conflict")
            artifact_id = str(uuid4())
            chunk_ids = tuple(
                stable_chunk_id(
                    source_id=source_id,
                    source_revision=revision,
                    ordinal=ordinal,
                    content_hash=chunk.content_hash,
                    locator=chunk.locator,
                    ingestion_spec_version=ingestion_spec_version,
                )
                for ordinal, chunk in enumerate(chunks)
            )
            outbox_id = str(uuid4())

            session.add(
                LearningArtifact(
                    id=artifact_id,
                    relative_path=artifact.relative_path,
                    content_hash=artifact.content_hash,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    aggregate_type="source",
                    aggregate_id=source_id,
                    revision=revision,
                )
            )
            session.add(
                LearningSourceRevision(
                    source_id=source_id,
                    revision=revision,
                    artifact_id=artifact_id,
                    content_hash=artifact.content_hash,
                    ingestion_spec_version=ingestion_spec_version,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    status="stored",
                )
            )
            session.flush()
            session.add_all(
                LearningSourceChunk(
                    id=chunk_ids[ordinal],
                    source_id=source_id,
                    source_revision=revision,
                    ordinal=ordinal,
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                    locator=chunk.locator,
                    locator_hash=_locator_hash(chunk.locator),
                    metadata_json=chunk.metadata,
                )
                for ordinal, chunk in enumerate(chunks)
            )
            session.add(
                LearningOutboxRecord(
                    id=outbox_id,
                    topic="learning.qdrant.source_revision.upsert",
                    idempotency_key=idempotency_key,
                    aggregate_type="source",
                    aggregate_id=source_id,
                    payload={
                        "source_id": source_id,
                        "source_revision": revision,
                        "artifact_id": artifact_id,
                        "chunk_ids": list(chunk_ids),
                        "collection": "learning_knowledge_v1",
                        "ingestion_spec_version": ingestion_spec_version,
                        "input_digest": input_digest,
                        "request_digest": request_digest,
                    },
                )
            )
            return SourceRevisionRecord(
                source_id=source_id,
                revision=revision,
                artifact_id=artifact_id,
                chunk_ids=chunk_ids,
                outbox_id=outbox_id,
            )

    def persist_failed_revision(
        self,
        *,
        source_id: str,
        course_id: str,
        title: str,
        expected_revision: int,
        idempotency_key: str,
        artifact: ArtifactInput,
        ingestion_spec_version: str = "ingestion-v1",
        request_digest: str | None = None,
    ) -> FailedSourceRevisionRecord:
        source_id = str(UUID(source_id))
        course_id = str(UUID(course_id))
        input_digest = _input_digest(
            source_id=source_id,
            course_id=course_id,
            title=title,
            expected_revision=expected_revision,
            artifact=artifact,
            ingestion_spec_version=ingestion_spec_version,
            state="failed",
        )
        invocation_id = str(uuid5(_FAILURE_NAMESPACE, idempotency_key))
        if request_digest is None:
            request_digest = input_digest
        _validate_hash(request_digest)
        try:
            return self._persist_failed_revision_once(
                source_id=source_id,
                course_id=course_id,
                title=title,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                artifact=artifact,
                ingestion_spec_version=ingestion_spec_version,
                request_digest=request_digest,
            )
        except (IntegrityError, PersistenceConflict) as error:
            with self._session_factory() as session:
                evidence = session.scalar(
                    select(LearningTerminalEvidence).where(
                        LearningTerminalEvidence.invocation_id == invocation_id
                    )
                )
                if (
                    evidence is not None
                    and evidence.payload.get("input_digest") == input_digest
                    and evidence.payload.get("request_digest") == request_digest
                ):
                    return FailedSourceRevisionRecord(
                        source_id=evidence.aggregate_id,
                        revision=evidence.aggregate_revision,
                        artifact_id=str(evidence.payload["artifact_id"]),
                    )
            if isinstance(error, PersistenceConflict):
                raise
            raise PersistenceConflict("source failure revision conflict") from error

    def _persist_failed_revision_once(
        self,
        *,
        source_id: str,
        course_id: str,
        title: str,
        expected_revision: int,
        idempotency_key: str,
        artifact: ArtifactInput,
        ingestion_spec_version: str = "ingestion-v1",
        request_digest: str | None = None,
    ) -> FailedSourceRevisionRecord:
        source_id = str(UUID(source_id))
        course_id = str(UUID(course_id))
        _validate_artifact(
            artifact=artifact,
            artifact_root=self._artifact_root,
        )
        if expected_revision < 0:
            raise ValueError("expected revision must not be negative")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency key is required")
        if not title.strip() or len(title) > 200:
            raise ValueError("source title is required")
        if not ingestion_spec_version or len(ingestion_spec_version) > 64:
            raise ValueError("ingestion specification version is required")
        input_digest = _input_digest(
            source_id=source_id,
            course_id=course_id,
            title=title,
            expected_revision=expected_revision,
            artifact=artifact,
            ingestion_spec_version=ingestion_spec_version,
            state="failed",
        )
        if request_digest is None:
            request_digest = input_digest
        _validate_hash(request_digest)
        invocation_id = str(uuid5(_FAILURE_NAMESPACE, idempotency_key))

        with self._session_factory() as session, session.begin():
            evidence = session.scalar(
                select(LearningTerminalEvidence).where(
                    LearningTerminalEvidence.invocation_id == invocation_id
                )
            )
            if evidence is not None:
                if (
                    evidence.payload.get("input_digest") != input_digest
                    or evidence.payload.get("request_digest") != request_digest
                ):
                    raise PersistenceConflict("source failure idempotency conflict")
                return FailedSourceRevisionRecord(
                    source_id=evidence.aggregate_id,
                    revision=evidence.aggregate_revision,
                    artifact_id=str(evidence.payload["artifact_id"]),
                )
            source = session.get(LearningSource, source_id)
            revision = expected_revision + 1
            if source is None:
                if expected_revision != 0:
                    raise PersistenceConflict("source revision conflict")
                session.add(
                    LearningSource(
                        id=source_id,
                        course_id=course_id,
                        title=title,
                        current_revision=revision,
                    )
                )
            elif (
                source.course_id != course_id
                or source.title != title
                or source.current_revision != expected_revision
            ):
                raise PersistenceConflict("source revision conflict")
            else:
                advanced = session.execute(
                    update(LearningSource)
                    .where(
                        LearningSource.id == source_id,
                        LearningSource.current_revision == expected_revision,
                    )
                    .values(current_revision=revision)
                )
                if advanced.rowcount != 1:
                    raise PersistenceConflict("source revision conflict")
            artifact_id = str(uuid4())
            session.add(
                LearningArtifact(
                    id=artifact_id,
                    relative_path=artifact.relative_path,
                    content_hash=artifact.content_hash,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    aggregate_type="source",
                    aggregate_id=source_id,
                    revision=revision,
                )
            )
            session.add(
                LearningSourceRevision(
                    source_id=source_id,
                    revision=revision,
                    artifact_id=artifact_id,
                    content_hash=artifact.content_hash,
                    ingestion_spec_version=ingestion_spec_version,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    status="failed",
                )
            )
            session.add(
                LearningTerminalEvidence(
                    id=str(uuid4()),
                    invocation_id=invocation_id,
                    correlation_id=str(
                        uuid5(_FAILURE_NAMESPACE, f"correlation:{idempotency_key}")
                    ),
                    owner="learning.ingestion",
                    aggregate_type="source",
                    aggregate_id=source_id,
                    aggregate_revision=revision,
                    terminal_state="failed",
                    evidence_type="parser_failure",
                    payload={
                        "artifact_id": artifact_id,
                        "input_digest": input_digest,
                        "request_digest": request_digest,
                        "error_code": "parser_failed",
                    },
                )
            )
            return FailedSourceRevisionRecord(
                source_id=source_id,
                revision=revision,
                artifact_id=artifact_id,
            )


def _record_from_outbox(
    session: Session, outbox: LearningOutboxRecord
) -> SourceRevisionRecord:
    payload = outbox.payload
    revision = int(payload["source_revision"])
    artifact_id = str(payload["artifact_id"])
    chunk_ids = tuple(str(value) for value in payload["chunk_ids"])
    if session.get(LearningSourceRevision, (outbox.aggregate_id, revision)) is None:
        raise PersistenceConflict("source replay is missing its durable revision")
    return SourceRevisionRecord(
        source_id=outbox.aggregate_id,
        revision=revision,
        artifact_id=artifact_id,
        chunk_ids=chunk_ids,
        outbox_id=outbox.id,
    )


def _validate_input(
    *,
    source_id: str,
    course_id: str,
    title: str,
    expected_revision: int,
    idempotency_key: str,
    artifact: ArtifactInput,
    chunks: list[SourceChunkInput],
    ingestion_spec_version: str,
    artifact_root: Path,
) -> None:
    UUID(source_id)
    UUID(course_id)
    if not title.strip() or len(title) > 200:
        raise ValueError("source title is required")
    if expected_revision < 0:
        raise ValueError("expected revision must not be negative")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("idempotency key is required")
    path = PurePosixPath(artifact.relative_path)
    if (
        not artifact.relative_path
        or artifact.relative_path == "."
        or "\\" in artifact.relative_path
        or "://" in artifact.relative_path
        or path.is_absolute()
        or _WINDOWS_DRIVE.match(artifact.relative_path)
        or ".." in path.parts
        or len(path.parts) < 2
        or path.parts[0] != "sources"
    ):
        raise ValueError("relative_path must stay inside the Learning data root")
    _validate_hash(artifact.content_hash)
    if not artifact.media_type or artifact.size_bytes < 0:
        raise ValueError("artifact metadata is invalid")
    _validate_artifact(artifact=artifact, artifact_root=artifact_root)
    if not chunks:
        raise ValueError("at least one source chunk is required")
    if not ingestion_spec_version or len(ingestion_spec_version) > 64:
        raise ValueError("ingestion specification version is required")
    for chunk in chunks:
        if not chunk.content:
            raise ValueError("source chunk content is required")
        _validate_hash(chunk.content_hash)
        actual_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        if chunk.content_hash != actual_hash:
            raise ValueError("chunk content hash does not match content")


def _validate_artifact(*, artifact: ArtifactInput, artifact_root: Path) -> None:
    path = PurePosixPath(artifact.relative_path)
    if (
        not artifact.relative_path
        or artifact.relative_path == "."
        or "\\" in artifact.relative_path
        or "://" in artifact.relative_path
        or path.is_absolute()
        or _WINDOWS_DRIVE.match(artifact.relative_path)
        or ".." in path.parts
        or len(path.parts) < 2
        or path.parts[0] != "sources"
    ):
        raise ValueError("relative_path must stay inside the Learning data root")
    _validate_hash(artifact.content_hash)
    if not artifact.media_type or artifact.size_bytes < 0:
        raise ValueError("artifact metadata is invalid")
    artifact_path = (artifact_root / Path(*path.parts)).resolve(strict=False)
    if not artifact_path.is_relative_to(artifact_root) or not artifact_path.is_file():
        raise ValueError("artifact file is missing from the Learning data root")
    if artifact_path.stat().st_size != artifact.size_bytes:
        raise ValueError("artifact size does not match the original file")
    digest = hashlib.sha256()
    with artifact_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != artifact.content_hash:
        raise ValueError("artifact content hash does not match the original file")


def _input_digest(**values: object) -> str:
    encoded = _canonical_json(values, default=lambda value: value.__dict__).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _canonical_json(values: object, **kwargs: object) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"), **kwargs)


def _locator_hash(locator: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json(locator).encode("utf-8")).hexdigest()


def _validate_hash(value: str) -> None:
    if not _HASH.fullmatch(value):
        raise ValueError("content hash must be lowercase SHA-256")
