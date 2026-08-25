from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


SOURCE_SYSTEM = "learning_plattform_v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LegacyExportRecord(_StrictModel):
    version: Literal["legacy-export-record-v1"] = "legacy-export-record-v1"
    source_system: Literal["learning_plattform_v1"] = SOURCE_SYSTEM
    migration_batch_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    entity: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    source_id: str = Field(min_length=1, max_length=512)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload: dict[str, JsonValue]


class LegacyExportManifest(_StrictModel):
    version: Literal["legacy-export-manifest-v1"] = "legacy-export-manifest-v1"
    source_system: Literal["learning_plattform_v1"] = SOURCE_SYSTEM
    batch_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    record_count: int = Field(ge=0)
    counts: dict[str, int]
    records_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    table_inventory: dict[str, int]
    excluded_authority: tuple[str, ...] = (
        "legacy_vectors",
        "provider_credentials",
    )


@dataclass(frozen=True)
class LegacySnapshot:
    tables: Mapping[str, Sequence[Mapping[str, object]]]
    artifact_root: Path | None = None
    artifact_paths: tuple[Path, ...] = ()
    mistake_journal: Sequence[Mapping[str, object]] = field(default_factory=tuple)


LEGACY_EXPORT_COLUMNS: dict[str, tuple[str, ...]] = {
    "universities": (
        "id", "name", "slug", "city", "postal_code", "street",
        "created_at", "updated_at",
    ),
    "study_programs": (
        "id", "university_id", "name", "slug", "created_at", "updated_at",
    ),
    "app_users": (
        "id", "university_id", "study_program_id", "role", "created_at", "updated_at",
    ),
    "professors": (
        "id", "university_id", "first_name", "last_name", "created_at", "updated_at",
    ),
    "modules": (
        "id", "study_program_id", "professor_id", "name", "slug",
        "created_at", "updated_at",
    ),
    "topics": (
        "id", "module_id", "created_by_user_id", "name", "slug", "description",
        "created_at", "updated_at",
    ),
    "hashtags": ("id", "topic_id", "name", "created_at"),
    "questions": (
        "id", "topic_id", "created_by_user_id", "source_open_question_id", "text",
        "difficulty", "verified_at", "created_at", "updated_at",
    ),
    "answers": (
        "id", "question_id", "created_by_user_id", "source_open_answer_id", "text",
        "is_correct", "explanation", "verified_at", "created_at", "updated_at",
    ),
    "open_questions": (
        "id", "topic_id", "submitted_by_user_id", "text", "status",
        "suggested_topic_id", "suggested_hashtag_id", "ai_confidence",
        "moderated_by_user_id", "moderated_at", "rejection_reason",
        "created_question_id", "created_at", "updated_at",
    ),
    "open_answers": (
        "id", "open_question_id", "question_id", "submitted_by_user_id", "text",
        "status", "ai_score", "string_match_score", "hashtag_matches_count",
        "likes_count", "moderated_by_user_id", "moderated_at", "rejection_reason",
        "created_answer_id", "created_at", "updated_at",
    ),
    "task_catalog_items": (
        "id", "topic_id", "source_question_id", "format", "prompt",
        "expected_answer", "payload", "difficulty", "report_status",
        "created_at", "updated_at",
    ),
    "quizzes": (
        "id", "user_id", "module_id", "topic_id", "quiz_type", "question_count",
        "difficulty", "created_at",
    ),
    "quiz_questions": (
        "id", "quiz_id", "task_catalog_item_id", "position", "created_at",
    ),
    "quiz_attempts": (
        "id", "quiz_id", "user_id", "started_at", "finished_at", "duration_seconds",
        "score", "created_at",
    ),
    "quiz_answer_submissions": (
        "id", "attempt_id", "quiz_question_id", "submitted_answer", "is_correct",
        "answer_time_seconds", "score", "needs_review", "feedback", "created_at",
    ),
    "evaluations": (
        "id", "user_id", "quiz_id", "attempt_id", "topic_progress_percent",
        "correct_answers", "total_questions", "quiz_difficulty", "task_difficulty",
        "average_answer_time_seconds", "needs_review", "created_at",
    ),
    "knowledge_documents": (
        "id", "topic_id", "source_type", "source_id", "title", "content",
        "content_hash", "created_by_user_id", "created_at", "updated_at",
    ),
    "knowledge_chunks": (
        "id", "document_id", "topic_id", "chunk_index", "content", "content_hash",
        "indexed_at", "created_at",
    ),
    "moderation_reports": (
        "id", "reporter_user_id", "question_id", "task_catalog_item_id", "reason",
        "status", "resolved_by_user_id", "resolved_at", "created_at",
    ),
}


MISTAKE_JOURNAL_COLUMNS = (
    "id",
    "actor_id",
    "topic_id",
    "task_id",
    "misconception",
    "notes",
    "status",
    "created_at",
    "updated_at",
)
