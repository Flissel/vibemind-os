from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.db.models import (
    Base,
    LearningOutboxRecord,
    LearningTerminalEvidence,
)
from spaces.learning.services.ingestion.models import LearningSourceRevision
from spaces.learning.services.ingestion.outbox_worker import QdrantOutboxWorker
from spaces.learning.services.ingestion.qdrant_index import (
    QdrantHit,
    QdrantPoint,
    QdrantUnavailable,
)
from spaces.learning.services.ingestion.rebuild import QdrantRebuildService
from spaces.learning.services.ingestion.repository import (
    ArtifactInput,
    SourceChunkInput,
    SourceRepository,
)


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1), 0.0] for index, _ in enumerate(texts)]


class RecordingIndex:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.points: list[QdrantPoint] = []

    def ensure_schema(self) -> None:
        if self.fail:
            raise QdrantUnavailable("qdrant unavailable")

    def upsert(self, points: list[QdrantPoint]) -> None:
        self.points = points

    def tombstone(
        self, point_ids: list[str], *, source_id: str, source_revision: int
    ) -> None:
        del point_ids, source_id, source_revision


class CrashAfterUpsertIndex(RecordingIndex):
    def __init__(self) -> None:
        super().__init__()
        self.point_ids_by_call: list[tuple[str, ...]] = []

    def upsert(self, points: list[QdrantPoint]) -> None:
        self.point_ids_by_call.append(tuple(point.point_id for point in points))
        self.points = points
        if len(self.point_ids_by_call) == 1:
            raise RuntimeError("worker crashed after Qdrant committed")


class RebuildIndex(RecordingIndex):
    def __init__(self) -> None:
        super().__init__()
        self.active: dict[str, QdrantPoint] = {}

    def upsert(self, points: list[QdrantPoint]) -> None:
        for point in points:
            self.active[point.point_id] = point

    def tombstone_all(self, *, rebuild_run_id: str) -> None:
        del rebuild_run_id
        self.active = {}

    def retrieve(self, point_ids: list[str]) -> tuple[QdrantHit, ...]:
        return tuple(
            QdrantHit(point_id=point_id, score=1.0, payload=self.active[point_id].payload)
            for point_id in point_ids
            if point_id in self.active
        )

    def count_active(self) -> int:
        return len(self.active)


def _worker_store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    source_id = str(uuid4())
    course_id = str(uuid4())
    content = "Human review is required."
    relative_path = f"sources/{course_id}/{source_id}/material.txt"
    artifact_path = artifact_root / relative_path
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text(content, encoding="utf-8")
    artifact_hash = hashlib.sha256(content.encode()).hexdigest()
    SourceRepository(factory, artifact_root=artifact_root).persist_revision(
        source_id=source_id,
        course_id=course_id,
        title="Governance",
        expected_revision=0,
        idempotency_key=f"worker-{source_id}",
        artifact=ArtifactInput(
            relative_path=relative_path,
            content_hash=artifact_hash,
            media_type="text/plain",
            size_bytes=len(content),
        ),
        chunks=[
            SourceChunkInput(
                content=content,
                content_hash=artifact_hash,
                locator={"line_start": 1, "line_end": 1},
                metadata={"kind": "paragraph"},
            )
        ],
    )
    return factory, source_id, course_id


def test_worker_projects_full_payload_and_completes_outbox(tmp_path: Path) -> None:
    factory, source_id, course_id = _worker_store(tmp_path)
    index = RecordingIndex()
    worker = QdrantOutboxWorker(factory, index=index, embedder=FakeEmbedder())

    assert worker.run_once() is True

    assert len(index.points) == 1
    payload = index.points[0].payload
    assert payload["source_id"] == source_id
    assert payload["course_id"] == course_id
    assert payload["source_revision"] == 1
    assert payload["locator"] == {"line_start": 1, "line_end": 1}
    with factory() as session:
        outbox = session.scalar(select(LearningOutboxRecord))
        revision = session.get(LearningSourceRevision, (source_id, 1))
    assert outbox is not None and outbox.status == "completed"
    assert revision is not None and revision.status == "indexed"


def test_qdrant_outage_retries_then_dead_letters_without_losing_source(
    tmp_path: Path,
) -> None:
    factory, source_id, _ = _worker_store(tmp_path)
    worker = QdrantOutboxWorker(
        factory,
        index=RecordingIndex(fail=True),
        embedder=FakeEmbedder(),
        maximum_attempts=2,
        retry_delay_seconds=0,
    )

    assert worker.run_once() is True
    assert worker.run_once() is True
    assert worker.run_once() is False

    with factory() as session:
        outbox = session.scalar(select(LearningOutboxRecord))
        revision = session.get(LearningSourceRevision, (source_id, 1))
        evidence = session.scalar(select(LearningTerminalEvidence))
    assert outbox is not None and outbox.status == "dead_letter"
    assert outbox.attempts == 2
    assert revision is not None and revision.status == "stored"
    assert evidence is not None
    assert evidence.payload["error_code"] == "qdrant_unavailable"


def test_worker_replays_same_point_ids_after_post_upsert_failure(tmp_path: Path) -> None:
    factory, source_id, _ = _worker_store(tmp_path)
    index = CrashAfterUpsertIndex()
    worker = QdrantOutboxWorker(
        factory,
        index=index,
        embedder=FakeEmbedder(),
        retry_delay_seconds=0,
    )

    assert worker.run_once() is True
    assert worker.run_once() is True

    assert len(index.point_ids_by_call) == 2
    assert index.point_ids_by_call[0] == index.point_ids_by_call[1]
    with factory() as session:
        outbox = session.scalar(select(LearningOutboxRecord))
        revision = session.get(LearningSourceRevision, (source_id, 1))
    assert outbox is not None and outbox.status == "completed"
    assert revision is not None and revision.status == "indexed"


def test_rebuild_requeues_current_postgres_revision_and_verifies_exact_points(
    tmp_path: Path,
) -> None:
    factory, _, _ = _worker_store(tmp_path)
    index = RebuildIndex()
    rebuild = QdrantRebuildService(factory)
    manifest = rebuild.prepare(index, run_id=str(uuid4()))
    worker = QdrantOutboxWorker(
        factory,
        index=index,
        embedder=FakeEmbedder(),
        retry_delay_seconds=0,
    )

    while worker.run_once():
        pass

    assert rebuild.statuses(manifest) == ("completed",)
    rebuild.verify(index, manifest)
    assert index.count_active() == 1


def test_worker_recovers_an_expired_last_attempt_lease(tmp_path: Path) -> None:
    factory, source_id, _ = _worker_store(tmp_path)
    with factory.begin() as session:
        outbox = session.scalar(select(LearningOutboxRecord))
        assert outbox is not None
        outbox.status = "processing"
        outbox.attempts = 2
        outbox.updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    worker = QdrantOutboxWorker(
        factory,
        index=RecordingIndex(),
        embedder=FakeEmbedder(),
        maximum_attempts=2,
        lease_seconds=1,
    )

    assert worker.run_once() is True

    with factory() as session:
        outbox = session.scalar(select(LearningOutboxRecord))
        revision = session.get(LearningSourceRevision, (source_id, 1))
    assert outbox is not None and outbox.status == "completed"
    assert outbox.attempts == 2
    assert revision is not None and revision.status == "indexed"


def test_worker_serializes_open_revisions_for_the_same_source(tmp_path: Path) -> None:
    factory, source_id, course_id = _worker_store(tmp_path)
    content = "A newer control requires approval."
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    relative_path = f"sources/{course_id}/{source_id}/material-v2.txt"
    artifact_path = tmp_path / "artifacts" / relative_path
    artifact_path.write_text(content, encoding="utf-8")
    SourceRepository(factory, artifact_root=tmp_path / "artifacts").persist_revision(
        source_id=source_id,
        course_id=course_id,
        title="Governance",
        expected_revision=1,
        idempotency_key=f"worker-v2-{source_id}",
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
    first_worker = QdrantOutboxWorker(
        factory, index=RecordingIndex(), embedder=FakeEmbedder()
    )
    second_worker = QdrantOutboxWorker(
        factory, index=RecordingIndex(), embedder=FakeEmbedder()
    )

    first_task = first_worker._claim()
    second_task = second_worker._claim()

    assert first_task is not None and first_task.source_revision == 1
    assert second_task is None
