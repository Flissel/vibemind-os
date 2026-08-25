from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.course_factory.artifact_store import CourseFactoryArtifactStore
from spaces.learning.services.course_factory.models import CourseFactoryPublication
from spaces.learning.services.course_factory.publisher import (
    CourseDraftPublisher,
    DraftDeliveryReceipt,
    LearnHouseCourseReadback,
    PublishConfirmation,
)
from spaces.learning.services.course_factory.repository import (
    CourseFactoryRepository,
    GenerationRequest,
    SourceProvenance,
    StageArtifactInput,
)
from spaces.learning.services.course_factory.roles.schemas import SourceVerificationOutput
from spaces.learning.services.course_factory.state_machine import FactoryState
from spaces.learning.services.db.models import Base
from spaces.learning.tests.unit.test_source_verification import _draft, _ids


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture()
def publish_context(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'publish.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    root = tmp_path / "artifacts"
    root.mkdir()
    repository = CourseFactoryRepository(factory)
    job = repository.create_job(
        course_id=str(uuid4()),
        request_hash=_hash("request"),
        provenance=(
            SourceProvenance(
                source_id=str(uuid4()), revision=1, content_hash=_hash("source")
            ),
        ),
        generation_request=GenerationRequest(
            correlation_id=str(uuid4()),
            audience="Students",
            target_outcome="Operate grounded AI",
            learnhouse_revision=1,
        ),
    )
    ids = _ids()
    draft = _draft(ids)
    draft.job_id = job.id
    store = CourseFactoryArtifactStore(factory, artifact_root=root)
    job = repository.advance(
        job.id, expected_revision=job.revision, target=FactoryState.INGESTING
    )
    for stage, target in (
        (FactoryState.INGESTING, FactoryState.STRUCTURING),
        (FactoryState.STRUCTURING, FactoryState.AUTHORING),
        (FactoryState.AUTHORING, FactoryState.ASSESSING),
        (FactoryState.ASSESSING, FactoryState.VERIFYING),
        (FactoryState.VERIFYING, FactoryState.QUALITY_GATE),
        (FactoryState.QUALITY_GATE, FactoryState.REVIEW_READY),
    ):
        payload = (
            {
                "source_verifier": SourceVerificationOutput(
                    schema_version="source-verification-v2",
                    draft=draft,
                    unsupported_claim_ids=[],
                ).model_dump(mode="json")
            }
            if stage is FactoryState.VERIFYING
            else {"stage": stage.value}
        )
        output = store.write_stage_output(job, stage=stage, payload=payload)
        repository.record_stage_artifact(
            job.id,
            expected_revision=job.revision,
            artifact=StageArtifactInput(
                stage=stage,
                input_hash=_hash(stage.value + "-input"),
                output_hash=output.content_hash,
                evidence_refs=(f"learning-artifact://{output.artifact_id}",),
                output_artifact=output,
            ),
        )
        job = repository.advance(
            job.id, expected_revision=job.revision, target=target
        )
    return factory, repository, store, job, draft


class _LearnHouse:
    def __init__(self, *, verify_publish: bool = True) -> None:
        self.verify_publish = verify_publish
        self.calls: list[str] = []

    async def stage_draft(self, **kwargs):
        self.calls.append("stage")
        return DraftDeliveryReceipt(
            course_id=kwargs["course_id"],
            factory_job_id=kwargs["draft"].job_id,
            attempt_number=kwargs["draft"].attempt_number,
            draft_hash=kwargs["draft_hash"],
            learnhouse_revision=kwargs["expected_revision"] + 1,
            evidence_ref="learnhouse://factory-draft/readback",
        )

    async def approve(self, *, course_id, expected_revision, correlation_id):
        self.calls.append("approve")
        return LearnHouseCourseReadback(
            course_id=course_id,
            revision=expected_revision + 1,
            review_status="approved",
            published=False,
        )

    async def publish(self, *, course_id, expected_revision, correlation_id):
        self.calls.append("publish")
        return LearnHouseCourseReadback(
            course_id=course_id,
            revision=expected_revision + 1,
            review_status="approved",
            published=True,
        )

    async def read_course(self, *, course_id, correlation_id):
        self.calls.append("read_course")
        return LearnHouseCourseReadback(
            course_id=course_id,
            revision=4,
            review_status="approved",
            published=self.verify_publish,
        )


def test_publish_confirmation_must_be_explicit() -> None:
    with pytest.raises(ValidationError):
        PublishConfirmation(confirmed=False, approval_ref="approval-1")


@pytest.mark.asyncio
async def test_confirmed_publish_records_terminal_readback_atomically(
    publish_context,
) -> None:
    factory, repository, store, job, draft = publish_context
    client = _LearnHouse()
    publisher = CourseDraftPublisher(
        repository=repository,
        artifact_store=store,
        learnhouse=client,
    )
    delivery = await publisher.stage_review_draft(
        job_id=job.id,
        expected_factory_revision=job.revision,
        expected_learnhouse_revision=1,
        draft=draft,
        correlation_id=str(uuid4()),
    )

    published = await publisher.publish(
        job_id=job.id,
        expected_factory_revision=job.revision,
        confirmation=PublishConfirmation(
            confirmed=True, approval_ref="human-approval-1"
        ),
        correlation_id=str(uuid4()),
    )

    assert delivery.learnhouse_revision == 2
    assert published.state is FactoryState.PUBLISHED
    assert client.calls == ["stage", "approve", "publish", "read_course"]
    with factory() as session:
        row = session.scalar(select(CourseFactoryPublication))
    assert row is not None
    assert row.confirmation_ref == "human-approval-1"
    assert row.learnhouse_revision == 4
    assert row.draft_hash == delivery.draft_hash


@pytest.mark.asyncio
async def test_unverified_publish_readback_leaves_factory_review_ready(
    publish_context,
) -> None:
    factory, repository, store, job, draft = publish_context
    client = _LearnHouse(verify_publish=False)
    publisher = CourseDraftPublisher(
        repository=repository,
        artifact_store=store,
        learnhouse=client,
    )
    await publisher.stage_review_draft(
        job_id=job.id,
        expected_factory_revision=job.revision,
        expected_learnhouse_revision=1,
        draft=draft,
        correlation_id=str(uuid4()),
    )

    with pytest.raises(RuntimeError, match="readback"):
        await publisher.publish(
            job_id=job.id,
            expected_factory_revision=job.revision,
            confirmation=PublishConfirmation(
                confirmed=True, approval_ref="human-approval-2"
            ),
            correlation_id=str(uuid4()),
        )

    assert repository.get(job.id).state is FactoryState.REVIEW_READY
    with factory() as session:
        assert session.scalar(select(CourseFactoryPublication)) is None
