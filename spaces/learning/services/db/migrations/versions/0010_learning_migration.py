"""Persist idempotent Learning migration batches and provenance."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_learning_migration"
down_revision = "0009_review_remediation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_migration_batches",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("learnhouse_receipt", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('applied','rolled_back')"),
    )
    op.create_table(
        "learning_migration_records",
        sa.Column(
            "batch_id",
            sa.String(128),
            sa.ForeignKey("learning_migration_batches.id"),
            primary_key=True,
        ),
        sa.Column("canonical_id", sa.String(36), primary_key=True),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("source_entity", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(512), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in ("learning_migration_records", "learning_migration_batches"):
        count = op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError("cannot downgrade persisted Learning migration data")
    op.drop_table("learning_migration_records")
    op.drop_table("learning_migration_batches")
