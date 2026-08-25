from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Callable, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from spaces.learning.deployment.migrate import migrate
from spaces.learning.services.db.models import LearningOutboxRecord
from spaces.learning.services.db.session import create_learning_engine
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceChunk,
    LearningSourceRevision,
)
from spaces.learning.services.ingestion.outbox_worker import QdrantOutboxWorker
from spaces.learning.services.ingestion.qdrant_index import QdrantHit, QdrantIndex
from spaces.learning.services.ingestion.retrieval import OpenFangEmbeddingGateway


SessionFactory = Callable[[], Session]


class RebuildIndex(Protocol):
    def ensure_schema(self) -> None: ...

    def tombstone_all(self, *, rebuild_run_id: str) -> None: ...

    def retrieve(self, point_ids: list[str]) -> tuple[QdrantHit, ...]: ...

    def count_active(self) -> int: ...


class RebuildVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExpectedPoint:
    point_id: str
    source_id: str
    source_revision: int


@dataclass(frozen=True)
class RebuildManifest:
    run_id: str
    outbox_ids: tuple[str, ...]
    expected_points: tuple[ExpectedPoint, ...]
    migration_batch_id: str | None = None


class QdrantRebuildService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def prepare(
        self,
        index: RebuildIndex,
        *,
        run_id: str,
        migration_batch_id: str | None = None,
    ) -> RebuildManifest:
        run_id = str(UUID(run_id))
        if migration_batch_id is not None and (
            not migration_batch_id
            or len(migration_batch_id) > 128
            or any(
                not (character.isalnum() or character in "._:-")
                for character in migration_batch_id
            )
        ):
            raise ValueError("Qdrant rebuild migration batch ID is invalid")
        index.ensure_schema()
        index.tombstone_all(rebuild_run_id=run_id)
        with self._session_factory() as session, session.begin():
            outbox_ids: list[str] = []
            expected_points: list[ExpectedPoint] = []
            sources = session.scalars(select(LearningSource).order_by(LearningSource.id)).all()
            for source in sources:
                revision = session.scalar(
                    select(LearningSourceRevision)
                    .where(
                        LearningSourceRevision.source_id == source.id,
                        LearningSourceRevision.status.in_(("stored", "indexed")),
                    )
                    .order_by(LearningSourceRevision.revision.desc())
                    .limit(1)
                )
                if revision is None:
                    continue
                chunks = session.scalars(
                    select(LearningSourceChunk)
                    .where(
                        LearningSourceChunk.source_id == source.id,
                        LearningSourceChunk.source_revision == revision.revision,
                    )
                    .order_by(LearningSourceChunk.ordinal)
                ).all()
                if not chunks:
                    raise RebuildVerificationError(
                        "rebuild source revision has no durable chunks"
                    )
                outbox_id = str(uuid4())
                idempotency_key = (
                    f"qdrant-rebuild:{run_id}:{source.id}:{revision.revision}"
                )
                payload: dict[str, object] = {
                    "source_id": source.id,
                    "source_revision": revision.revision,
                    "artifact_id": revision.artifact_id,
                    "chunk_ids": [chunk.id for chunk in chunks],
                    "collection": "learning_knowledge_v1",
                    "ingestion_spec_version": revision.ingestion_spec_version,
                    "rebuild_run_id": run_id,
                }
                if migration_batch_id is not None:
                    payload["migration_batch_id"] = migration_batch_id
                payload["input_digest"] = _digest(payload)
                existing = session.scalar(
                    select(LearningOutboxRecord).where(
                        LearningOutboxRecord.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    if existing.payload != payload:
                        raise RebuildVerificationError(
                            "rebuild idempotency payload conflict"
                        )
                    outbox_id = existing.id
                    existing.status = "pending"
                    existing.attempts = 0
                else:
                    session.add(
                        LearningOutboxRecord(
                            id=outbox_id,
                            topic="learning.qdrant.source_revision.upsert",
                            idempotency_key=idempotency_key,
                            aggregate_type="source",
                            aggregate_id=source.id,
                            payload=payload,
                            status="pending",
                            attempts=0,
                        )
                    )
                outbox_ids.append(outbox_id)
                expected_points.extend(
                    ExpectedPoint(
                        point_id=chunk.id,
                        source_id=source.id,
                        source_revision=revision.revision,
                    )
                    for chunk in chunks
                )
            return RebuildManifest(
                run_id=run_id,
                outbox_ids=tuple(outbox_ids),
                expected_points=tuple(expected_points),
                migration_batch_id=migration_batch_id,
            )

    def statuses(self, manifest: RebuildManifest) -> tuple[str, ...]:
        if not manifest.outbox_ids:
            return ()
        with self._session_factory() as session:
            rows = session.scalars(
                select(LearningOutboxRecord).where(
                    LearningOutboxRecord.id.in_(manifest.outbox_ids)
                )
            ).all()
        by_id = {row.id: row.status for row in rows}
        return tuple(by_id.get(outbox_id, "missing") for outbox_id in manifest.outbox_ids)

    def verify(self, index: RebuildIndex, manifest: RebuildManifest) -> None:
        expected = {point.point_id: point for point in manifest.expected_points}
        if index.count_active() != len(expected):
            raise RebuildVerificationError("Qdrant active point count does not match PostgreSQL")
        actual: dict[str, QdrantHit] = {}
        point_ids = list(expected)
        for offset in range(0, len(point_ids), 100):
            actual.update(
                (hit.point_id, hit)
                for hit in index.retrieve(point_ids[offset : offset + 100])
            )
        if set(actual) != set(expected):
            raise RebuildVerificationError("Qdrant point identities do not match PostgreSQL")
        for point_id, expected_point in expected.items():
            payload = actual[point_id].payload
            if (
                payload.get("source_id") != expected_point.source_id
                or payload.get("source_revision") != expected_point.source_revision
                or payload.get("tombstone") is not False
            ):
                raise RebuildVerificationError("Qdrant rebuild payload verification failed")


def run_rebuild(*, verify: bool, timeout_seconds: float = 300.0) -> RebuildManifest:
    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    qdrant_url = os.environ.get("LEARNING_QDRANT_URL", "").strip()
    embedding_url = os.environ.get("LEARNING_EMBEDDING_URL", "").strip()
    if not database_url or not qdrant_url or not embedding_url:
        raise RuntimeError("rebuild requires database, Qdrant, and embedding service URLs")
    migrate(database_url)
    engine = create_learning_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    index = QdrantIndex(qdrant_url, vector_size=3072)
    service = QdrantRebuildService(factory)
    worker = QdrantOutboxWorker(
        factory,
        index=index,
        embedder=OpenFangEmbeddingGateway(embedding_url),
        retry_delay_seconds=0,
    )
    try:
        manifest = service.prepare(index, run_id=str(uuid4()))
        deadline = time.monotonic() + timeout_seconds
        while True:
            statuses = service.statuses(manifest)
            if all(status == "completed" for status in statuses):
                break
            if any(status in {"dead_letter", "missing"} for status in statuses):
                raise RebuildVerificationError("Qdrant rebuild did not complete")
            if time.monotonic() >= deadline:
                raise TimeoutError("Qdrant rebuild timed out")
            if not worker.run_once():
                time.sleep(0.1)
        if verify:
            service.verify(index, manifest)
        return manifest
    finally:
        index.close()
        engine.dispose()


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    run_rebuild(verify=args.verify)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
