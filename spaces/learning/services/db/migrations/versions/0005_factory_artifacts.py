"""Persist restart-safe Course Factory requests and full stage outputs."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_factory_artifacts"
down_revision = "0004_course_factory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("learning_course_factory_attempts") as batch:
        batch.add_column(sa.Column("request_json", sa.JSON(), nullable=True))
    with op.batch_alter_table("learning_course_factory_stage_artifacts") as batch:
        batch.add_column(
            sa.Column(
                "output_artifact_id",
                sa.String(36),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_learning_factory_stage_output_artifact",
            "learning_artifacts",
            ["output_artifact_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_learning_factory_stage_output_artifact", ["output_artifact_id"]
        )
    if op.get_bind().dialect.name == "postgresql":
        _replace_postgresql_attempt_guard()


def _replace_postgresql_attempt_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION learning_guard_factory_attempt()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR NEW.id IS DISTINCT FROM OLD.id
               OR NEW.job_id IS DISTINCT FROM OLD.job_id
               OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
               OR NEW.retry_of_attempt_id IS DISTINCT FROM OLD.retry_of_attempt_id
               OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
               OR NEW.request_json::text IS DISTINCT FROM OLD.request_json::text
               OR NEW.provenance::text IS DISTINCT FROM OLD.provenance::text
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR OLD.terminal_state IS NOT NULL
               OR NEW.terminal_state NOT IN ('published','failed','cancelled','rejected')
               OR NEW.terminal_at IS NULL THEN
                RAISE EXCEPTION 'course factory attempts are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM learning_course_factory_stage_artifacts "
            "WHERE output_artifact_id IS NOT NULL"
        )
    ).scalar_one()
    if count:
        raise RuntimeError("cannot downgrade persisted Course Factory outputs")
    with op.batch_alter_table("learning_course_factory_stage_artifacts") as batch:
        batch.drop_constraint(
            "uq_learning_factory_stage_output_artifact", type_="unique"
        )
        batch.drop_constraint(
            "fk_learning_factory_stage_output_artifact", type_="foreignkey"
        )
        batch.drop_column("output_artifact_id")
    with op.batch_alter_table("learning_course_factory_attempts") as batch:
        batch.drop_column("request_json")
