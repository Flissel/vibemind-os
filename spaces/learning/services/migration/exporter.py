from __future__ import annotations

import hashlib
import json
import math
import mimetypes
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import MetaData, Table, create_engine, inspect, select

from spaces.learning.services.migration.legacy_schema import (
    LEGACY_EXPORT_COLUMNS,
    MISTAKE_JOURNAL_COLUMNS,
    LegacyExportManifest,
    LegacyExportRecord,
    LegacySnapshot,
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


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("legacy export contains a non-finite number")
        return value
    if isinstance(value, Decimal):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("legacy export contains a non-finite decimal")
        return number
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("legacy export object keys must be strings")
        return {key: _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ValueError(f"legacy export contains unsupported value type: {type(value).__name__}")


def _payload(row: Mapping[str, object], columns: tuple[str, ...]) -> dict[str, object]:
    return {
        column: _json_value(row[column])
        for column in columns
        if column in row
    }


def _record(
    *,
    entity: str,
    source_id: str,
    payload: dict[str, object],
    batch_id: str,
) -> LegacyExportRecord:
    content_hash = hashlib.sha256(_canonical(payload)).hexdigest()
    return LegacyExportRecord(
        migration_batch_id=batch_id,
        entity=entity,
        source_id=source_id,
        content_hash=content_hash,
        payload=payload,
    )


class LegacyExporter:
    def export(
        self,
        snapshot: LegacySnapshot,
        output_dir: Path,
        *,
        batch_id: str,
    ) -> LegacyExportManifest:
        records: list[LegacyExportRecord] = []
        inventory = {
            table: len(rows)
            for table, rows in sorted(snapshot.tables.items())
        }
        for entity, columns in LEGACY_EXPORT_COLUMNS.items():
            rows = snapshot.tables.get(entity, ())
            for row in rows:
                payload = _payload(row, columns)
                source_id = payload.get("id")
                if not isinstance(source_id, str) or not source_id:
                    raise ValueError(f"legacy {entity} row has no stable source ID")
                records.append(_record(
                    entity=entity,
                    source_id=source_id,
                    payload=payload,
                    batch_id=batch_id,
                ))
        records.extend(self._artifact_records(snapshot, batch_id=batch_id))
        for row in snapshot.mistake_journal:
            payload = _payload(row, MISTAKE_JOURNAL_COLUMNS)
            source_id = payload.get("id")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError("legacy mistake journal row has no stable source ID")
            records.append(_record(
                entity="mistake_journal",
                source_id=source_id,
                payload=payload,
                batch_id=batch_id,
            ))

        records.sort(key=lambda item: (item.entity, item.source_id, item.content_hash))
        encoded_records = b"".join(
            _canonical(record.model_dump(mode="json")) + b"\n"
            for record in records
        )
        counts = dict(sorted(Counter(record.entity for record in records).items()))
        manifest = LegacyExportManifest(
            batch_id=batch_id,
            record_count=len(records),
            counts=counts,
            records_sha256=hashlib.sha256(encoded_records).hexdigest(),
            table_inventory=inventory,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "records.jsonl").write_bytes(encoded_records)
        (output_dir / "manifest.json").write_bytes(
            _canonical(manifest.model_dump(mode="json")) + b"\n"
        )
        return manifest

    @staticmethod
    def _artifact_records(
        snapshot: LegacySnapshot,
        *,
        batch_id: str,
    ) -> list[LegacyExportRecord]:
        if not snapshot.artifact_paths:
            return []
        if snapshot.artifact_root is None:
            raise ValueError("legacy artifact root is required")
        root = snapshot.artifact_root.resolve(strict=True)
        records: list[LegacyExportRecord] = []
        for relative in snapshot.artifact_paths:
            if relative.is_absolute():
                raise ValueError("legacy artifact path must be relative")
            unresolved = root / relative
            if unresolved.is_symlink():
                raise ValueError("legacy artifact path may not be a symlink")
            try:
                path = unresolved.resolve(strict=True)
                path.relative_to(root)
            except (OSError, ValueError) as error:
                raise ValueError("legacy artifact path escapes the declared root") from error
            if not path.is_file():
                raise ValueError("legacy artifact path must identify a file")
            normalized = path.relative_to(root).as_posix()
            size_bytes = path.stat().st_size
            payload = {
                "relative_path": normalized,
                "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "size_bytes": size_bytes,
                "sha256": _file_sha256(path),
                "trusted_vector_data": False,
            }
            records.append(_record(
                entity="artifacts",
                source_id=f"artifact:{normalized}",
                payload=payload,
                batch_id=batch_id,
            ))
        return records


def load_sql_snapshot(
    database_url: str,
    *,
    artifact_root: Path | None = None,
    artifact_paths: tuple[Path, ...] = (),
    mistake_journal: tuple[Mapping[str, object], ...] = (),
) -> LegacySnapshot:
    """Read admitted legacy tables in a transaction that is read-only on PostgreSQL."""

    engine = create_engine(database_url)
    tables: dict[str, tuple[dict[str, object], ...]] = {}
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if connection.dialect.name == "postgresql":
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                available = set(inspect(connection).get_table_names())
                metadata = MetaData()
                for name in LEGACY_EXPORT_COLUMNS:
                    if name not in available:
                        continue
                    table = Table(name, metadata, autoload_with=connection)
                    ordering = table.c.id if "id" in table.c else next(iter(table.c))
                    rows = connection.execute(select(table).order_by(ordering)).mappings()
                    tables[name] = tuple(dict(row) for row in rows)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    return LegacySnapshot(
        tables=tables,
        artifact_root=artifact_root,
        artifact_paths=artifact_paths,
        mistake_journal=mistake_journal,
    )
