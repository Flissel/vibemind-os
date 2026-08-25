from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ValidationCheck(_StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    status: Literal["passed", "failed", "warning"]
    details: dict[str, JsonValue] = Field(default_factory=dict)


class MigrationValidationReport(_StrictModel):
    version: Literal["learning-migration-dry-run-v1"] = "learning-migration-dry-run-v1"
    source_system: Literal["learning_plattform_v1"] = "learning_plattform_v1"
    migration_batch_id: str
    ready: bool
    destructive_operations: Literal[0] = 0
    export_record_count: int = Field(ge=0)
    import_envelope_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    quarantine_count: int = Field(ge=0)
    source_counts: dict[str, int]
    import_counts: dict[str, int]
    sample_hashes: dict[str, str]
    checks: tuple[ValidationCheck, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    non_claims: tuple[str, ...] = (
        "real_import_not_executed",
        "qdrant_rebuild_not_executed",
        "route_cutover_not_executed",
        "legacy_data_not_modified",
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"


def write_validation_report(
    report: MigrationValidationReport,
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_bytes(_canonical_json(report.model_dump(mode="json")))
    state = "READY" if report.ready else "BLOCKED"
    lines = [
        f"# Learning Migration Dry Run: {state}",
        "",
        f"- Source system: `{report.source_system}`",
        f"- Migration batch: `{report.migration_batch_id}`",
        f"- Export records: **{report.export_record_count}**",
        f"- Canonical envelopes: **{report.import_envelope_count}**",
        f"- Warnings: **{report.warning_count}**",
        f"- Quarantines: **{report.quarantine_count}**",
        f"- Destructive operations: **{report.destructive_operations}**",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    lines.extend(f"| `{check.name}` | {check.status} |" for check in report.checks)
    lines.extend(["", "## Counts", "", "### Legacy export", ""])
    lines.extend(
        f"- `{entity}`: {count}" for entity, count in report.source_counts.items()
    )
    lines.extend(["", "### Canonical import", ""])
    lines.extend(
        f"- `{record_type}`: {count}"
        for record_type, count in report.import_counts.items()
    )
    lines.extend(["", "## Blockers", ""])
    lines.extend(
        [f"- `{code}`" for code in report.blockers]
        or ["- None"]
    )
    lines.extend(["", "## Warnings", ""])
    lines.extend(
        [f"- `{code}`" for code in report.warnings]
        or ["- None"]
    )
    lines.extend(["", "## Non-Claims", ""])
    lines.extend(f"- `{value}`" for value in report.non_claims)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
