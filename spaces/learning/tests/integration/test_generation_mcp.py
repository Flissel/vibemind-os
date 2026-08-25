from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from spaces.learning.bridge.dispatcher import InMemoryReceiptStore, LearningDispatcher
from spaces.learning.contracts.events import LearningEventType, LearningToolName
from spaces.learning.contracts.mcp_models import (
    ActorV1,
    EventEnvelopeV1,
    ToolRequestV1,
)
from spaces.learning.mcp.tools.generation import CourseFactoryGateway
from spaces.learning.services.course_factory.artifact_store import (
    CourseFactoryArtifactStore,
)
from spaces.learning.services.course_factory.repository import CourseFactoryRepository
from spaces.learning.services.course_factory.state_machine import FactoryState
from spaces.learning.services.db.models import Base, LearningArtifact
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceRevision,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class _Publisher:
    async def publish(self, **kwargs):
        raise AssertionError("publish is outside this queue test")


@pytest.fixture()
def generation_gateway(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'generation-mcp.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    root = tmp_path / "artifacts"
    root.mkdir()
    course_id = str(uuid4())
    source_id = str(uuid4())
    artifact_id = str(uuid4())
    with factory() as session, session.begin():
        session.add(
            LearningArtifact(
                id=artifact_id,
                relative_path="sources/authority.txt",
                content_hash=_hash("authority"),
                media_type="text/plain",
                size_bytes=9,
                aggregate_type="source",
                aggregate_id=source_id,
                revision=1,
            )
        )
        session.add(
            LearningSource(
                id=source_id,
                course_id=course_id,
                title="Authority",
                current_revision=1,
            )
        )
        session.add(
            LearningSourceRevision(
                source_id=source_id,
                revision=1,
                artifact_id=artifact_id,
                content_hash=_hash("authority"),
                ingestion_spec_version="ingestion-v1",
                media_type="text/plain",
                size_bytes=9,
                status="stored",
            )
        )
    repository = CourseFactoryRepository(factory)
    gateway = CourseFactoryGateway(
        session_factory=factory,
        repository=repository,
        publisher=_Publisher(),
        artifact_store=CourseFactoryArtifactStore(factory, artifact_root=root),
    )
    dispatcher = LearningDispatcher(
        gateways={
            LearningToolName.COURSE_GENERATE: gateway,
            LearningToolName.GENERATION_STATUS: gateway,
        },
        receipts=InMemoryReceiptStore(),
    )
    return dispatcher, repository, factory, course_id, source_id


def _request(
    tool: LearningToolName,
    event_type: LearningEventType,
    *,
    course_id: str | None = None,
    expected_revision: int | None = None,
    payload: dict[str, object],
    idempotency_key: str | None = None,
) -> ToolRequestV1:
    return ToolRequestV1(
        tool=tool,
        event=EventEnvelopeV1(
            event_type=event_type,
            invocation_id=uuid4(),
            correlation_id=uuid4(),
            actor=ActorV1(actor_id="local-owner", actor_type="local_user"),
            course_id=course_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload=payload,
        ),
    )


def test_generation_queue_and_status_have_durable_readback(generation_gateway) -> None:
    dispatcher, repository, _, course_id, source_id = generation_gateway
    request = _request(
        LearningToolName.COURSE_GENERATE,
        LearningEventType.COURSE_GENERATE,
        course_id=course_id,
        expected_revision=4,
        idempotency_key="generate-course-1",
        payload={
            "audience": "AI operations students",
            "target_outcome": "Operate authorized AI workflows",
            "source_ids": [source_id],
        },
    )

    created = dispatcher.dispatch(request)
    replayed = dispatcher.dispatch(request)

    assert created.state == "completed"
    assert replayed == created
    assert created.evidence is not None
    assert created.result is not None
    job_id = created.result["job_id"]
    attempt = repository.get_current_attempt(str(job_id))
    assert attempt.generation_request is not None
    assert attempt.generation_request.learnhouse_revision == 4
    status = dispatcher.dispatch(
        _request(
            LearningToolName.GENERATION_STATUS,
            LearningEventType.GENERATION_STATUS,
            course_id=course_id,
            payload={"job_id": job_id},
        )
    )
    assert status.state == "completed"
    assert status.result is not None
    assert status.result["state"] == "queued"


def test_regeneration_is_bounded_to_three_attempts(generation_gateway) -> None:
    dispatcher, repository, _, course_id, source_id = generation_gateway
    created = dispatcher.dispatch(
        _request(
            LearningToolName.COURSE_GENERATE,
            LearningEventType.COURSE_GENERATE,
            course_id=course_id,
            expected_revision=1,
            idempotency_key="generate-bounded-1",
            payload={
                "audience": "Students",
                "target_outcome": "Apply source-grounded decisions",
                "source_ids": [source_id],
            },
        )
    )
    assert created.result is not None
    job_id = str(created.result["job_id"])
    job = repository.advance(
        job_id,
        expected_revision=1,
        target=FactoryState.FAILED,
        reason_code="generation_validation_failed",
    )
    for attempt_number in (2, 3):
        retried = dispatcher.dispatch(
            _request(
                LearningToolName.COURSE_GENERATE,
                LearningEventType.COURSE_GENERATE,
                expected_revision=job.revision,
                idempotency_key=f"retry-{attempt_number}",
                payload={"retry_job_id": job_id},
            )
        )
        assert retried.state == "completed"
        assert retried.result is not None
        assert retried.result["attempt_number"] == attempt_number
        job = repository.advance(
            job_id,
            expected_revision=int(retried.result["revision"]),
            target=FactoryState.FAILED,
            reason_code="generation_validation_failed",
        )

    blocked = dispatcher.dispatch(
        _request(
            LearningToolName.COURSE_GENERATE,
            LearningEventType.COURSE_GENERATE,
            expected_revision=job.revision,
            idempotency_key="retry-4",
            payload={"retry_job_id": job_id},
        )
    )
    assert blocked.state == "rejected"
    assert blocked.error is not None
    assert blocked.error.code == "generation_attempt_limit"


def test_indexed_source_revision_remains_eligible_for_generation(
    generation_gateway,
) -> None:
    dispatcher, _, factory, course_id, source_id = generation_gateway
    with factory() as session, session.begin():
        revision = session.get(LearningSourceRevision, (source_id, 1))
        assert revision is not None
        revision.status = "indexed"

    result = dispatcher.dispatch(
        _request(
            LearningToolName.COURSE_GENERATE,
            LearningEventType.COURSE_GENERATE,
            course_id=course_id,
            expected_revision=1,
            idempotency_key="generate-indexed-source",
            payload={
                "audience": "Students",
                "target_outcome": "Use indexed source evidence",
                "source_ids": [source_id],
            },
        )
    )

    assert result.state == "completed"
    assert result.result is not None
    assert result.result["state"] == "queued"
