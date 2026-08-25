from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from spaces.learning.services.db.models import Base
from spaces.learning.services.ingestion import models as ingestion_models  # noqa: F401
from spaces.learning.services.adaptive_engine import models as adaptive_models  # noqa: F401
from spaces.learning.services.migration import models as migration_models  # noqa: F401


config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="learning_alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="learning_alembic_version",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
