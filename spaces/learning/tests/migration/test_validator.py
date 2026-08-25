from __future__ import annotations

import json
from pathlib import Path

from spaces.learning.services.migration.exporter import LegacyExporter
from spaces.learning.services.migration.legacy_schema import LegacySnapshot
from spaces.learning.services.migration.report import write_validation_report
from spaces.learning.services.migration.transformer import transform_legacy_records
from spaces.learning.services.migration.validator import load_export, validate_dry_run


UNIVERSITY = "00000000-0000-0000-0000-000000000001"
PROGRAM = "00000000-0000-0000-0000-000000000002"
MODULE = "00000000-0000-0000-0000-000000000003"
TOPIC = "00000000-0000-0000-0000-000000000004"
QUESTION = "00000000-0000-0000-0000-000000000005"
TASK = "00000000-0000-0000-0000-000000000006"


def _snapshot(
    *,
    artifact_root: Path | None = None,
    artifact_paths: tuple[Path, ...] = (),
) -> LegacySnapshot:
    return LegacySnapshot(
        tables={
            "universities": ({"id": UNIVERSITY, "name": "Demo", "slug": "demo"},),
            "study_programs": ({
                "id": PROGRAM, "university_id": UNIVERSITY, "name": "AI", "slug": "ai",
            },),
            "modules": ({
                "id": MODULE, "study_program_id": PROGRAM, "name": "Safety", "slug": "safety",
            },),
            "topics": ({
                "id": TOPIC, "module_id": MODULE, "name": "Authority", "slug": "authority",
            },),
            "questions": ({
                "id": QUESTION, "topic_id": TOPIC, "text": "Who authorizes?", "difficulty": 3,
            },),
            "task_catalog_items": ({
                "id": TASK, "topic_id": TOPIC, "source_question_id": QUESTION,
                "format": "definition", "prompt": "Name authority",
                "expected_answer": "OpenFang", "payload": {}, "difficulty": 3,
            },),
        },
        artifact_root=artifact_root,
        artifact_paths=artifact_paths,
    )


def _prepared(tmp_path: Path, snapshot: LegacySnapshot | None = None):
    export_dir = tmp_path / "export"
    LegacyExporter().export(snapshot or _snapshot(), export_dir, batch_id="dry-run-1")
    _, records = load_export(export_dir)
    return export_dir, transform_legacy_records(records)


def test_dry_run_validates_counts_hashes_foreign_keys_samples_and_zero_writes(
    tmp_path: Path,
) -> None:
    export_dir, transformed = _prepared(tmp_path)

    report = validate_dry_run(export_dir, transformed)
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    write_validation_report(report, json_path=json_path, markdown_path=markdown_path)

    assert report.ready is True
    assert report.destructive_operations == 0
    assert report.blockers == ()
    assert report.export_record_count == 6
    assert report.import_envelope_count == 7
    assert report.sample_hashes
    assert all(check.status == "passed" for check in report.checks)
    assert json.loads(json_path.read_text(encoding="utf-8"))["ready"] is True
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Dry Run: READY" in markdown
    assert "Destructive operations: **0**" in markdown


def test_dry_run_blocks_tampered_export_and_missing_canonical_parent(tmp_path: Path) -> None:
    export_dir, transformed = _prepared(tmp_path)
    records_path = export_dir / "records.jsonl"
    records_path.write_bytes(records_path.read_bytes() + b"\n")
    course = next(item for item in transformed.envelopes if item.record_type == "course")
    broken = transformed.model_copy(update={
        "envelopes": tuple(
            item.model_copy(update={"payload": {**item.payload, "program_id": "missing"}})
            if item == course else item
            for item in transformed.envelopes
        )
    })

    report = validate_dry_run(export_dir, broken)

    assert report.ready is False
    assert "export_records_hash_mismatch" in report.blockers
    assert "envelope_hash_mismatch" in report.blockers
    assert "foreign_key_missing" in report.blockers
    assert report.destructive_operations == 0


def test_dry_run_blocks_invalid_scores_quarantine_and_missing_artifacts(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    artifact = artifact_root / "source.txt"
    artifact.write_text("source", encoding="utf-8")
    export_dir, transformed = _prepared(
        tmp_path,
        _snapshot(artifact_root=artifact_root, artifact_paths=(Path("source.txt"),)),
    )
    artifact.unlink()
    adaptive_item = next(
        item for item in transformed.envelopes if item.record_type == "adaptive_item"
    )
    invalid = adaptive_item.model_copy(update={
        "record_type": "evaluation",
        "payload": {"score": 1.5, "confidence": 1.0},
    })
    transformed = transformed.model_copy(update={
        "envelopes": (*transformed.envelopes, invalid),
    })

    report = validate_dry_run(
        export_dir,
        transformed,
        legacy_artifact_root=artifact_root,
    )

    assert report.ready is False
    assert "invalid_score_range" in report.blockers
    assert "artifact_missing" in report.blockers
    assert "quarantined_records" in report.blockers
    assert report.quarantine_count == 1
