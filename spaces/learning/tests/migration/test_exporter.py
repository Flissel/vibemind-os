from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from spaces.learning.services.migration.exporter import LegacyExporter
from spaces.learning.services.migration.legacy_schema import LegacySnapshot


def _row(**values: object) -> dict[str, object]:
    return {"id": str(uuid4()), **values}


def _snapshot(artifact_root: Path) -> LegacySnapshot:
    university = _row(name="Demo University", slug="demo")
    program = _row(university_id=university["id"], name="AI", slug="ai")
    module = _row(study_program_id=program["id"], name="Safety", slug="safety")
    topic = _row(module_id=module["id"], name="Authority", slug="authority")
    question = _row(topic_id=topic["id"], text="Who authorizes execution?", difficulty=3)
    answer = _row(question_id=question["id"], text="OpenFang", is_correct=True)
    task = _row(
        topic_id=topic["id"], source_question_id=question["id"],
        format="definition", prompt="Name the authority", expected_answer="OpenFang",
        payload={}, difficulty=3, report_status="open",
    )
    quiz = _row(
        user_id="actor-1", module_id=module["id"], topic_id=topic["id"],
        quiz_type="comprehension", question_count=1, difficulty=3,
    )
    quiz_question = _row(
        quiz_id=quiz["id"], task_catalog_item_id=task["id"], position=1,
    )
    attempt = _row(
        quiz_id=quiz["id"], user_id="actor-1", score=100,
        started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T00:01:00Z",
    )
    submission = _row(
        attempt_id=attempt["id"], quiz_question_id=quiz_question["id"],
        submitted_answer={"text": "OpenFang"}, is_correct=True, score=100,
    )
    evaluation = _row(
        user_id="actor-1", quiz_id=quiz["id"], attempt_id=attempt["id"],
        topic_progress_percent=100, correct_answers=1, total_questions=1,
        quiz_difficulty=3, task_difficulty=3, average_answer_time_seconds=60,
        needs_review=False,
    )
    document = _row(
        topic_id=topic["id"], source_type="manual", source_id=str(uuid4()),
        title="Authority Notes", content="OpenFang authorizes provider execution.",
        content_hash="a" * 64,
    )
    chunk = _row(
        document_id=document["id"], topic_id=topic["id"], chunk_index=0,
        content="OpenFang authorizes provider execution.", content_hash="b" * 64,
        qdrant_point_id=str(uuid4()), vector=[0.1, 0.2], embedding=[0.3, 0.4],
    )
    artifact = artifact_root / "source.txt"
    artifact.write_text("local source artifact", encoding="utf-8")

    return LegacySnapshot(
        tables={
            "universities": (university,),
            "study_programs": (program,),
            "modules": (module,),
            "topics": (topic,),
            "knowledge_documents": (document,),
            "questions": (question,),
            "answers": (answer,),
            "task_catalog_items": (task,),
            "quizzes": (quiz,),
            "quiz_questions": (quiz_question,),
            "quiz_attempts": (attempt,),
            "quiz_answer_submissions": (submission,),
            "evaluations": (evaluation,),
            "knowledge_chunks": (chunk,),
        },
        artifact_root=artifact_root,
        artifact_paths=(Path("source.txt"),),
        mistake_journal=({
            "id": "mistake-1",
            "actor_id": "actor-1",
            "topic_id": topic["id"],
            "misconception": "Confused MCP with provider authority",
            "status": "unresolved",
        },),
    )


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_export_covers_legacy_learning_groups_with_provenance_and_hashes(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "legacy-artifacts"
    artifact_root.mkdir()
    output = tmp_path / "export"

    manifest = LegacyExporter().export(
        _snapshot(artifact_root),
        output,
        batch_id="legacy-fixture-001",
    )
    records = _records(output / "records.jsonl")

    entities = {record["entity"] for record in records}
    assert {
        "universities", "study_programs", "modules", "topics",
        "knowledge_documents", "questions", "answers", "task_catalog_items",
        "quizzes", "quiz_questions", "quiz_attempts", "quiz_answer_submissions",
        "evaluations", "knowledge_chunks", "artifacts", "mistake_journal",
    } <= entities
    assert manifest.batch_id == "legacy-fixture-001"
    assert manifest.source_system == "learning_plattform_v1"
    assert manifest.record_count == len(records)
    assert sum(manifest.counts.values()) == len(records)
    assert all(record["migration_batch_id"] == manifest.batch_id for record in records)
    assert all(record["source_system"] == manifest.source_system for record in records)
    assert all(len(record["content_hash"]) == 64 for record in records)
    assert all(record["source_id"] for record in records)
    assert (output / "manifest.json").is_file()


def test_export_is_byte_stable_and_never_trusts_legacy_vectors_or_secrets(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "legacy-artifacts"
    artifact_root.mkdir()
    snapshot = _snapshot(artifact_root)
    snapshot.tables["knowledge_documents"][0]["api_key"] = "secret-shaped-value"

    first, second = tmp_path / "first", tmp_path / "second"
    LegacyExporter().export(snapshot, first, batch_id="stable-batch")
    LegacyExporter().export(snapshot, second, batch_id="stable-batch")

    assert (first / "records.jsonl").read_bytes() == (second / "records.jsonl").read_bytes()
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    records = _records(first / "records.jsonl")
    chunk = next(record for record in records if record["entity"] == "knowledge_chunks")
    encoded = json.dumps(records, sort_keys=True)
    assert "vector" not in chunk["payload"]
    assert "embedding" not in chunk["payload"]
    assert "qdrant_point_id" not in chunk["payload"]
    assert "secret-shaped-value" not in encoded
    artifact = next(record for record in records if record["entity"] == "artifacts")
    assert artifact["payload"]["relative_path"] == "source.txt"
    assert artifact["payload"]["trusted_vector_data"] is False


def test_export_rejects_artifacts_outside_the_declared_read_only_root(
    tmp_path: Path,
) -> None:
    import pytest

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    snapshot = LegacySnapshot(
        tables={},
        artifact_root=root,
        artifact_paths=(Path("..") / "outside.txt",),
    )

    with pytest.raises(ValueError, match="artifact path"):
        LegacyExporter().export(snapshot, tmp_path / "output", batch_id="unsafe")
