"""Add durable write-ahead receipt claim state."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_learning_receipt_claims"
down_revision = "0001_learning_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "learning_invocation_receipts",
        sa.Column("terminal", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("learning_invocation_receipts", "terminal")
