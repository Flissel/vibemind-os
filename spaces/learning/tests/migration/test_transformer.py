from __future__ import annotations

import hashlib
import json

from spaces.learning.services.migration.legacy_schema import LegacyExportRecord
from spaces.learning.services.migration.transformer import transform_legacy_records


IDS = {
    name: f"00000000-0000-0000-0000-{index:012d}"
    for index, name in enumerate((
        "university", "program", "module", "topic", "document", "chunk",
        "question", "answer", "task", "quiz", "quiz_question", "attempt",
        "submission", "evaluation",
    ), start=1)
}


def _record(entity: str, source_id: str, **payload: object) -> LegacyExportRecord:
    value = {"id": source_id, **payload}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return LegacyExportRecord(
        migration_batch_id="batch-1",
        entity=entity,
        source_id=source_id,
        content_hash=hashlib.sha256(encoded).hexdigest(),
        payload=value,
    )


def _complete_records() -> list[LegacyExportRecord]:
    return [
        _record("universities", IDS["university"], name="Demo", slug="demo"),
        _record(
            "study_programs", IDS["program"], university_id=IDS["university"],
            name="AI", slug="ai",
        ),
        _record(
            "modules", IDS["module"], study_program_id=IDS["program"],
            name="Safety", slug="safety",
        ),
        _record(
            "topics", IDS["topic"], module_id=IDS["module"],
            name="Authority", slug="authority", description="Execution authority",
        ),
        _record(
            "knowledge_documents", IDS["document"], topic_id=IDS["topic"],
            source_type="manual", title="Authority Notes", content="OpenFang authorizes.",
            content_hash="a" * 64,
        ),
        _record(
            "knowledge_chunks", IDS["chunk"], document_id=IDS["document"],
            topic_id=IDS["topic"], chunk_index=0, content="OpenFang authorizes.",
            content_hash="b" * 64,
        ),
        _record(
            "questions", IDS["question"], topic_id=IDS["topic"],
            text="Who authorizes?", difficulty=3,
        ),
        _record(
            "answers", IDS["answer"], question_id=IDS["question"],
            text="OpenFang", is_correct=True, explanation="Control plane authority.",
        ),
        _record(
            "task_catalog_items", IDS["task"], topic_id=IDS["topic"],
            source_question_id=IDS["question"], format="definition",
            prompt="Name the authority", expected_answer="OpenFang", payload={},
            difficulty=3, report_status="open",
        ),
        _record(
            "quizzes", IDS["quiz"], user_id="actor-1", module_id=IDS["module"],
            topic_id=IDS["topic"], quiz_type="comprehension", question_count=1,
            difficulty=3,
        ),
        _record(
            "quiz_questions", IDS["quiz_question"], quiz_id=IDS["quiz"],
            task_catalog_item_id=IDS["task"], position=1,
        ),
        _record(
            "quiz_attempts", IDS["attempt"], quiz_id=IDS["quiz"], user_id="actor-1",
            score=100, started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:01:00Z", duration_seconds=60,
        ),
        _record(
            "quiz_answer_submissions", IDS["submission"], attempt_id=IDS["attempt"],
            quiz_question_id=IDS["quiz_question"], submitted_answer={"text": "OpenFang"},
            is_correct=True, score=100, needs_review=False, answer_time_seconds=60,
        ),
        _record(
            "evaluations", IDS["evaluation"], user_id="actor-1", quiz_id=IDS["quiz"],
            attempt_id=IDS["attempt"], topic_progress_percent=100,
            correct_answers=1, total_questions=1, needs_review=False,
        ),
        _record(
            "mistake_journal", "mistake-1", actor_id="actor-1",
            topic_id=IDS["topic"], misconception="Confused MCP and OpenFang",
            status="unresolved",
        ),
    ]


def test_transform_maps_every_design_group_to_versioned_canonical_envelopes() -> None:
    first = transform_legacy_records(_complete_records())
    second = transform_legacy_records(list(reversed(_complete_records())))

    kinds = {item.record_type for item in first.envelopes}
    assert {
        "organization", "program", "course", "chapter", "concept",
        "source", "source_revision", "source_chunk", "activity", "adaptive_item",
        "session", "response", "evaluation", "mastery_evidence",
        "review_schedule", "misconception",
    } <= kinds
    assert first == second
    assert first.quarantines == ()
    assert all(item.source_system == "learning_plattform_v1" for item in first.envelopes)
    assert all(item.migration_batch_id == "batch-1" for item in first.envelopes)
    assert all(item.source_id and len(item.content_hash) == 64 for item in first.envelopes)
    assert len({item.canonical_id for item in first.envelopes}) == len(first.envelopes)

    chunk = next(item for item in first.envelopes if item.record_type == "source_chunk")
    assert chunk.payload["ordinal"] == 0
    assert "vector" not in chunk.payload
    evaluation = next(item for item in first.envelopes if item.record_type == "evaluation")
    assert evaluation.payload["score"] == 1.0
    mastery = next(item for item in first.envelopes if item.record_type == "mastery_evidence")
    assert mastery.payload["score"] == 1.0


def test_transform_quarantines_broken_relationships_scores_formats_and_artifacts() -> None:
    broken = [
        _record(
            "topics", "topic-orphan", module_id="missing-module",
            name="Orphan", slug="orphan",
        ),
        _record(
            "task_catalog_items", "task-unsupported", topic_id="topic-orphan",
            format="telepathy", prompt="Unsupported", expected_answer="none",
            payload={}, difficulty=3,
        ),
        _record(
            "quiz_answer_submissions", "submission-invalid", attempt_id="missing-attempt",
            quiz_question_id="missing-question", submitted_answer={"text": "x"},
            is_correct=False, score=140,
        ),
        _record(
            "artifacts", "artifact:orphan", relative_path="orphan.bin",
            sha256="f" * 64, size_bytes=4, media_type="application/octet-stream",
            trusted_vector_data=False,
        ),
    ]
    duplicate = _record("universities", IDS["university"], name="First", slug="first")
    conflicting = _record("universities", IDS["university"], name="Other", slug="other")

    result = transform_legacy_records([*broken, duplicate, conflicting])
    codes = {item.code for item in result.quarantines}

    assert "missing_parent" in codes
    assert "unsupported_task_format" in codes
    assert "invalid_score" in codes
    assert "orphan_artifact" in codes
    assert "duplicate_source_id" in codes
    assert all(item.source_id and item.migration_batch_id == "batch-1" for item in result.quarantines)


def test_transform_deduplicates_identical_source_records_with_warning() -> None:
    record = _record("universities", IDS["university"], name="Demo", slug="demo")
    result = transform_legacy_records([record, record])

    assert len(result.envelopes) == 1
    assert [warning.code for warning in result.warnings] == ["duplicate_identical_record"]
