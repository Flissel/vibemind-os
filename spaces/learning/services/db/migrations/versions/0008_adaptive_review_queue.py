"""Persist adaptive evaluation review work."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_adaptive_review_queue"
down_revision = "0007_adaptive_learning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_adaptive_review_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_ref", sa.String(128), nullable=False),
        sa.Column(
            "evaluation_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_evaluations.id"),
        ),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("request_ref", "reason_code"),
        sa.CheckConstraint("status IN ('pending','resolved')"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION learning_guard_adaptive_review_item()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   OR NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.request_ref IS DISTINCT FROM OLD.request_ref
                   OR NEW.evaluation_id IS DISTINCT FROM OLD.evaluation_id
                   OR NEW.reason_code IS DISTINCT FROM OLD.reason_code
                   OR NEW.details::text IS DISTINCT FROM OLD.details::text
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR OLD.status <> 'pending'
                   OR NEW.status <> 'resolved'
                   OR NEW.resolved_at IS NULL THEN
                    RAISE EXCEPTION 'adaptive review identity is immutable';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER learning_adaptive_review_item_guard
            BEFORE UPDATE OR DELETE ON learning_adaptive_review_items
            FOR EACH ROW EXECUTE FUNCTION learning_guard_adaptive_review_item()
            """
        )


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM learning_adaptive_review_items")
    ).scalar_one()
    if count:
        raise RuntimeError("cannot downgrade persisted adaptive review work")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER learning_adaptive_review_item_guard "
            "ON learning_adaptive_review_items"
        )
        op.execute("DROP FUNCTION learning_guard_adaptive_review_item()")
    op.drop_table("learning_adaptive_review_items")
