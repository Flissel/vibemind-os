from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.bridge.dispatcher import Receipt
from spaces.learning.contracts.outcomes import (
    AggregateRefV1,
    EvidenceRefV1,
    ToolResultV1,
)
from spaces.learning.services.db.models import Base, LearningOutboxRecord
from spaces.learning.services.db.repository import (
    PersistenceConflict,
    SqlLearningRepository,
    SqlReceiptStore,
)


@pytest.fixture()
def session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'learning.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _result() -> ToolResultV1:
    return ToolResultV1(
        invocation_id=uuid4(),
        correlation_id=uuid4(),
        state="completed",
        aggregate=AggregateRefV1(
            aggregate_type="course", aggregate_id="course-1", revision=1
        ),
        evidence=EvidenceRefV1(
            owner="learnhouse",
            evidence_id="readback-1",
            evidence_type="application_readback",
        ),
        result={"course_id": "course-1"},
    )


def test_receipt_store_survives_new_sessions_and_rejects_digest_conflict(
    session_factory,
) -> None:
    store = SqlReceiptStore(session_factory)
    receipt = Receipt(request_digest="a" * 64, result=_result())
    store.put("create-1", receipt)

    reloaded = SqlReceiptStore(session_factory).get("create-1")
    assert reloaded == receipt
    store.put("create-1", receipt)
    with pytest.raises(PersistenceConflict, match="idempotency"):
        store.put(
            "create-1", Receipt(request_digest="b" * 64, result=_result())
        )


def test_aggregate_revision_compare_and_set_is_atomic(session_factory) -> None:
    repository = SqlLearningRepository(session_factory)

    assert repository.advance_revision("course", "course-1", expected=0) == 1
    assert repository.advance_revision("course", "course-1", expected=1) == 2
    with pytest.raises(PersistenceConflict, match="revision"):
        repository.advance_revision("course", "course-1", expected=1)


def test_outbox_is_idempotent_and_terminal_evidence_is_immutable(
    session_factory,
) -> None:
    repository = SqlLearningRepository(session_factory)
    first = repository.enqueue_outbox(
        topic="learning.source.index",
        idempotency_key="source-1-rev-1",
        aggregate_type="source",
        aggregate_id="source-1",
        payload={"revision": 1},
    )
    second = repository.enqueue_outbox(
        topic="learning.source.index",
        idempotency_key="source-1-rev-1",
        aggregate_type="source",
        aggregate_id="source-1",
        payload={"revision": 1},
    )
    assert first == second
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(LearningOutboxRecord)) == 1

    evidence_id = repository.record_terminal_evidence(
        invocation_id=str(uuid4()),
        correlation_id=str(uuid4()),
        owner="learnhouse",
        aggregate_type="course",
        aggregate_id="course-1",
        aggregate_revision=2,
        terminal_state="completed",
        evidence_type="application_readback",
        payload={"status": "published"},
    )
    assert repository.read_terminal_evidence(evidence_id)["payload"] == {
        "status": "published"
    }
    with pytest.raises(PersistenceConflict, match="immutable"):
        repository.record_terminal_evidence(
            invocation_id=repository.read_terminal_evidence(evidence_id)["invocation_id"],
            correlation_id=str(uuid4()),
            owner="learnhouse",
            aggregate_type="course",
            aggregate_id="course-1",
            aggregate_revision=3,
            terminal_state="completed",
            evidence_type="application_readback",
            payload={"status": "changed"},
        )


def test_artifact_metadata_uses_relative_paths_and_content_hashes(session_factory) -> None:
    repository = SqlLearningRepository(session_factory)
    artifact_id = repository.record_artifact(
        relative_path="sources/course-1/source.pdf",
        content_hash="a" * 64,
        media_type="application/pdf",
        size_bytes=128,
        aggregate_type="source",
        aggregate_id="source-1",
        revision=1,
    )
    artifact = repository.read_artifact(artifact_id)

    assert artifact["relative_path"] == "sources/course-1/source.pdf"
    assert artifact["content_hash"] == "a" * 64
    with pytest.raises(ValueError, match="relative_path"):
        repository.record_artifact(
            relative_path="C:/private/source.pdf",
            content_hash="b" * 64,
            media_type="application/pdf",
            size_bytes=1,
            aggregate_type="source",
            aggregate_id="source-2",
            revision=1,
        )


def test_core_migration_and_metadata_own_the_exact_prefixed_tables() -> None:
    assert set(Base.metadata.tables) == {
        "learning_invocation_receipts",
        "learning_aggregate_revisions",
        "learning_outbox",
        "learning_artifacts",
        "learning_terminal_evidence",
    }
