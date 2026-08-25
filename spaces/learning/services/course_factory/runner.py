from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import ValidationError

from spaces.learning.services.course_factory.artifact_store import CourseFactoryArtifactStore
from spaces.learning.services.course_factory.catalog import SourceCatalogLoader
from spaces.learning.services.course_factory.quality_gate import (
    QualityGate,
    QualityReport,
)
from spaces.learning.services.course_factory.publisher import CourseDraftPublisher
from spaces.learning.services.course_factory.repository import (
    CourseFactoryRepository,
    FactoryJobRecord,
    StageArtifactInput,
)
from spaces.learning.services.course_factory.roles.schemas import (
    QualityReviewOutput,
    SourceVerificationOutput,
)
from spaces.learning.services.course_factory.state_machine import FactoryState
from spaces.learning.services.course_factory.team import CourseAgentTeam, CourseTeamInput
from spaces.learning.services.course_factory.verification import SourceSnapshot


@dataclass(frozen=True)
class GenerationRunResult:
    job: FactoryJobRecord
    report: QualityReport


class CourseFactoryRunner:
    def __init__(
        self,
        *,
        repository: CourseFactoryRepository,
        artifact_store: CourseFactoryArtifactStore,
        catalog: SourceCatalogLoader,
        team: CourseAgentTeam,
        quality_gate: QualityGate,
        publisher: CourseDraftPublisher,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store
        self._catalog = catalog
        self._team = team
        self._quality_gate = quality_gate
        self._publisher = publisher

    async def run_job(self, job_id: str) -> GenerationRunResult:
        job = self._repository.get(job_id)
        if job.state is FactoryState.QUEUED:
            job = self._repository.advance(
                job.id,
                expected_revision=job.revision,
                target=FactoryState.INGESTING,
            )
        attempt = self._repository.get_current_attempt(job.id)
        request = attempt.generation_request
        if request is None:
            self._fail(job.id, "generation_request_missing")
            raise ValueError("course factory generation request is missing")
        try:
            catalog = self._catalog.load(
                course_id=job.course_id,
                provenance=attempt.provenance,
            )
            if job.state is FactoryState.INGESTING:
                job = self._complete_ingestion(job, catalog.snapshots)
            team_request = CourseTeamInput(
                job_id=job.id,
                expected_revision=job.revision,
                correlation_id=request.correlation_id,
                course_id=job.course_id,
                audience=request.audience,
                target_outcome=request.target_outcome,
                source_refs=catalog.model_refs,
            )
            if job.state in {
                FactoryState.STRUCTURING,
                FactoryState.AUTHORING,
                FactoryState.ASSESSING,
                FactoryState.VERIFYING,
            }:
                job = await self._team.run(team_request)
                team_request = CourseTeamInput(
                    **{
                        **team_request.__dict__,
                        "expected_revision": job.revision,
                    }
                )
            if job.state is not FactoryState.QUALITY_GATE:
                raise ValueError("course factory job cannot run generation")
            verified = self._read_verified_output(job)
            report = self._quality_gate.inspect(
                job.id,
                expected_revision=job.revision,
                draft=verified.draft,
                sources=catalog.snapshots,
                declared_unsupported_claim_ids=tuple(
                    verified.unsupported_claim_ids
                ),
            )
            if not report.approved:
                return GenerationRunResult(job=job, report=report)
            review = self._read_quality_output(job)
            if review is None:
                review = await self._team.run_quality_review(
                    team_request,
                    draft=verified.draft,
                    deterministic_issue_codes=(),
                )
            await self._publisher.stage_review_draft(
                job_id=job.id,
                expected_factory_revision=job.revision,
                expected_learnhouse_revision=request.learnhouse_revision,
                draft=verified.draft,
                correlation_id=request.correlation_id,
            )
            promoted = self._quality_gate.evaluate_and_promote(
                job.id,
                expected_revision=job.revision,
                draft=verified.draft,
                sources=catalog.snapshots,
                ai_review=review,
            )
            return GenerationRunResult(job=promoted.job, report=promoted.report)
        except (LookupError, ValidationError, ValueError):
            current = self._repository.get(job.id)
            if current.state not in {
                FactoryState.QUALITY_GATE,
                FactoryState.REVIEW_READY,
                FactoryState.FAILED,
            }:
                self._fail(job.id, "generation_validation_failed")
            raise

    def _complete_ingestion(
        self, job: FactoryJobRecord, snapshots: tuple[SourceSnapshot, ...]
    ) -> FactoryJobRecord:
        payload = {
            "schema_version": "factory-ingestion-v1",
            "sources": [
                {
                    "source_id": source.source_id,
                    "revision": source.revision,
                    "content_hash": source.content_hash,
                    "chunk_ids": [chunk.chunk_id for chunk in source.chunks],
                }
                for source in snapshots
            ],
        }
        try:
            self._artifact_store.read_stage_output(
                job.id,
                attempt_number=job.attempt_number,
                stage=FactoryState.INGESTING,
            )
        except LookupError:
            artifact = self._artifact_store.write_stage_output(
                job,
                stage=FactoryState.INGESTING,
                payload=payload,
            )
            self._repository.record_stage_artifact(
                job.id,
                expected_revision=job.revision,
                artifact=StageArtifactInput(
                    stage=FactoryState.INGESTING,
                    input_hash=_hash_json(payload),
                    output_hash=artifact.content_hash,
                    evidence_refs=(
                        *(
                            f"source://{source.source_id}/{source.revision}"
                            for source in snapshots
                        ),
                        f"learning-artifact://{artifact.artifact_id}",
                    ),
                    output_artifact=artifact,
                ),
            )
        return self._repository.advance(
            job.id,
            expected_revision=job.revision,
            target=FactoryState.STRUCTURING,
        )

    def _read_verified_output(self, job: FactoryJobRecord) -> SourceVerificationOutput:
        payload = self._artifact_store.read_stage_output(
            job.id,
            attempt_number=job.attempt_number,
            stage=FactoryState.VERIFYING,
        )
        return SourceVerificationOutput.model_validate(payload.get("source_verifier"))

    def _read_quality_output(self, job: FactoryJobRecord) -> QualityReviewOutput | None:
        try:
            payload = self._artifact_store.read_stage_output(
                job.id,
                attempt_number=job.attempt_number,
                stage=FactoryState.QUALITY_GATE,
            )
        except LookupError:
            return None
        return QualityReviewOutput.model_validate(payload.get("quality_reviewer"))

    def _fail(self, job_id: str, reason_code: str) -> None:
        current = self._repository.get(job_id)
        if current.state not in {
            FactoryState.FAILED,
            FactoryState.CANCELLED,
            FactoryState.REJECTED,
            FactoryState.PUBLISHED,
        }:
            self._repository.advance(
                job_id,
                expected_revision=current.revision,
                target=FactoryState.FAILED,
                reason_code=reason_code,
            )


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
