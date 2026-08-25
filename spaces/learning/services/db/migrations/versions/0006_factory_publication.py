"""Persist immutable Course Factory delivery and publication evidence."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_factory_publication"
down_revision = "0005_factory_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _create_draft_deliveries()
    _create_review_decisions()
    _create_publications()
    if op.get_bind().dialect.name == "postgresql":
        _create_postgresql_guards()


def _create_draft_deliveries() -> None:
    op.create_table(
        "learning_course_factory_draft_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("draft_hash", sa.String(64), nullable=False),
        sa.Column("learnhouse_revision", sa.Integer(), nullable=False),
        sa.Column("evidence_ref", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id", "attempt_id"],
            [
                "learning_course_factory_attempts.job_id",
                "learning_course_factory_attempts.id",
            ],
        ),
        sa.UniqueConstraint("job_id", "attempt_number"),
        sa.UniqueConstraint("job_id", "id"),
        sa.CheckConstraint("attempt_number >= 1"),
        sa.CheckConstraint("learnhouse_revision >= 1"),
    )


def _create_review_decisions() -> None:
    op.create_table(
        "learning_course_factory_review_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("confirmation_ref", sa.String(128), nullable=False),
        sa.Column("draft_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id", "attempt_id"],
            [
                "learning_course_factory_attempts.job_id",
                "learning_course_factory_attempts.id",
            ],
        ),
        sa.UniqueConstraint("job_id", "attempt_number"),
        sa.CheckConstraint("attempt_number >= 1"),
        sa.CheckConstraint("decision IN ('approved','rejected')"),
    )


def _create_publications() -> None:
    op.create_table(
        "learning_course_factory_publications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("draft_hash", sa.String(64), nullable=False),
        sa.Column("learnhouse_revision", sa.Integer(), nullable=False),
        sa.Column("confirmation_ref", sa.String(128), nullable=False),
        sa.Column("readback_evidence_ref", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id", "attempt_id"],
            [
                "learning_course_factory_attempts.job_id",
                "learning_course_factory_attempts.id",
            ],
        ),
        sa.UniqueConstraint("job_id"),
        sa.UniqueConstraint("job_id", "attempt_number"),
        sa.CheckConstraint("attempt_number >= 1"),
        sa.CheckConstraint("learnhouse_revision >= 1"),
    )


def _create_postgresql_guards() -> None:
    for table_name in (
        "learning_course_factory_draft_deliveries",
        "learning_course_factory_review_decisions",
        "learning_course_factory_publications",
    ):
        function_name = f"learning_reject_{table_name}_mutation"
        trigger_name = f"{table_name}_append_only"
        op.execute(
            f"""
            CREATE FUNCTION {function_name}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION '{table_name} is append-only';
            END;
            $$
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION {function_name}()
            """
        )


def downgrade() -> None:
    tables = (
        "learning_course_factory_publications",
        "learning_course_factory_review_decisions",
        "learning_course_factory_draft_deliveries",
    )
    for table_name in tables:
        count = op.get_bind().execute(
            sa.text(f"SELECT COUNT(*) FROM {table_name}")
        ).scalar_one()
        if count:
            raise RuntimeError("cannot downgrade persisted Course Factory publication data")
    if op.get_bind().dialect.name == "postgresql":
        for table_name in tables:
            op.execute(f"DROP TRIGGER {table_name}_append_only ON {table_name}")
            op.execute(f"DROP FUNCTION learning_reject_{table_name}_mutation()")
    for table_name in tables:
        op.drop_table(table_name)
