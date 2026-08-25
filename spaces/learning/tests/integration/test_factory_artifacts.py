from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.course_factory.artifact_store import (
    CourseFactoryArtifactStore,
)
from spaces.learning.services.course_factory.models import CourseFactoryStageArtifact
from spaces.learning.services.course_factory.repository import (
    CourseFactoryRepository,
    GenerationRequest,
    SourceProvenance,
    StageArtifactInput,
)
from spaces.learning.services.course_factory.state_machine import FactoryState
from spaces.learning.services.db.models import Base, LearningArtifact


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture()
def factory_store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'factory-artifacts.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    root = tmp_path / "artifacts"
    root.mkdir()
    return factory, root


def _request() -> GenerationRequest:
    return GenerationRequest(
        correlation_id=str(uuid4()),
        audience="Operations professionals",
        target_outcome="Operate grounded AI workflows",
    )


def _job(repository: CourseFactoryRepository):
    return repository.create_job(
        course_id=str(uuid4()),
        request_hash=_hash("request"),
        provenance=(
            SourceProvenance(
                source_id=str(uuid4()),
                revision=1,
                content_hash=_hash("source"),
            ),
        ),
        generation_request=_request(),
    )


def test_generation_request_and_full_stage_output_survive_restart(factory_store) -> None:
    factory, root = factory_store
    repository = CourseFactoryRepository(factory)
    queued = _job(repository)
    request = repository.get_current_attempt(queued.id)
    ingesting = repository.advance(
        queued.id,
        expected_revision=queued.revision,
        target=FactoryState.INGESTING,
    )
    output = {"schema_version": "ingestion-stage-v1", "source_ids": ["source-1"]}
    store = CourseFactoryArtifactStore(factory, artifact_root=root)
    persisted = store.write_stage_output(
        ingesting,
        stage=FactoryState.INGESTING,
        payload=output,
    )
    repository.record_stage_artifact(
        ingesting.id,
        expected_revision=ingesting.revision,
        artifact=StageArtifactInput(
            stage=FactoryState.INGESTING,
            input_hash=_hash("input"),
            output_hash=persisted.content_hash,
            evidence_refs=(f"learning-artifact://{persisted.artifact_id}",),
            output_artifact=persisted,
        ),
    )

    restarted = CourseFactoryArtifactStore(factory, artifact_root=root)

    assert request.generation_request == _request().model_copy(
        update={"correlation_id": request.generation_request.correlation_id}
    )
    assert restarted.read_stage_output(
        ingesting.id,
        attempt_number=1,
        stage=FactoryState.INGESTING,
    ) == output
    with factory() as session:
        stage = session.scalar(select(CourseFactoryStageArtifact))
        artifact = session.scalar(select(LearningArtifact))
    assert stage is not None and artifact is not None
    assert stage.output_artifact_id == artifact.id == persisted.artifact_id
    assert artifact.content_hash == persisted.content_hash


def test_stage_output_is_idempotent_for_same_bytes_and_rejects_overwrite(
    factory_store,
) -> None:
    factory, root = factory_store
    repository = CourseFactoryRepository(factory)
    job = repository.advance(
        _job(repository).id,
        expected_revision=1,
        target=FactoryState.INGESTING,
    )
    store = CourseFactoryArtifactStore(factory, artifact_root=root)

    first = store.write_stage_output(
        job, stage=FactoryState.INGESTING, payload={"value": 1}
    )
    replay = store.write_stage_output(
        job, stage=FactoryState.INGESTING, payload={"value": 1}
    )

    assert replay == first
    with pytest.raises(RuntimeError, match="immutable"):
        store.write_stage_output(
            job, stage=FactoryState.INGESTING, payload={"value": 2}
        )


def test_retry_outputs_remain_readable_by_attempt(factory_store) -> None:
    factory, root = factory_store
    repository = CourseFactoryRepository(factory)
    first = repository.advance(
        _job(repository).id,
        expected_revision=1,
        target=FactoryState.INGESTING,
    )
    store = CourseFactoryArtifactStore(factory, artifact_root=root)
    first_output = store.write_stage_output(
        first, stage=FactoryState.INGESTING, payload={"attempt": 1}
    )
    repository.record_stage_artifact(
        first.id,
        expected_revision=first.revision,
        artifact=StageArtifactInput(
            stage=FactoryState.INGESTING,
            input_hash=_hash("first-input"),
            output_hash=first_output.content_hash,
            evidence_refs=(f"learning-artifact://{first_output.artifact_id}",),
            output_artifact=first_output,
        ),
    )
    failed = repository.advance(
        first.id,
        expected_revision=first.revision,
        target=FactoryState.FAILED,
        reason_code="stage_failed",
    )
    retried = repository.retry(
        first.id,
        expected_revision=failed.revision,
        request_hash=_hash("retry"),
        provenance=repository.get_current_attempt(first.id).provenance,
        generation_request=_request(),
    )
    second = repository.advance(
        retried.id,
        expected_revision=retried.revision,
        target=FactoryState.INGESTING,
    )
    second_output = store.write_stage_output(
        second, stage=FactoryState.INGESTING, payload={"attempt": 2}
    )
    repository.record_stage_artifact(
        second.id,
        expected_revision=second.revision,
        artifact=StageArtifactInput(
            stage=FactoryState.INGESTING,
            input_hash=_hash("second-input"),
            output_hash=second_output.content_hash,
            evidence_refs=(f"learning-artifact://{second_output.artifact_id}",),
            output_artifact=second_output,
        ),
    )

    assert store.read_stage_output(
        first.id, attempt_number=1, stage=FactoryState.INGESTING
    ) == {"attempt": 1}
    assert store.read_stage_output(
        first.id, attempt_number=2, stage=FactoryState.INGESTING
    ) == {"attempt": 2}
