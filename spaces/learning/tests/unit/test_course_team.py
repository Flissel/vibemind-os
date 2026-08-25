from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.course_factory.model_gateway import (
    GatewayResult,
    GatewayUnavailable,
)
from spaces.learning.services.course_factory.models import CourseFactoryStageArtifact
from spaces.learning.services.course_factory.repository import (
    CourseFactoryRepository,
    SourceProvenance,
    StageArtifactInput,
)
from spaces.learning.services.course_factory.roles.schemas import (
    ArchitectOutput,
    AssessmentOutput,
    ConceptMapOutput,
    LessonOutput,
    QualityReviewOutput,
    SourceVerificationOutput,
)
from spaces.learning.services.course_factory.state_machine import FactoryState
from spaces.learning.services.course_factory.team import (
    CourseAgentTeam,
    CourseTeamInput,
)
from spaces.learning.services.db.models import Base


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture()
def session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'team.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _provenance() -> tuple[SourceProvenance, ...]:
    return (
        SourceProvenance(
            source_id=str(uuid4()),
            revision=1,
            content_hash=_hash("source"),
        ),
    )


def _stage(stage: FactoryState) -> StageArtifactInput:
    return StageArtifactInput(
        stage=stage,
        input_hash=_hash(stage.value + "-input"),
        output_hash=_hash(stage.value + "-output"),
        evidence_refs=("test://evidence",),
    )


def _structuring_job(repository: CourseFactoryRepository):
    job = repository.create_job(
        course_id=str(uuid4()),
        request_hash=_hash("request"),
        provenance=_provenance(),
    )
    job = repository.advance(
        job.id, expected_revision=1, target=FactoryState.INGESTING
    )
    repository.record_stage_artifact(
        job.id,
        expected_revision=2,
        artifact=_stage(FactoryState.INGESTING),
    )
    return repository.advance(
        job.id, expected_revision=2, target=FactoryState.STRUCTURING
    )


def _role_output(role: str):
    values = {
        "architect": ArchitectOutput(
            schema_version="architect-v1",
            audience="Professionals",
            prerequisites=["Domain basics"],
            outcomes=["Apply grounded AI"],
            chapters=["Foundations", "Practice"],
        ),
        "concept_mapper": ConceptMapOutput(
            schema_version="concept-map-v1",
            concepts=["authority", "grounding"],
            dependencies=[{"before": "authority", "after": "grounding"}],
            chapter_coverage={"Foundations": ["authority"]},
        ),
        "lesson_author": LessonOutput(
            schema_version="lesson-v1",
            lessons=[{"chapter": "Foundations", "title": "Authority", "body": "Draft"}],
        ),
        "assessment_designer": AssessmentOutput(
            schema_version="assessment-v1",
            activities=[{"concept": "authority", "type": "case", "prompt": "Decide"}],
        ),
        "source_verifier": SourceVerificationOutput(
            schema_version="source-verification-v1",
            supported_claims=["claim-1"],
            unsupported_claims=[],
            citation_refs=["source://one/1#page=2"],
        ),
        "quality_reviewer": QualityReviewOutput(
            schema_version="quality-review-v1",
            decision="pass",
            issues=[],
            score=0.9,
        ),
    }
    return values[role]


class _Gateway:
    def __init__(self, fail_role: str | None = None) -> None:
        self.fail_role = fail_role
        self.calls = []

    async def generate(self, invocation, output_model):
        self.calls.append(invocation)
        if invocation.role == self.fail_role:
            raise GatewayUnavailable("OpenFang model gateway is unavailable")
        output = _role_output(invocation.role)
        assert isinstance(output, output_model)
        return GatewayResult(
            output=output,
            evidence_ref=f"openfang://completion/{invocation.role}",
        )


@pytest.mark.asyncio
async def test_autogen_team_runs_fixed_roles_and_persists_each_stage(session_factory) -> None:
    repository = CourseFactoryRepository(session_factory)
    job = _structuring_job(repository)
    gateway = _Gateway()
    correlation_id = str(uuid4())
    team = CourseAgentTeam(repository, gateway)

    result = await team.run(
        CourseTeamInput(
            job_id=job.id,
            expected_revision=job.revision,
            correlation_id=correlation_id,
            course_id=job.course_id,
            audience="Professionals",
            target_outcome="Apply grounded AI safely",
            source_refs=("source://one/1",),
        )
    )

    assert result.state is FactoryState.QUALITY_GATE
    assert [call.role for call in gateway.calls] == [
        "architect",
        "concept_mapper",
        "lesson_author",
        "assessment_designer",
        "source_verifier",
        "quality_reviewer",
    ]
    assert {call.correlation_id for call in gateway.calls} == {correlation_id}
    with session_factory() as session:
        artifacts = session.scalars(
            select(CourseFactoryStageArtifact).where(
                CourseFactoryStageArtifact.job_id == job.id
            )
        ).all()
    assert {artifact.stage for artifact in artifacts} == {
        "ingesting",
        "structuring",
        "authoring",
        "assessing",
        "verifying",
        "quality_gate",
    }
    assert all(artifact.input_hash and artifact.output_hash for artifact in artifacts)
    assert result.state is not FactoryState.PUBLISHED


@pytest.mark.asyncio
async def test_team_stops_after_gateway_failure_and_persists_terminal_state(
    session_factory,
) -> None:
    repository = CourseFactoryRepository(session_factory)
    job = _structuring_job(repository)
    gateway = _Gateway(fail_role="lesson_author")
    team = CourseAgentTeam(repository, gateway)

    with pytest.raises(GatewayUnavailable):
        await team.run(
            CourseTeamInput(
                job_id=job.id,
                expected_revision=job.revision,
                correlation_id=str(uuid4()),
                course_id=job.course_id,
                audience="Professionals",
                target_outcome="Apply grounded AI safely",
                source_refs=("source://one/1",),
            )
        )

    failed = repository.get(job.id)
    assert failed.state is FactoryState.FAILED
    assert [call.role for call in gateway.calls] == [
        "architect",
        "concept_mapper",
        "lesson_author",
    ]


@pytest.mark.asyncio
async def test_rejected_job_has_zero_agent_or_gateway_calls(session_factory) -> None:
    repository = CourseFactoryRepository(session_factory)
    job = _structuring_job(repository)
    for current, target in (
        (FactoryState.STRUCTURING, FactoryState.AUTHORING),
        (FactoryState.AUTHORING, FactoryState.ASSESSING),
        (FactoryState.ASSESSING, FactoryState.VERIFYING),
        (FactoryState.VERIFYING, FactoryState.QUALITY_GATE),
        (FactoryState.QUALITY_GATE, FactoryState.REVIEW_READY),
    ):
        job = repository.advance(
            job.id,
            expected_revision=job.revision,
            target=target,
            artifact=_stage(current),
        )
    job = repository.advance(
        job.id,
        expected_revision=job.revision,
        target=FactoryState.REJECTED,
        reason_code="review_rejected",
    )
    gateway = _Gateway()
    team = CourseAgentTeam(repository, gateway)

    with pytest.raises(ValueError, match="structuring"):
        await team.run(
            CourseTeamInput(
                job_id=job.id,
                expected_revision=job.revision,
                correlation_id=str(uuid4()),
                course_id=job.course_id,
                audience="Professionals",
                target_outcome="Apply grounded AI safely",
                source_refs=("source://one/1",),
            )
        )

    assert gateway.calls == []
