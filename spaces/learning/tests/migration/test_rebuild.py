from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.db.models import Base
from spaces.learning.services.ingestion.outbox_worker import QdrantOutboxWorker
from spaces.learning.services.ingestion.qdrant_index import QdrantHit
from spaces.learning.services.ingestion.rebuild import QdrantRebuildService
from spaces.learning.services.migration.importer import MigrationImporter, confirmation_digest
from spaces.learning.tests.migration.test_importer import FakeLearnHouse, import_fixture


class MemoryIndex:
    def __init__(self) -> None:
        self.points = {}
        self.schema_calls = 0

    def ensure_schema(self):
        self.schema_calls += 1

    def tombstone_all(self, *, rebuild_run_id):
        for point in self.points.values():
            point.payload["tombstone"] = True
            point.payload["tombstone_rebuild_run_id"] = rebuild_run_id

    def tombstone(self, point_ids, *, source_id, source_revision):
        for point_id in point_ids:
            if point_id in self.points:
                self.points[point_id].payload["tombstone"] = True

    def upsert(self, points):
        for point in points:
            self.points[point.point_id] = point

    def retrieve(self, point_ids):
        return tuple(
            QdrantHit(point_id=point_id, score=1.0, payload=self.points[point_id].payload)
            for point_id in point_ids
            if point_id in self.points
        )

    def count_active(self):
        return sum(point.payload.get("tombstone") is False for point in self.points.values())


class Embedder:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_imported_source_rebuilds_qdrant_from_postgres_without_legacy_vectors(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'rebuild.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    gateway = FakeLearnHouse()
    importer = MigrationImporter(
        factory,
        artifact_root=tmp_path / "artifacts",
        learnhouse=gateway,
        confirmation_sha256=confirmation_digest("confirm-batch-apply-1"),
    )
    importer.run(
        import_fixture(), batch_id="batch-apply-1", apply=True,
        confirmation_token="confirm-batch-apply-1",
    )
    index = MemoryIndex()
    rebuild = QdrantRebuildService(factory)
    manifest = rebuild.prepare(
        index,
        run_id=str(uuid4()),
        migration_batch_id="batch-apply-1",
    )
    worker = QdrantOutboxWorker(
        factory, index=index, embedder=Embedder(), retry_delay_seconds=0,
    )
    while worker.run_once():
        pass

    rebuild.verify(index, manifest)
    assert manifest.migration_batch_id == "batch-apply-1"
    assert len(manifest.expected_points) == 1
    assert index.count_active() == 1
    point = next(iter(index.points.values()))
    assert point.vector == [1.0, 0.0]
    assert "qdrant_point_id" not in point.payload
    engine.dispose()
