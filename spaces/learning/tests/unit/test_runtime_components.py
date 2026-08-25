from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from spaces.learning.deployment import worker
from spaces.learning.deployment.migrate import migrate
from spaces.learning.deployment.runtime_api import _migration_probe
from spaces.learning.deployment.runtime_smoke import _status_request
from spaces.learning.deployment.runtime_smoke import _run as run_compose
from spaces.learning.services.db.models import Base, LearningInvocationReceipt
from spaces.learning.services.db.session import create_learning_engine


PHASE_ONE_TABLES = (
    "learning_invocation_receipts",
    "learning_aggregate_revisions",
    "learning_outbox",
    "learning_artifacts",
    "learning_terminal_evidence",
)


def test_runtime_metadata_registers_ingestion_owned_tables(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'empty-runtime.db'}"

    assert "learning_source_revisions" in Base.metadata.tables
    assert _migration_probe(database_url) is False


def test_migrate_creates_all_learning_owned_tables(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'runtime.db'}"

    migrate(database_url)

    engine = create_learning_engine(database_url)
    try:
        table_names = set(__import__("sqlalchemy").inspect(engine).get_table_names())
        assert set(Base.metadata.tables) <= table_names
        assert "learning_alembic_version" in table_names
        assert "alembic_version" not in table_names
        with engine.connect() as connection:
            revision = connection.exec_driver_sql(
                "SELECT version_num FROM learning_alembic_version"
            ).scalar_one()
        assert revision == "0006_factory_publication"
    finally:
        engine.dispose()


def test_migrate_adopts_complete_pre_alembic_learning_schema(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy-runtime.db'}"
    engine = create_learning_engine(database_url)
    try:
        for table_name in PHASE_ONE_TABLES:
            Base.metadata.tables[table_name].create(engine)
    finally:
        engine.dispose()

    migrate(database_url)

    engine = create_learning_engine(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.exec_driver_sql(
                "SELECT version_num FROM learning_alembic_version"
            ).scalar_one()
        assert revision == "0006_factory_publication"
    finally:
        engine.dispose()


def test_migrate_rejects_unversioned_extension_tables(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'unversioned-extension.db'}"
    engine = create_learning_engine(database_url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="unversioned extension"):
        migrate(database_url)


def test_migrate_upgrades_phase_one_pre_alembic_receipts_as_terminal(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'phase-one-runtime.db'}"
    engine = create_learning_engine(database_url)
    try:
        for table_name in PHASE_ONE_TABLES:
            Base.metadata.tables[table_name].create(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE learning_invocation_receipts DROP COLUMN terminal"
            )
            connection.exec_driver_sql(
                """
                INSERT INTO learning_invocation_receipts
                    (idempotency_key, request_digest, result_json, created_at)
                VALUES
                    ('legacy-key', 'legacy-digest', '{}', CURRENT_TIMESTAMP)
                """
            )
    finally:
        engine.dispose()

    migrate(database_url)

    engine = create_learning_engine(database_url)
    try:
        columns = {
            column["name"]
            for column in __import__("sqlalchemy")
            .inspect(engine)
            .get_columns("learning_invocation_receipts")
        }
        assert "terminal" in columns
        with engine.connect() as connection:
            terminal = connection.exec_driver_sql(
                "SELECT terminal FROM learning_invocation_receipts "
                "WHERE idempotency_key = 'legacy-key'"
            ).scalar_one()
            revision = connection.exec_driver_sql(
                "SELECT version_num FROM learning_alembic_version"
            ).scalar_one()
        assert terminal in {True, 1}
        assert revision == "0006_factory_publication"
    finally:
        engine.dispose()


def test_migrate_rejects_partial_pre_alembic_learning_schema(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'partial-runtime.db'}"
    engine = create_learning_engine(database_url)
    try:
        LearningInvocationReceipt.__table__.create(engine)
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="partial pre-Alembic Learning schema"):
        migrate(database_url)


def test_worker_healthcheck_requires_recent_heartbeat(
    tmp_path: Path, monkeypatch
) -> None:
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setenv("LEARNING_WORKER_HEARTBEAT", os.fspath(heartbeat))
    assert worker._healthy() is False

    heartbeat.touch()
    assert worker._healthy() is True

    old = time.time() - 60
    os.utime(heartbeat, (old, old))
    assert worker._healthy() is False


def test_runtime_smoke_uses_a_fresh_idempotency_scope_per_run() -> None:
    first = _status_request()
    second = _status_request()

    first_arguments = first["params"]["arguments"]
    second_arguments = second["params"]["arguments"]
    assert first_arguments["idempotency_key"] != second_arguments["idempotency_key"]
    assert first_arguments["invocation_id"] in first_arguments["idempotency_key"]


def test_runtime_smoke_decodes_docker_output_as_utf8(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=None, stderr=None)

    monkeypatch.setattr("subprocess.run", fake_run)

    assert run_compose(tmp_path / "compose.yml", "config") == ""
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
