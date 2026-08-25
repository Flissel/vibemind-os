from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from spaces.learning.contracts.mcp_models import ContractModel, SafeToken


Revision = Annotated[int, Field(ge=0)]
NormalizedScore = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
_SOURCE_LOCATOR = re.compile(r"^source://[A-Za-z0-9][A-Za-z0-9._:/-]{0,510}$")
_ARTIFACT_LOCATOR = re.compile(r"^artifact://[A-Za-z0-9][A-Za-z0-9._:/-]{0,510}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SourceReferenceV1(ContractModel):
    version: Literal["1"] = "1"
    source_id: SafeToken
    revision: Annotated[int, Field(ge=1)]
    locator: Annotated[str, Field(min_length=10, max_length=512)]
    title: Annotated[str, Field(min_length=1, max_length=500)]

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str) -> str:
        if not _SOURCE_LOCATOR.fullmatch(value):
            raise ValueError("source locator is invalid")
        return value


class CanvasHelpPolicyV1(ContractModel):
    version: Literal["1"] = "1"
    hint_mode: Literal["none", "selected_region", "guided"]
    tutor_allowed: bool
    max_hints: Annotated[int, Field(ge=0, le=20)]

    @model_validator(mode="after")
    def validate_disabled_policy(self) -> "CanvasHelpPolicyV1":
        if self.hint_mode == "none" and self.max_hints != 0:
            raise ValueError("disabled canvas help must have zero hints")
        if self.hint_mode != "none" and self.max_hints == 0:
            raise ValueError("enabled canvas help requires a hint allowance")
        return self


class PenEchoLaunchContextV1(ContractModel):
    version: Literal["1"] = "1"
    activity_id: UUID
    session_id: UUID
    course_id: UUID
    course_revision: Annotated[int, Field(ge=1)]
    actor_id: SafeToken
    mode: Literal["training", "exam"]
    help_policy: CanvasHelpPolicyV1
    source_refs: Annotated[tuple[SourceReferenceV1, ...], Field(min_length=1, max_length=256)]
    penecho_origin: Annotated[str, Field(min_length=18, max_length=200)]
    bridge_session_ref: SafeToken

    @field_validator("penecho_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError("PenEcho origin is invalid") from error
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("PenEcho origin must be an uncredentialed loopback origin")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_exam_policy(self) -> "PenEchoLaunchContextV1":
        if self.mode == "exam" and (
            self.help_policy.hint_mode != "none"
            or self.help_policy.tutor_allowed
            or self.help_policy.max_hints != 0
        ):
            raise ValueError("exam canvas help must be disabled")
        return self


class CanvasObjectV1(ContractModel):
    version: Literal["1"] = "1"
    object_id: SafeToken
    object_type: Literal["text", "line", "shape", "path", "image"]
    x: Annotated[float, Field(ge=-1_000_000, le=1_000_000, allow_inf_nan=False)]
    y: Annotated[float, Field(ge=-1_000_000, le=1_000_000, allow_inf_nan=False)]
    width: Annotated[float, Field(ge=0, le=1_000_000, allow_inf_nan=False)]
    height: Annotated[float, Field(ge=0, le=1_000_000, allow_inf_nan=False)]
    rotation: Annotated[float, Field(ge=-360, le=360, allow_inf_nan=False)]
    text: Annotated[str, Field(max_length=20_000)] | None = None
    asset_ref: Annotated[str, Field(max_length=512)] | None = None

    @field_validator("asset_ref")
    @classmethod
    def validate_asset_ref(cls, value: str | None) -> str | None:
        if value is not None and not _ARTIFACT_LOCATOR.fullmatch(value):
            raise ValueError("canvas artifact reference is invalid")
        return value

    @model_validator(mode="after")
    def validate_type_payload(self) -> "CanvasObjectV1":
        if self.object_type == "text" and not self.text:
            raise ValueError("text canvas objects require text")
        if self.object_type == "image" and self.asset_ref is None:
            raise ValueError("image canvas objects require an artifact reference")
        if self.object_type != "image" and self.asset_ref is not None:
            raise ValueError("only image objects may reference artifacts")
        return self


class CanvasDocumentV1(ContractModel):
    version: Literal["1"] = "1"
    project_id: UUID
    canvas_id: UUID
    revision: Revision
    background: Literal["transparent", "white", "grid"]
    objects: Annotated[tuple[CanvasObjectV1, ...], Field(max_length=5_000)]

    @model_validator(mode="after")
    def validate_unique_objects(self) -> "CanvasDocumentV1":
        identifiers = [item.object_id for item in self.objects]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("canvas object IDs must be unique")
        return self


class CanvasSaveV1(ContractModel):
    version: Literal["1"] = "1"
    expected_revision: Revision
    document: CanvasDocumentV1

    @model_validator(mode="after")
    def validate_revision(self) -> "CanvasSaveV1":
        if self.expected_revision != self.document.revision:
            raise ValueError("canvas save revision does not match the document")
        return self


class CanvasArtifactV1(ContractModel):
    version: Literal["1"] = "1"
    artifact_id: UUID
    artifact_kind: Literal["structured_canvas", "rendered_snapshot"]
    media_type: Literal["application/json", "image/png"]
    sha256: Annotated[str, Field(min_length=64, max_length=64)]
    size_bytes: Annotated[int, Field(ge=1, le=20_000_000)]
    locator: Annotated[str, Field(min_length=12, max_length=512)]
    canvas_revision: Revision

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("canvas artifact SHA-256 is invalid")
        return value

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str) -> str:
        if not _ARTIFACT_LOCATOR.fullmatch(value):
            raise ValueError("canvas artifact locator is invalid")
        return value

    @model_validator(mode="after")
    def validate_kind_media(self) -> "CanvasArtifactV1":
        expected = (
            "application/json"
            if self.artifact_kind == "structured_canvas"
            else "image/png"
        )
        if self.media_type != expected:
            raise ValueError("canvas artifact media type does not match its kind")
        return self


class CanvasSubmissionV1(ContractModel):
    version: Literal["1"] = "1"
    document: CanvasDocumentV1
    structured_artifact: CanvasArtifactV1
    rendered_snapshot: CanvasArtifactV1

    @model_validator(mode="after")
    def validate_artifacts(self) -> "CanvasSubmissionV1":
        if self.structured_artifact.artifact_kind != "structured_canvas":
            raise ValueError("canvas submission structured artifact is invalid")
        if self.rendered_snapshot.artifact_kind != "rendered_snapshot":
            raise ValueError("canvas submission snapshot is invalid")
        revisions = {
            self.document.revision,
            self.structured_artifact.canvas_revision,
            self.rendered_snapshot.canvas_revision,
        }
        if len(revisions) != 1:
            raise ValueError("canvas submission artifact revisions do not match")
        return self


class CanvasRubricCriterionV1(ContractModel):
    version: Literal["1"] = "1"
    criterion_id: SafeToken
    score: NormalizedScore
    feedback: Annotated[str, Field(min_length=1, max_length=5_000)]
    source_refs: Annotated[tuple[SourceReferenceV1, ...], Field(min_length=1, max_length=100)]


class CanvasRubricResultV1(ContractModel):
    version: Literal["1"] = "1"
    evaluation_id: UUID
    submission_id: UUID
    score: NormalizedScore
    confidence: NormalizedScore
    accepted: bool
    criteria: Annotated[tuple[CanvasRubricCriterionV1, ...], Field(min_length=1, max_length=100)]
    misconception_tags: Annotated[tuple[SafeToken, ...], Field(max_length=100)]
    evaluator_versions: dict[SafeToken, SafeToken]

    @model_validator(mode="after")
    def validate_result(self) -> "CanvasRubricResultV1":
        criterion_ids = [item.criterion_id for item in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("canvas rubric criterion IDs must be unique")
        if len(self.misconception_tags) != len(set(self.misconception_tags)):
            raise ValueError("canvas misconception tags must be unique")
        required_versions = {"model", "prompt", "sources"}
        if set(self.evaluator_versions) != required_versions:
            raise ValueError("canvas evaluator versions are incomplete")
        if any(not item.source_refs for item in self.criteria):
            raise ValueError("canvas rubric feedback requires source references")
        return self
