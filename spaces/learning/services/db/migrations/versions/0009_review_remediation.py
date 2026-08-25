"""Persist alternate-representation remediation requirements."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_review_remediation"
down_revision = "0008_adaptive_review_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("learning_adaptive_review_schedules") as batch:
        batch.add_column(
            sa.Column(
                "alternate_representation_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("learning_adaptive_review_schedules") as batch:
        batch.alter_column("alternate_representation_required", server_default=None)


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM learning_adaptive_review_schedules")
    ).scalar_one()
    if count:
        raise RuntimeError("cannot downgrade persisted adaptive review schedules")
    with op.batch_alter_table("learning_adaptive_review_schedules") as batch:
        batch.drop_column("alternate_representation_required")
