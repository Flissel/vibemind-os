from __future__ import annotations

import os
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.deployment.migrate import migrate
from spaces.learning.services.db.models import LearningOutboxRecord
from spaces.learning.services.ingestion.models import LearningSourceRevision
from spaces.learning.services.ingestion.outbox_worker import QdrantOutboxWorker
from spaces.learning.services.ingestion.qdrant_index import (
    COLLECTION_ALIAS,
    COLLECTION_NAME,
    QdrantIndex,
    QdrantPoint,
)
from spaces.learning.services.ingestion.rebuild import QdrantRebuildService
from spaces.learning.services.ingestion.repository import (
    ArtifactInput,
    SourceChunkInput,
    SourceRepository,
)


@pytest.fixture()
def qdrant_index():
    url = os.environ.get("TEST_LEARNING_QDRANT_URL", "").strip()
    if not url:
        pytest.skip("TEST_LEARNING_QDRANT_URL is required for Qdrant evidence")
    index = QdrantIndex(url, vector_size=3072)
    index.delete_collection_for_test()
    yield index
    index.delete_collection_for_test()


def test_qdrant_projection_is_idempotent_filterable_and_tombstoned(
    qdrant_index: QdrantIndex,
) -> None:
    course_id = str(uuid4())
    source_id = str(uuid4())
    chunk_id = str(uuid4())
    vector = [0.0] * 3072
    vector[0] = 1.0
    point = QdrantPoint(
        point_id=chunk_id,
        vector=vector,
        payload={
            "chunk_id": chunk_id,
            "course_id": course_id,
            "source_id": source_id,
            "source_revision": 1,
            "source_title": "Governance",
            "artifact_id": str(uuid4()),
            "ordinal": 0,
            "content": "Human review is required.",
            "content_hash": "a" * 64,
            "locator": {"page": 3},
            "metadata": {"kind": "page"},
            "ingestion_spec_version": "ingestion-v1",
            "tombstone": False,
        },
    )

    qdrant_index.ensure_schema()
    qdrant_index.tombstone(
        [str(uuid4())], source_id=source_id, source_revision=1
    )
    qdrant_index.upsert([point])
    qdrant_index.upsert([point])

    assert qdrant_index.collection_names() == (COLLECTION_NAME, COLLECTION_ALIAS)
    hits = qdrant_index.search(
        vector,
        course_id=course_id,
        source_id=source_id,
        source_revision=1,
        limit=5,
    )
    assert len(hits) == 1
    assert hits[0].point_id == chunk_id
    assert hits[0].payload["source_revision"] == 1
    assert hits[0].payload["locator"] == {"page": 3}
    assert qdrant_index.count_active() == 1
    assert qdrant_index.retrieve([chunk_id])[0].point_id == chunk_id

    qdrant_index.tombstone(
        [chunk_id], source_id=source_id, source_revision=1
    )

    assert qdrant_index.search(vector, course_id=course_id, limit=5) == ()
    assert qdrant_index.count_active() == 0


class _EmbeddingGateway:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, *([0.0] * 3071)] for _ in texts]


def test_postgres_outbox_projects_to_real_qdrant(
    tmp_path: Path, qdrant_index: QdrantIndex
) -> None:
    database_url = os.environ.get("TEST_LEARNING_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_LEARNING_DATABASE_URL is required for projection evidence")
    if not database_url.startswith("postgresql+psycopg://") or "_test" not in database_url:
        raise RuntimeError("projection integration requires a dedicated _test database")
    migrate(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    source_id = str(uuid4())
    course_id = str(uuid4())
    content = "Human review is required."
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    relative_path = f"sources/{course_id}/{source_id}/material.txt"
    artifact_path = artifact_root / relative_path
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text(content, encoding="utf-8")
    SourceRepository(factory, artifact_root=artifact_root).persist_revision(
        source_id=source_id,
        course_id=course_id,
        title="Governance",
        expected_revision=0,
        idempotency_key=f"qdrant-projection-{source_id}",
        artifact=ArtifactInput(
            relative_path=relative_path,
            content_hash=content_hash,
            media_type="text/plain",
            size_bytes=len(content),
        ),
        chunks=[
            SourceChunkInput(
                content=content,
                content_hash=content_hash,
                locator={"line_start": 1, "line_end": 1},
                metadata={"kind": "paragraph"},
            )
        ],
    )
    worker = QdrantOutboxWorker(
        factory,
        index=qdrant_index,
        embedder=_EmbeddingGateway(),
        retry_delay_seconds=0,
        lease_seconds=0,
    )

    try:
        for _ in range(500):
            with factory() as session:
                target_status = session.scalar(
                    select(LearningOutboxRecord.status).where(
                        LearningOutboxRecord.aggregate_id == source_id
                    )
                )
            if target_status == "completed":
                break
            assert worker.run_once() is True
        else:
            raise AssertionError("target Qdrant outbox did not complete")
        hits = qdrant_index.search(
            [1.0, *([0.0] * 3071)], course_id=course_id, limit=5
        )
        with factory() as session:
            outbox = session.scalar(
                select(LearningOutboxRecord).where(
                    LearningOutboxRecord.aggregate_id == source_id
                )
            )
            revision = session.get(LearningSourceRevision, (source_id, 1))
        assert len(hits) == 1
        assert hits[0].payload["source_id"] == source_id
        assert outbox is not None and outbox.status == "completed"
        assert revision is not None and revision.status == "indexed"
        rebuild = QdrantRebuildService(factory)
        manifest = rebuild.prepare(qdrant_index, run_id=str(uuid4()))
        for _ in range(1000):
            statuses = rebuild.statuses(manifest)
            if all(status == "completed" for status in statuses):
                break
            assert not any(status in {"dead_letter", "missing"} for status in statuses)
            worker.run_once()
        else:
            raise AssertionError("Qdrant rebuild manifest did not complete")
        rebuild.verify(qdrant_index, manifest)
        while worker.run_once():
            pass

        second_source_id = str(uuid4())
        second_relative_path = (
            f"sources/{course_id}/{second_source_id}/material.txt"
        )
        second_artifact_path = artifact_root / second_relative_path
        second_artifact_path.parent.mkdir(parents=True)
        second_artifact_path.write_text(content, encoding="utf-8")
        SourceRepository(factory, artifact_root=artifact_root).persist_revision(
            source_id=second_source_id,
            course_id=course_id,
            title="Concurrent governance",
            expected_revision=0,
            idempotency_key=f"qdrant-concurrent-{second_source_id}",
            artifact=ArtifactInput(
                relative_path=second_relative_path,
                content_hash=content_hash,
                media_type="text/plain",
                size_bytes=len(content),
            ),
            chunks=[
                SourceChunkInput(
                    content=content,
                    content_hash=content_hash,
                    locator={"line_start": 1, "line_end": 1},
                    metadata={"kind": "paragraph"},
                )
            ],
        )
        start = Barrier(2)

        def run_concurrently() -> bool:
            start.wait()
            return QdrantOutboxWorker(
                factory, index=qdrant_index, embedder=_EmbeddingGateway()
            ).run_once()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run_concurrently(), range(2)))
        assert sorted(results) == [False, True]
        with factory() as session:
            second_outbox = session.scalar(
                select(LearningOutboxRecord).where(
                    LearningOutboxRecord.aggregate_id == second_source_id
                )
            )
        assert second_outbox is not None and second_outbox.status == "completed"
        assert second_outbox.attempts == 1
    finally:
        engine.dispose()
