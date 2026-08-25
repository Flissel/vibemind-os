from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from spaces.learning.services.course_factory.artifact_store import (
    CourseFactoryArtifactStore,
)
from spaces.learning.services.course_factory.repository import (
    CourseFactoryRepository,
    DraftDeliveryInput,
    FactoryJobRecord,
    PublicationInput,
)
from spaces.learning.services.course_factory.schemas import CourseDraft
from spaces.learning.services.course_factory.state_machine import FactoryState


_EVIDENCE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublishConfirmation(_StrictModel):
    confirmed: Literal[True]
    approval_ref: str = Field(min_length=3, max_length=128)

    @field_validator("approval_ref")
    @classmethod
    def validate_approval_ref(cls, value: str) -> str:
        if not _EVIDENCE_TOKEN.fullmatch(value):
            raise ValueError("approval_ref must be a safe evidence token")
        return value


class DraftDeliveryReceipt(_StrictModel):
    course_id: str
    factory_job_id: str
    attempt_number: int = Field(ge=1)
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    learnhouse_revision: int = Field(ge=1)
    evidence_ref: str = Field(min_length=1, max_length=512)

    @field_validator("course_id", "factory_job_id")
    @classmethod
    def validate_uuid(cls, value: str) -> str:
        return str(UUID(value))


class LearnHouseCourseReadback(_StrictModel):
    course_id: str
    revision: int = Field(ge=1)
    review_status: Literal["draft", "approved", "rejected"]
    published: bool

    @field_validator("course_id")
    @classmethod
    def validate_course_id(cls, value: str) -> str:
        return str(UUID(value))


class LearnHouseFactoryGateway(Protocol):
    async def stage_draft(
        self,
        *,
        course_id: str,
        expected_revision: int,
        draft: CourseDraft,
        draft_hash: str,
        correlation_id: str,
    ) -> DraftDeliveryReceipt: ...

    async def approve(
        self, *, course_id: str, expected_revision: int, correlation_id: str
    ) -> LearnHouseCourseReadback: ...

    async def publish(
        self, *, course_id: str, expected_revision: int, correlation_id: str
    ) -> LearnHouseCourseReadback: ...

    async def read_course(
        self, *, course_id: str, correlation_id: str
    ) -> LearnHouseCourseReadback: ...


class CourseDraftPublisher:
    def __init__(
        self,
        *,
        repository: CourseFactoryRepository,
        artifact_store: CourseFactoryArtifactStore,
        learnhouse: LearnHouseFactoryGateway,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store
        self._learnhouse = learnhouse

    async def stage_review_draft(
        self,
        *,
        job_id: str,
        expected_factory_revision: int,
        expected_learnhouse_revision: int,
        draft: CourseDraft,
        correlation_id: str,
    ) -> DraftDeliveryReceipt:
        UUID(correlation_id)
        job = self._repository.get(job_id)
        if job.revision != expected_factory_revision:
            raise RuntimeError("factory revision changed before draft delivery")
        if job.state not in {FactoryState.QUALITY_GATE, FactoryState.REVIEW_READY}:
            raise RuntimeError("factory draft is not reviewable")
        if draft.job_id != job.id or draft.attempt_number != job.attempt_number:
            raise RuntimeError("factory draft does not match current attempt")
        persisted = self._load_verified_draft(job)
        if persisted != draft:
            raise RuntimeError("factory draft differs from verified artifact")
        draft_hash = _hash_draft(draft)
        receipt = await self._learnhouse.stage_draft(
            course_id=job.course_id,
            expected_revision=expected_learnhouse_revision,
            draft=draft,
            draft_hash=draft_hash,
            correlation_id=correlation_id,
        )
        if (
            receipt.course_id != job.course_id
            or receipt.factory_job_id != job.id
            or receipt.attempt_number != job.attempt_number
            or receipt.draft_hash != draft_hash
            or receipt.learnhouse_revision != expected_learnhouse_revision + 1
        ):
            raise RuntimeError("LearnHouse draft readback did not match delivery")
        self._repository.record_draft_delivery(
            job.id,
            expected_revision=job.revision,
            delivery=DraftDeliveryInput(
                draft_hash=draft_hash,
                learnhouse_revision=receipt.learnhouse_revision,
                evidence_ref=receipt.evidence_ref,
            ),
        )
        return receipt

    async def publish(
        self,
        *,
        job_id: str,
        expected_factory_revision: int,
        confirmation: PublishConfirmation,
        correlation_id: str,
    ) -> FactoryJobRecord:
        UUID(correlation_id)
        job = self._repository.get(job_id)
        if job.revision != expected_factory_revision:
            raise RuntimeError("factory revision changed before publication")
        if job.state is not FactoryState.REVIEW_READY:
            raise RuntimeError("only a review-ready factory job can be published")
        delivery = self._repository.get_draft_delivery(job.id)

        approved = await self._learnhouse.approve(
            course_id=job.course_id,
            expected_revision=delivery.learnhouse_revision,
            correlation_id=correlation_id,
        )
        self._require_readback(
            approved,
            job=job,
            expected_revision=delivery.learnhouse_revision + 1,
            published=False,
        )
        published = await self._learnhouse.publish(
            course_id=job.course_id,
            expected_revision=approved.revision,
            correlation_id=correlation_id,
        )
        self._require_readback(
            published,
            job=job,
            expected_revision=approved.revision + 1,
            published=True,
        )
        readback = await self._learnhouse.read_course(
            course_id=job.course_id, correlation_id=correlation_id
        )
        self._require_readback(
            readback,
            job=job,
            expected_revision=published.revision,
            published=True,
        )
        return self._repository.mark_published(
            job.id,
            expected_revision=job.revision,
            publication=PublicationInput(
                draft_hash=delivery.draft_hash,
                learnhouse_revision=readback.revision,
                confirmation_ref=confirmation.approval_ref,
                readback_evidence_ref=(
                    f"learnhouse://courses/{job.course_id}/revision/{readback.revision}"
                ),
            ),
        )

    def _load_verified_draft(self, job: FactoryJobRecord) -> CourseDraft:
        payload = self._artifact_store.read_stage_output(
            job.id,
            attempt_number=job.attempt_number,
            stage=FactoryState.VERIFYING,
        )
        verifier = payload.get("source_verifier")
        if not isinstance(verifier, dict):
            raise RuntimeError("verified factory artifact is missing")
        candidate = verifier.get("draft")
        return CourseDraft.model_validate(candidate)

    @staticmethod
    def _require_readback(
        value: LearnHouseCourseReadback,
        *,
        job: FactoryJobRecord,
        expected_revision: int,
        published: bool,
    ) -> None:
        if (
            value.course_id != job.course_id
            or value.revision != expected_revision
            or value.review_status != "approved"
            or value.published is not published
        ):
            raise RuntimeError("LearnHouse publication readback was not verified")


def _hash_draft(draft: CourseDraft) -> str:
    payload = json.dumps(
        draft.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
