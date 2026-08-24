"""Create Learning-owned invocation, revision, outbox, artifact, and evidence tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_learning_core"
down_revision = None
branch_labels = ("learning",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_invocation_receipts",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "learning_aggregate_revisions",
        sa.Column("aggregate_type", sa.String(64), primary_key=True),
        sa.Column("aggregate_id", sa.String(128), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "learning_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "learning_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("relative_path", sa.String(512), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "learning_terminal_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("invocation_id", sa.String(36), nullable=False, unique=True),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("aggregate_revision", sa.Integer(), nullable=False),
        sa.Column("terminal_state", sa.String(24), nullable=False),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("learning_terminal_evidence")
    op.drop_table("learning_artifacts")
    op.drop_table("learning_outbox")
    op.drop_table("learning_aggregate_revisions")
    op.drop_table("learning_invocation_receipts")
