from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.bridge.dispatcher import ApplicationOutcomeV1, LearningDispatcher
from spaces.learning.contracts.events import LearningEventType, LearningToolName
from spaces.learning.contracts.mcp_models import ActorV1, EventEnvelopeV1, ToolRequestV1
from spaces.learning.contracts.outcomes import AggregateRefV1, EvidenceRefV1, TruthReadbackV1
from spaces.learning.services.course_factory.repository import (
    CourseFactoryRepository,
    GenerationRequest,
    SourceProvenance,
)
from spaces.learning.services.db.models import Base, LearningOutboxRecord
from spaces.learning.services.db.repository import SqlReceiptStore
from spaces.learning.services.ingestion.models import LearningSourceRevision
from spaces.learning.services.ingestion.outbox_worker import QdrantOutboxWorker
from spaces.learning.services.ingestion.qdrant_index import QdrantPoint, QdrantUnavailable
from spaces.learning.services.ingestion.repository import (
    ArtifactInput,
    SourceChunkInput,
    SourceRepository,
)


class _CourseGateway:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request) -> ApplicationOutcomeV1:
        self.calls += 1
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(
                aggregate_type="course", aggregate_id="restart-course", revision=1
            ),
            result={"course_id": "restart-course"},
        )

    def readback(self, request, outcome) -> TruthReadbackV1:
        assert outcome.aggregate is not None
        return TruthReadbackV1(
            invocation_id=request.event.invocation_id,
            correlation_id=request.event.correlation_id,
            owner="learnhouse",
            terminal_state="completed",
            aggregate=outcome.aggregate,
            evidence=EvidenceRefV1(
                owner="learnhouse",
                evidence_id="restart-readback-1",
                evidence_type="application_readback",
            ),
        )


class _Embedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(value)), 1.0] for value in texts]


class _Index:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.points: list[QdrantPoint] = []

    def ensure_schema(self) -> None:
        if self.fail:
            raise QdrantUnavailable("qdrant unavailable")

    def upsert(self, points: list[QdrantPoint]) -> None:
        self.points = points

    def tombstone(self, point_ids, **values) -> None:
        del point_ids, values


def _request() -> ToolRequestV1:
    return ToolRequestV1(
        tool=LearningToolName.COURSE_CREATE,
        event=EventEnvelopeV1(
            event_type=LearningEventType.COURSE_CREATE,
            invocation_id=uuid4(),
            correlation_id=uuid4(),
            actor=ActorV1(actor_id="restart-audit", actor_type="system"),
            idempotency_key="restart-course-create",
            payload={"title": "Restart recovery"},
        ),
    )


def test_mcp_receipt_replays_after_dispatcher_process_restart(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'receipt.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    first_gateway = _CourseGateway()
    first = LearningDispatcher(
        gateways={LearningToolName.COURSE_CREATE: first_gateway},
        receipts=SqlReceiptStore(factory),
    ).dispatch(_request())
    restarted_gateway = _CourseGateway()

    replay = LearningDispatcher(
        gateways={LearningToolName.COURSE_CREATE: restarted_gateway},
        receipts=SqlReceiptStore(factory),
    ).dispatch(_request())

    assert first.state == "completed"
    assert replay == first
    assert first_gateway.calls == 1
    assert restarted_gateway.calls == 0
    engine.dispose()


def test_factory_queued_job_survives_repository_process_restart(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'factory.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = CourseFactoryRepository(factory)
    job = repository.create_job(
        course_id=str(uuid4()),
        request_hash=hashlib.sha256(b"restart-job").hexdigest(),
        provenance=(SourceProvenance(
            source_id=str(uuid4()), revision=1, content_hash="a" * 64
        ),),
        generation_request=GenerationRequest(
            correlation_id=str(uuid4()),
            audience="Students",
            target_outcome="Recover a queued factory job",
            learnhouse_revision=1,
        ),
    )

    claimed = CourseFactoryRepository(factory).claim_next_queued()

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.state.value == "ingesting"
    engine.dispose()


def test_qdrant_retry_recovers_from_postgres_after_worker_restart(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'outbox.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    source_id, course_id = str(uuid4()), str(uuid4())
    content = "Terminal readback proves completion."
    relative = f"sources/{source_id}/authority.txt"
    path = artifact_root / relative
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode()).hexdigest()
    SourceRepository(factory, artifact_root=artifact_root).persist_revision(
        source_id=source_id,
        course_id=course_id,
        title="Authority",
        expected_revision=0,
        idempotency_key="restart-source",
        artifact=ArtifactInput(
            relative_path=relative,
            content_hash=digest,
            media_type="text/plain",
            size_bytes=len(content),
        ),
        chunks=[SourceChunkInput(
            content=content,
            content_hash=digest,
            locator={"line_start": 1, "line_end": 1},
            metadata={},
        )],
    )
    failed_worker = QdrantOutboxWorker(
        factory,
        index=_Index(fail=True),
        embedder=_Embedder(),
        retry_delay_seconds=0,
    )
    assert failed_worker.run_once() is True
    recovered_index = _Index()

    assert QdrantOutboxWorker(
        factory,
        index=recovered_index,
        embedder=_Embedder(),
        retry_delay_seconds=0,
    ).run_once() is True

    with factory() as db:
        outbox = db.scalar(select(LearningOutboxRecord))
        revision = db.get(LearningSourceRevision, (source_id, 1))
    assert outbox is not None and outbox.status == "completed"
    assert outbox.attempts == 2
    assert revision is not None and revision.status == "indexed"
    assert len(recovered_index.points) == 1
    engine.dispose()
