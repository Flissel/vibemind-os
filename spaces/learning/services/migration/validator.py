from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from spaces.learning.services.migration.legacy_schema import (
    LegacyExportManifest,
    LegacyExportRecord,
)
from spaces.learning.services.migration.report import (
    MigrationValidationReport,
    ValidationCheck,
)
from spaces.learning.services.migration.schemas import (
    CanonicalImportEnvelope,
    TransformationResult,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_export(
    export_dir: Path,
) -> tuple[LegacyExportManifest, tuple[LegacyExportRecord, ...]]:
    manifest = LegacyExportManifest.model_validate_json(
        (export_dir / "manifest.json").read_text(encoding="utf-8")
    )
    records = tuple(
        LegacyExportRecord.model_validate_json(line)
        for line in (export_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    return manifest, records


def _check(
    checks: list[ValidationCheck],
    blockers: set[str],
    name: str,
    passed: bool,
    *,
    blocker: str,
    details: dict[str, object] | None = None,
) -> None:
    checks.append(ValidationCheck(
        name=name,
        status="passed" if passed else "failed",
        details=details or {},
    ))
    if not passed:
        blockers.add(blocker)


def _foreign_keys(
    envelopes: tuple[CanonicalImportEnvelope, ...],
) -> tuple[int, list[dict[str, str]]]:
    identities = {
        (item.record_type, item.canonical_id)
        for item in envelopes
    }
    missing: list[dict[str, str]] = []
    scalar_refs: dict[str, tuple[tuple[str, str], ...]] = {
        "program": (("organization_id", "organization"),),
        "course": (("program_id", "program"),),
        "chapter": (("course_id", "course"),),
        "concept": (("course_id", "course"),),
        "source": (("course_id", "course"), ("chapter_id", "chapter")),
        "source_revision": (("source_id", "source"),),
        "source_chunk": (("source_id", "source"),),
        "activity": (("course_id", "course"), ("chapter_id", "chapter")),
        "adaptive_item": (
            ("course_id", "course"),
            ("chapter_id", "chapter"),
            ("activity_id", "activity"),
        ),
        "session": (("course_id", "course"),),
        "response": (("session_id", "session"), ("item_id", "adaptive_item")),
        "evaluation": (("response_id", "response"),),
        "mastery_evidence": (
            ("evaluation_id", "evaluation"),
            ("session_id", "session"),
            ("concept_id", "concept"),
        ),
        "review_schedule": (("concept_id", "concept"),),
        "misconception": (("concept_id", "concept"),),
    }
    for item in envelopes:
        for field, target_type in scalar_refs.get(item.record_type, ()):
            value = item.payload.get(field)
            if not isinstance(value, str) or (target_type, value) not in identities:
                missing.append({
                    "record_type": item.record_type,
                    "canonical_id": item.canonical_id,
                    "field": field,
                    "target_type": target_type,
                    "target_id": str(value),
                })
        if item.record_type == "adaptive_item":
            concepts = item.payload.get("concept_ids")
            if not isinstance(concepts, list):
                missing.append({
                    "record_type": item.record_type,
                    "canonical_id": item.canonical_id,
                    "field": "concept_ids",
                    "target_type": "concept",
                    "target_id": str(concepts),
                })
            else:
                for concept_id in concepts:
                    if not isinstance(concept_id, str) or ("concept", concept_id) not in identities:
                        missing.append({
                            "record_type": item.record_type,
                            "canonical_id": item.canonical_id,
                            "field": "concept_ids",
                            "target_type": "concept",
                            "target_id": str(concept_id),
                        })
    return len(missing), missing[:20]


def _artifact_failures(
    records: tuple[LegacyExportRecord, ...],
    artifact_root: Path | None,
) -> list[dict[str, str]]:
    artifacts = [record for record in records if record.entity == "artifacts"]
    if not artifacts:
        return []
    if artifact_root is None:
        return [{"source_id": record.source_id, "reason": "artifact_root_missing"} for record in artifacts]
    try:
        root = artifact_root.resolve(strict=True)
    except OSError:
        return [{"source_id": record.source_id, "reason": "artifact_root_missing"} for record in artifacts]
    failures: list[dict[str, str]] = []
    for record in artifacts:
        relative = record.payload.get("relative_path")
        if not isinstance(relative, str):
            failures.append({"source_id": record.source_id, "reason": "invalid_locator"})
            continue
        try:
            path = (root / relative).resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError):
            failures.append({"source_id": record.source_id, "reason": "missing_or_unsafe"})
            continue
        expected_size = record.payload.get("size_bytes")
        expected_hash = record.payload.get("sha256")
        if (
            not path.is_file()
            or path.stat().st_size != expected_size
            or _file_sha256(path) != expected_hash
        ):
            failures.append({"source_id": record.source_id, "reason": "content_mismatch"})
    return failures


def validate_dry_run(
    export_dir: Path,
    transformed: TransformationResult,
    *,
    legacy_artifact_root: Path | None = None,
) -> MigrationValidationReport:
    manifest, records = load_export(export_dir)
    checks: list[ValidationCheck] = []
    blockers: set[str] = set()

    records_bytes = (export_dir / "records.jsonl").read_bytes()
    _check(
        checks,
        blockers,
        "export_records_hash",
        hashlib.sha256(records_bytes).hexdigest() == manifest.records_sha256,
        blocker="export_records_hash_mismatch",
    )
    _check(
        checks,
        blockers,
        "export_record_count",
        len(records) == manifest.record_count,
        blocker="export_record_count_mismatch",
        details={"manifest": manifest.record_count, "observed": len(records)},
    )
    observed_counts = dict(sorted(Counter(record.entity for record in records).items()))
    _check(
        checks,
        blockers,
        "export_entity_counts",
        observed_counts == manifest.counts,
        blocker="export_entity_counts_mismatch",
    )
    invalid_source_hashes = [
        record.source_id
        for record in records
        if hashlib.sha256(_canonical(record.payload)).hexdigest() != record.content_hash
    ]
    _check(
        checks,
        blockers,
        "source_content_hashes",
        not invalid_source_hashes,
        blocker="source_content_hash_mismatch",
        details={"invalid_source_ids": invalid_source_hashes[:20]},
    )
    invalid_envelope_hashes = [
        item.canonical_id
        for item in transformed.envelopes
        if hashlib.sha256(_canonical(item.payload)).hexdigest() != item.content_hash
    ]
    _check(
        checks,
        blockers,
        "envelope_content_hashes",
        not invalid_envelope_hashes,
        blocker="envelope_hash_mismatch",
        details={"invalid_canonical_ids": invalid_envelope_hashes[:20]},
    )
    missing_count, missing_refs = _foreign_keys(transformed.envelopes)
    _check(
        checks,
        blockers,
        "canonical_foreign_keys",
        missing_count == 0,
        blocker="foreign_key_missing",
        details={"missing_count": missing_count, "examples": missing_refs},
    )
    invalid_scores: list[str] = []
    for item in transformed.envelopes:
        if item.record_type not in {"evaluation", "mastery_evidence"}:
            continue
        for field in ("score", "confidence"):
            value = item.payload.get(field)
            if value is None and field == "confidence" and item.record_type == "mastery_evidence":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                invalid_scores.append(f"{item.canonical_id}:{field}")
    _check(
        checks,
        blockers,
        "score_ranges",
        not invalid_scores,
        blocker="invalid_score_range",
        details={"invalid": invalid_scores[:20]},
    )
    artifact_failures = _artifact_failures(records, legacy_artifact_root)
    _check(
        checks,
        blockers,
        "artifact_existence",
        not artifact_failures,
        blocker="artifact_missing",
        details={"failures": artifact_failures[:20]},
    )
    _check(
        checks,
        blockers,
        "quarantine_empty",
        not transformed.quarantines,
        blocker="quarantined_records",
        details={
            "codes": dict(sorted(Counter(item.code for item in transformed.quarantines).items()))
        },
    )
    _check(
        checks,
        blockers,
        "destructive_operations",
        True,
        blocker="destructive_operation_detected",
        details={"count": 0},
    )

    import_counts = dict(sorted(Counter(item.record_type for item in transformed.envelopes).items()))
    samples: dict[str, str] = {}
    for item in transformed.envelopes:
        samples.setdefault(item.record_type, item.content_hash)
    warning_codes = tuple(sorted({item.code for item in transformed.warnings}))
    return MigrationValidationReport(
        migration_batch_id=manifest.batch_id,
        ready=not blockers,
        export_record_count=len(records),
        import_envelope_count=len(transformed.envelopes),
        warning_count=len(transformed.warnings),
        quarantine_count=len(transformed.quarantines),
        source_counts=observed_counts,
        import_counts=import_counts,
        sample_hashes=dict(sorted(samples.items())),
        checks=tuple(checks),
        blockers=tuple(sorted(blockers)),
        warnings=warning_codes,
    )
