from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


_HASH_PATTERN = r"^[0-9a-f]{64}$"


class _StrictDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceProvenanceRef(_StrictDraft):
    source_id: str = Field(min_length=36, max_length=36)
    revision: int = Field(ge=1)
    content_hash: str = Field(pattern=_HASH_PATTERN)


class Citation(_StrictDraft):
    citation_id: str = Field(min_length=36, max_length=36)
    source_id: str = Field(min_length=36, max_length=36)
    source_revision: int = Field(ge=1)
    chunk_id: str = Field(min_length=36, max_length=36)
    content_hash: str = Field(pattern=_HASH_PATTERN)
    locator: dict[str, str | int] = Field(min_length=1, max_length=20)


class Claim(_StrictDraft):
    claim_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)
    citation_ids: list[str] = Field(max_length=100)


class ConceptDraft(_StrictDraft):
    concept_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)


class LessonDraft(_StrictDraft):
    lesson_id: str = Field(min_length=1, max_length=200)
    chapter_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=100_000)
    concept_ids: list[str] = Field(min_length=1, max_length=200)
    claims: list[Claim] = Field(max_length=2_000)


class RubricCriterion(_StrictDraft):
    criterion_id: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    points: int = Field(ge=1, le=1_000)


class ActivityDraft(_StrictDraft):
    activity_id: str = Field(min_length=1, max_length=200)
    chapter_id: str = Field(min_length=1, max_length=200)
    type: Literal["quiz", "open", "case", "code", "penecho_canvas"]
    concept_ids: list[str] = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=20_000)
    expected_answer: str = Field(max_length=50_000)
    citation_ids: list[str] = Field(max_length=100)
    rubric: list[RubricCriterion] = Field(max_length=100)


class ChapterDraft(_StrictDraft):
    chapter_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    lesson_ids: list[str] = Field(min_length=1, max_length=1_000)
    activity_ids: list[str] = Field(min_length=1, max_length=2_000)


class CourseDraft(_StrictDraft):
    schema_version: Literal["course-draft-v1"]
    job_id: str = Field(min_length=36, max_length=36)
    attempt_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    outcomes: list[str] = Field(min_length=1, max_length=100)
    source_provenance: list[SourceProvenanceRef] = Field(
        min_length=1, max_length=256
    )
    citations: list[Citation] = Field(min_length=1, max_length=10_000)
    concepts: list[ConceptDraft] = Field(min_length=1, max_length=2_000)
    chapters: list[ChapterDraft] = Field(min_length=1, max_length=500)
    lessons: list[LessonDraft] = Field(min_length=1, max_length=5_000)
    activities: list[ActivityDraft] = Field(min_length=1, max_length=10_000)
