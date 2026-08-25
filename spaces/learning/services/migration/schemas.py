from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


CanonicalRecordType = Literal[
    "organization",
    "program",
    "course",
    "chapter",
    "concept",
    "source",
    "source_revision",
    "source_chunk",
    "activity",
    "adaptive_item",
    "session",
    "response",
    "evaluation",
    "mastery_evidence",
    "artifact",
    "review_schedule",
    "misconception",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CanonicalImportEnvelope(_StrictModel):
    version: Literal["learning-import-envelope-v1"] = "learning-import-envelope-v1"
    record_type: CanonicalRecordType
    canonical_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    source_system: Literal["learning_plattform_v1"]
    source_entity: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    source_id: str = Field(min_length=1, max_length=512)
    migration_batch_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload: dict[str, JsonValue]


class TransformationIssue(_StrictModel):
    version: Literal["learning-transform-issue-v1"] = "learning-transform-issue-v1"
    severity: Literal["warning", "quarantine"]
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    source_system: Literal["learning_plattform_v1"]
    source_entity: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    source_id: str = Field(min_length=1, max_length=512)
    migration_batch_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    source_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    details: dict[str, JsonValue] = Field(default_factory=dict)


class TransformationResult(_StrictModel):
    version: Literal["learning-transform-result-v1"] = "learning-transform-result-v1"
    migration_batch_id: str
    envelopes: tuple[CanonicalImportEnvelope, ...]
    warnings: tuple[TransformationIssue, ...]
    quarantines: tuple[TransformationIssue, ...]
