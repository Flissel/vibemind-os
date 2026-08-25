from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from spaces.learning.services.db.models import Base
from spaces.learning.services.db.session import create_learning_engine
from spaces.learning.services.ingestion import models as ingestion_models  # noqa: F401


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "services" / "db" / "migrations"
VERSION_TABLE = "learning_alembic_version"
PHASE_ONE_REVISION = "0001_learning_core"
RECEIPT_CLAIMS_REVISION = "0002_learning_receipt_claims"
LEGACY_CORE_TABLES = {
    "learning_invocation_receipts",
    "learning_aggregate_revisions",
    "learning_outbox",
    "learning_artifacts",
    "learning_terminal_evidence",
}


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", os.fspath(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _adopt_complete_legacy_schema(database_url: str, config: Config) -> bool:
    engine = create_learning_engine(database_url)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        receipt_columns = (
            {
                column["name"]
                for column in inspector.get_columns("learning_invocation_receipts")
            }
            if "learning_invocation_receipts" in table_names
            else set()
        )
    finally:
        engine.dispose()

    if VERSION_TABLE in table_names:
        return False

    owned_tables = set(Base.metadata.tables)
    existing_owned_tables = owned_tables & table_names
    if not existing_owned_tables:
        return False
    missing_legacy_tables = LEGACY_CORE_TABLES - existing_owned_tables
    if missing_legacy_tables:
        missing = ", ".join(sorted(missing_legacy_tables))
        raise RuntimeError(
            f"partial pre-Alembic Learning schema; missing owned tables: {missing}"
        )

    extension_tables = owned_tables - LEGACY_CORE_TABLES
    existing_extension_tables = extension_tables & existing_owned_tables
    if existing_extension_tables:
        raise RuntimeError(
            "unversioned extension tables cannot prove migration-owned constraints"
        )

    if "terminal" not in receipt_columns:
        command.stamp(config, PHASE_ONE_REVISION)
        return False

    command.stamp(config, RECEIPT_CLAIMS_REVISION)
    return False


def migrate(database_url: str) -> None:
    config = _config(database_url)
    if _adopt_complete_legacy_schema(database_url, config):
        return
    command.upgrade(config, "head")


def main() -> int:
    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("LEARNING_DATABASE_URL is required")
    migrate(database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
