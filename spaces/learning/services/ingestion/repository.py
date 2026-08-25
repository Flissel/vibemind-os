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

from spaces.learning.services.db.models import LearningArtifact, LearningOutboxRecord
from spaces.learning.services.db.repository import PersistenceConflict
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceChunk,
    LearningSourceRevision,
)


SessionFactory = Callable[[], Session]
_CHUNK_NAMESPACE = UUID("79ef81aa-1cb7-5f90-a96c-0d89eb3f716d")
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
                ):
                    return _record_from_outbox(session, replay)
            if isinstance(error, PersistenceConflict):
                raise
            raise PersistenceConflict("source revision conflict") from error

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
