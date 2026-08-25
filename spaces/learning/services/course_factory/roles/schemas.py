from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArchitectOutput(_StrictOutput):
    schema_version: Literal["architect-v1"]
    audience: str = Field(min_length=1, max_length=500)
    prerequisites: list[str] = Field(max_length=50)
    outcomes: list[str] = Field(min_length=1, max_length=50)
    chapters: list[str] = Field(min_length=1, max_length=50)


class ConceptDependency(_StrictOutput):
    before: str = Field(min_length=1, max_length=200)
    after: str = Field(min_length=1, max_length=200)


class ConceptMapOutput(_StrictOutput):
    schema_version: Literal["concept-map-v1"]
    concepts: list[str] = Field(min_length=1, max_length=500)
    dependencies: list[ConceptDependency] = Field(max_length=1_000)
    chapter_coverage: dict[str, list[str]] = Field(max_length=100)


class LessonDraft(_StrictOutput):
    chapter: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=100_000)


class LessonOutput(_StrictOutput):
    schema_version: Literal["lesson-v1"]
    lessons: list[LessonDraft] = Field(min_length=1, max_length=500)


class AssessmentDraft(_StrictOutput):
    concept: str = Field(min_length=1, max_length=300)
    type: Literal["quiz", "open", "case", "code", "penecho_canvas"]
    prompt: str = Field(min_length=1, max_length=20_000)


class AssessmentOutput(_StrictOutput):
    schema_version: Literal["assessment-v1"]
    activities: list[AssessmentDraft] = Field(min_length=1, max_length=1_000)


class SourceVerificationOutput(_StrictOutput):
    schema_version: Literal["source-verification-v1"]
    supported_claims: list[str] = Field(max_length=5_000)
    unsupported_claims: list[str] = Field(max_length=5_000)
    citation_refs: list[str] = Field(max_length=10_000)


class QualityReviewOutput(_StrictOutput):
    schema_version: Literal["quality-review-v1"]
    decision: Literal["pass", "revise"]
    issues: list[str] = Field(max_length=1_000)
    score: float = Field(ge=0, le=1)
