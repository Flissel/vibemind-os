from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from spaces.learning.services.db.models import Base
from spaces.learning.services.db.session import create_learning_engine


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "services" / "db" / "migrations"
VERSION_TABLE = "learning_alembic_version"
PHASE_ONE_REVISION = "0001_learning_core"


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
    if existing_owned_tables != owned_tables:
        missing = ", ".join(sorted(owned_tables - existing_owned_tables))
        raise RuntimeError(
            f"partial pre-Alembic Learning schema; missing owned tables: {missing}"
        )

    if "terminal" not in receipt_columns:
        command.stamp(config, PHASE_ONE_REVISION)
        return False

    command.stamp(config, "head")
    return True


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
