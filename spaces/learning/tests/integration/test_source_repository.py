from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import BigInteger, create_engine, delete, func, inspect, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.db.models import Base, LearningArtifact, LearningOutboxRecord
from spaces.learning.services.db.repository import PersistenceConflict
from spaces.learning.deployment.migrate import _config, migrate
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceChunk,
    LearningSourceRevision,
)
from spaces.learning.services.ingestion.repository import (
    ArtifactInput,
    SourceChunkInput,
    SourceRepository,
    stable_chunk_id,
)


@pytest.fixture()
def session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sources.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    factory.artifact_root = tmp_path / "artifacts"
    factory.artifact_root.mkdir()
    return factory


@pytest.fixture()
def postgres_session_factory(tmp_path: Path):
    database_url = os.environ.get("TEST_LEARNING_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_LEARNING_DATABASE_URL is required for PostgreSQL evidence")
    if not database_url.startswith("postgresql+psycopg://") or "_test" not in database_url:
        raise RuntimeError("PostgreSQL integration requires a dedicated _test database")
    migrate(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        factory.database_url = database_url
        factory.artifact_root = tmp_path / "artifacts"
        factory.artifact_root.mkdir()
        yield factory
    finally:
        engine.dispose()


def _repository(session_factory) -> SourceRepository:
    return SourceRepository(
        session_factory,
        artifact_root=session_factory.artifact_root,
    )


def _artifact(session_factory, revision: int) -> ArtifactInput:
    relative_path = f"sources/course-1/policy-r{revision}.pdf"
    content = f"source artifact revision {revision}".encode()
    path = session_factory.artifact_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return ArtifactInput(
        relative_path=relative_path,
        content_hash=hashlib.sha256(content).hexdigest(),
        media_type="application/pdf",
        size_bytes=len(content),
    )


def _chunks(revision: int) -> list[SourceChunkInput]:
    approved = f"Revision {revision}: approved AI policy."
    prohibited = f"Revision {revision}: prohibited AI policy."
    return [
        SourceChunkInput(
            content=approved,
            content_hash=hashlib.sha256(approved.encode()).hexdigest(),
            locator={"page": 3, "heading": "Approved use"},
            metadata={"language": "en"},
        ),
        SourceChunkInput(
            content=prohibited,
            content_hash=hashlib.sha256(prohibited.encode()).hexdigest(),
            locator={"page": 4, "heading": "Prohibited use"},
            metadata={"language": "en"},
        ),
    ]


def test_source_revisions_are_immutable_and_keep_artifact_and_locator_metadata(
    session_factory,
) -> None:
    repository = _repository(session_factory)
    source_id = str(uuid4())
    course_id = str(uuid4())

    first = repository.persist_revision(
        source_id=source_id,
        course_id=course_id,
        title="AI policy",
        expected_revision=0,
        idempotency_key="source-policy-r1",
        artifact=_artifact(session_factory, 1),
        chunks=_chunks(1),
    )
    second = repository.persist_revision(
        source_id=source_id,
        course_id=course_id,
        title="AI policy",
        expected_revision=1,
        idempotency_key="source-policy-r2",
        artifact=_artifact(session_factory, 2),
        chunks=_chunks(2),
    )

    assert (first.revision, second.revision) == (1, 2)
    assert first.chunk_ids[0] == stable_chunk_id(
        source_id=source_id,
        source_revision=1,
        ordinal=0,
        content_hash=_chunks(1)[0].content_hash,
        locator=_chunks(1)[0].locator,
        ingestion_spec_version="ingestion-v1",
    )
    with session_factory() as session:
        source = session.get(LearningSource, source_id)
        revisions = session.scalars(
            select(LearningSourceRevision)
            .where(LearningSourceRevision.source_id == source_id)
            .order_by(LearningSourceRevision.revision)
        ).all()
        first_chunk = session.get(LearningSourceChunk, first.chunk_ids[0])

    assert [row.content_hash for row in revisions] == [
        _artifact(session_factory, 1).content_hash,
        _artifact(session_factory, 2).content_hash,
    ]
    assert source is not None
    assert source.current_revision == 2
    assert [row.ingestion_spec_version for row in revisions] == [
        "ingestion-v1",
        "ingestion-v1",
    ]
    assert [row.status for row in revisions] == ["stored", "stored"]
    assert first_chunk is not None
    assert first_chunk.locator == {"page": 3, "heading": "Approved use"}
    assert first_chunk.metadata_json == {"language": "en"}

    with pytest.raises(PersistenceConflict, match="revision"):
        repository.persist_revision(
            source_id=source_id,
            course_id=course_id,
            title="AI policy",
            expected_revision=1,
            idempotency_key="source-policy-stale",
            artifact=_artifact(session_factory, 3),
            chunks=_chunks(3),
        )


def test_source_revision_replay_has_one_qdrant_outbox_row(session_factory) -> None:
    repository = _repository(session_factory)
    source_id = str(uuid4())
    values = {
        "source_id": source_id,
        "course_id": str(uuid4()),
        "title": "AI policy",
        "expected_revision": 0,
        "idempotency_key": "source-policy-replay",
        "artifact": _artifact(session_factory, 1),
        "chunks": _chunks(1),
    }

    first = repository.persist_revision(**values)
    replay = _repository(session_factory).persist_revision(**values)

    assert replay == first
    with session_factory() as session:
        rows = session.scalars(
            select(LearningOutboxRecord).where(
                LearningOutboxRecord.idempotency_key == "source-policy-replay"
            )
        ).all()
        chunk_count = session.scalar(
            select(func.count())
            .select_from(LearningSourceChunk)
            .where(LearningSourceChunk.source_id == source_id)
        )

    assert len(rows) == 1
    assert rows[0].topic == "learning.qdrant.source_revision.upsert"
    assert rows[0].payload["chunk_ids"] == list(first.chunk_ids)
    assert chunk_count == 2

    changed = {**values, "title": "Changed under the same key"}
    with pytest.raises(PersistenceConflict, match="idempotency"):
        repository.persist_revision(**changed)


def test_source_repository_canonicalizes_equivalent_uuid_spellings(
    session_factory,
) -> None:
    canonical = "00000000-0000-0000-0000-000000000001"
    values = {
        "source_id": "{00000000-0000-0000-0000-000000000001}",
        "course_id": "00000000000000000000000000000002",
        "title": "Canonical source",
        "expected_revision": 0,
        "idempotency_key": "canonical-source",
        "artifact": _artifact(session_factory, 1),
        "chunks": _chunks(1),
    }

    first = _repository(session_factory).persist_revision(**values)
    replay = _repository(session_factory).persist_revision(
        **{
            **values,
            "source_id": canonical,
            "course_id": "00000000-0000-0000-0000-000000000002",
        }
    )

    assert first.source_id == canonical
    assert replay == first


@pytest.mark.parametrize(
    "relative_path",
    [
        "C:/Users/User/secret.pdf",
        "//server/share/secret.pdf",
        "sources\\..\\secret.pdf",
        "file://sources/secret.pdf",
        "other/secret.pdf",
        "",
        ".",
    ],
)
def test_source_artifacts_reject_paths_outside_the_learning_data_root(
    session_factory, relative_path: str
) -> None:
    repository = _repository(session_factory)
    artifact = ArtifactInput(
        relative_path=relative_path,
        content_hash="a" * 64,
        media_type="application/pdf",
        size_bytes=10,
    )

    with pytest.raises(ValueError, match="relative_path"):
        repository.persist_revision(
            source_id=str(uuid4()),
            course_id=str(uuid4()),
            title="Unsafe source",
            expected_revision=0,
            idempotency_key="unsafe-source",
            artifact=artifact,
            chunks=_chunks(1),
        )


def test_source_model_metadata_matches_the_big_file_migration_contract() -> None:
    size_type = LearningSourceRevision.__table__.c.size_bytes.type

    assert isinstance(size_type, BigInteger)


def test_chunk_identity_changes_with_locator_or_ingestion_specification() -> None:
    source_id = "{00000000-0000-0000-0000-000000000001}"
    values = {
        "source_id": source_id,
        "source_revision": 1,
        "ordinal": 0,
        "content_hash": "a" * 64,
        "locator": {"page": 1, "start": 0, "end": 20},
        "ingestion_spec_version": "ingestion-v1",
    }

    baseline = stable_chunk_id(**values)

    assert baseline == "f26fd0a1-826b-5894-bae1-627dbdb1fb06"
    assert stable_chunk_id(**values) == baseline
    assert stable_chunk_id(
        **{**values, "source_id": "00000000000000000000000000000001"}
    ) == baseline
    assert stable_chunk_id(**{**values, "locator": {"page": 2}}) != baseline
    assert stable_chunk_id(**{**values, "ingestion_spec_version": "ingestion-v2"}) != baseline


def test_source_repository_rejects_a_chunk_with_a_false_content_hash(
    session_factory,
) -> None:
    repository = _repository(session_factory)
    chunk = _chunks(1)[0]

    with pytest.raises(ValueError, match="chunk content hash"):
        repository.persist_revision(
            source_id=str(uuid4()),
            course_id=str(uuid4()),
            title="Invalid chunk",
            expected_revision=0,
            idempotency_key="invalid-chunk-hash",
            artifact=_artifact(session_factory, 1),
            chunks=[
                SourceChunkInput(
                    content=chunk.content,
                    content_hash="f" * 64,
                    locator=chunk.locator,
                    metadata=chunk.metadata,
                )
            ],
        )


def test_source_repository_verifies_the_original_artifact_bytes(session_factory) -> None:
    repository = _repository(session_factory)
    missing = ArtifactInput(
        relative_path="sources/course-1/missing.pdf",
        content_hash="a" * 64,
        media_type="application/pdf",
        size_bytes=10,
    )

    with pytest.raises(ValueError, match="artifact file"):
        repository.persist_revision(
            source_id=str(uuid4()),
            course_id=str(uuid4()),
            title="Missing artifact",
            expected_revision=0,
            idempotency_key="missing-artifact",
            artifact=missing,
            chunks=_chunks(1),
        )

    existing = _artifact(session_factory, 1)
    mismatched = ArtifactInput(
        relative_path=existing.relative_path,
        content_hash="f" * 64,
        media_type=existing.media_type,
        size_bytes=existing.size_bytes,
    )
    with pytest.raises(ValueError, match="artifact content hash"):
        repository.persist_revision(
            source_id=str(uuid4()),
            course_id=str(uuid4()),
            title="Substituted artifact",
            expected_revision=0,
            idempotency_key="substituted-artifact",
            artifact=mismatched,
            chunks=_chunks(1),
        )


def test_postgres_migration_and_source_content_are_append_only(
    postgres_session_factory,
) -> None:
    repository = _repository(postgres_session_factory)
    source_id = str(uuid4())
    record = repository.persist_revision(
        source_id=source_id,
        course_id=str(uuid4()),
        title="PostgreSQL source",
        expected_revision=0,
        idempotency_key=f"postgres-source-{source_id}",
        artifact=_artifact(postgres_session_factory, 1),
        chunks=_chunks(1),
    )

    with postgres_session_factory() as session:
        inspector = inspect(session.bind)
        unique_sets = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(
                "learning_source_revisions"
            )
        }
        version = session.connection().exec_driver_sql(
            "SELECT version_num FROM learning_alembic_version"
        ).scalar_one()

    assert version == "0003_sources_outbox"
    assert ("artifact_id",) in unique_sets
    assert ("source_id", "content_hash", "ingestion_spec_version") in unique_sets

    with pytest.raises(DBAPIError, match="append-only"):
        with postgres_session_factory.begin() as session:
            session.execute(
                update(LearningSourceRevision)
                .where(
                    LearningSourceRevision.source_id == source_id,
                    LearningSourceRevision.revision == record.revision,
                )
                .values(content_hash="f" * 64)
            )

    with postgres_session_factory.begin() as session:
        session.execute(
            update(LearningSourceRevision)
            .where(
                LearningSourceRevision.source_id == source_id,
                LearningSourceRevision.revision == record.revision,
            )
            .values(status="indexed")
        )

    with pytest.raises(DBAPIError, match="append-only"):
        with postgres_session_factory.begin() as session:
            session.execute(
                update(LearningSource)
                .where(LearningSource.id == source_id)
                .values(title="Rebound source")
            )

    with pytest.raises(DBAPIError, match="append-only"):
        with postgres_session_factory.begin() as session:
            session.execute(
                update(LearningArtifact)
                .where(LearningArtifact.id == record.artifact_id)
                .values(content_hash="e" * 64)
            )

    generic_artifact_id = str(uuid4())
    with postgres_session_factory.begin() as session:
        session.add(
            LearningArtifact(
                id=generic_artifact_id,
                relative_path="exports/review.json",
                content_hash="d" * 64,
                media_type="application/json",
                size_bytes=2,
                aggregate_type="review",
                aggregate_id=str(uuid4()),
                revision=1,
            )
        )
    with postgres_session_factory.begin() as session:
        session.execute(
            delete(LearningArtifact).where(LearningArtifact.id == generic_artifact_id)
        )
    with postgres_session_factory() as session:
        assert session.get(LearningArtifact, generic_artifact_id) is None

    with pytest.raises(RuntimeError, match="non-empty source history"):
        command.downgrade(
            _config(postgres_session_factory.database_url),
            "0002_learning_receipt_claims",
        )


def test_postgres_concurrent_exact_replay_returns_one_durable_revision(
    postgres_session_factory,
) -> None:
    source_id = str(uuid4())
    values = {
        "source_id": source_id,
        "course_id": str(uuid4()),
        "title": "Concurrent source",
        "expected_revision": 0,
        "idempotency_key": f"postgres-concurrent-{source_id}",
        "artifact": _artifact(postgres_session_factory, 1),
        "chunks": _chunks(1),
    }
    start = Barrier(2)

    def persist_once():
        start.wait()
        return _repository(postgres_session_factory).persist_revision(**values)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: persist_once(), range(2)))

    assert results[0] == results[1]
    with postgres_session_factory() as session:
        revision_count = session.scalar(
            select(func.count())
            .select_from(LearningSourceRevision)
            .where(LearningSourceRevision.source_id == source_id)
        )
        outbox_count = session.scalar(
            select(func.count())
            .select_from(LearningOutboxRecord)
            .where(LearningOutboxRecord.idempotency_key == values["idempotency_key"])
        )

    assert revision_count == 1
    assert outbox_count == 1


def test_postgres_concurrent_retry_of_a_later_revision_replays_exactly(
    postgres_session_factory,
) -> None:
    repository = _repository(postgres_session_factory)
    source_id = str(uuid4())
    course_id = str(uuid4())
    repository.persist_revision(
        source_id=source_id,
        course_id=course_id,
        title="Concurrent later revision",
        expected_revision=0,
        idempotency_key=f"postgres-r1-{source_id}",
        artifact=_artifact(postgres_session_factory, 1),
        chunks=_chunks(1),
    )
    values = {
        "source_id": source_id,
        "course_id": course_id,
        "title": "Concurrent later revision",
        "expected_revision": 1,
        "idempotency_key": f"postgres-r2-{source_id}",
        "artifact": _artifact(postgres_session_factory, 2),
        "chunks": _chunks(2),
    }
    start = Barrier(2)

    def persist_once():
        start.wait()
        return _repository(postgres_session_factory).persist_revision(**values)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: persist_once(), range(2)))

    assert results[0] == results[1]
    assert results[0].revision == 2
