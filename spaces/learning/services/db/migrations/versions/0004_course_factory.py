"""Create durable Course Factory jobs, attempts, transitions, and stage evidence."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_course_factory"
down_revision = "0003_sources_outbox"
branch_labels = None
depends_on = None


STATE_VALUES = (
    "'queued','ingesting','structuring','authoring','assessing','verifying',"
    "'quality_gate','review_ready','published','failed','cancelled','rejected'"
)
TERMINAL_VALUES = "'published','failed','cancelled','rejected'"


def upgrade() -> None:
    op.create_table(
        "learning_course_factory_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("current_attempt", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"state IN ({STATE_VALUES})"),
        sa.CheckConstraint("current_attempt >= 1"),
        sa.CheckConstraint("revision >= 1"),
        sa.CheckConstraint(
            f"(state IN ({TERMINAL_VALUES}) AND terminal_at IS NOT NULL) OR "
            f"(state NOT IN ({TERMINAL_VALUES}) AND terminal_at IS NULL)"
        ),
    )
    op.create_index(
        "ix_learning_course_factory_jobs_course_id",
        "learning_course_factory_jobs",
        ["course_id"],
    )
    op.create_table(
        "learning_course_factory_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("learning_course_factory_jobs.id"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "retry_of_attempt_id",
            sa.String(36),
            sa.ForeignKey("learning_course_factory_attempts.id"),
        ),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("terminal_state", sa.String(24)),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "attempt_number"),
        sa.UniqueConstraint("job_id", "id"),
        sa.CheckConstraint("attempt_number >= 1"),
        sa.CheckConstraint(
            f"(terminal_state IN ({TERMINAL_VALUES}) AND terminal_at IS NOT NULL) OR "
            "(terminal_state IS NULL AND terminal_at IS NULL)"
        ),
    )
    op.create_table(
        "learning_course_factory_transitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("job_revision", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(24), nullable=False),
        sa.Column("to_state", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id", "attempt_id"],
            ["learning_course_factory_attempts.job_id", "learning_course_factory_attempts.id"],
        ),
        sa.UniqueConstraint("job_id", "job_revision"),
        sa.CheckConstraint("job_revision >= 2"),
        sa.CheckConstraint(f"from_state IN ({STATE_VALUES})"),
        sa.CheckConstraint(f"to_state IN ({STATE_VALUES})"),
    )
    op.create_table(
        "learning_course_factory_stage_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id", "attempt_id"],
            ["learning_course_factory_attempts.job_id", "learning_course_factory_attempts.id"],
        ),
        sa.UniqueConstraint("attempt_id", "stage"),
        sa.CheckConstraint(
            "stage IN ('ingesting','structuring','authoring','assessing',"
            "'verifying','quality_gate')"
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        _create_postgresql_guards()


def _create_postgresql_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION learning_guard_factory_job()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE valid_transition boolean := false;
        BEGIN
            IF TG_OP = 'DELETE'
               OR NEW.id IS DISTINCT FROM OLD.id
               OR NEW.course_id IS DISTINCT FROM OLD.course_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'course factory job identity is immutable';
            END IF;
            IF NEW.revision <> OLD.revision + 1 THEN
                RAISE EXCEPTION 'course factory revision must advance exactly once';
            END IF;
            IF NEW.current_attempt = OLD.current_attempt + 1 THEN
                valid_transition := OLD.state IN ('failed','cancelled','rejected')
                    AND NEW.state = 'queued' AND NEW.terminal_at IS NULL;
            ELSIF NEW.current_attempt = OLD.current_attempt THEN
                valid_transition :=
                    (OLD.state = 'queued' AND NEW.state = 'ingesting') OR
                    (OLD.state = 'ingesting' AND NEW.state = 'structuring') OR
                    (OLD.state = 'structuring' AND NEW.state = 'authoring') OR
                    (OLD.state = 'authoring' AND NEW.state = 'assessing') OR
                    (OLD.state = 'assessing' AND NEW.state = 'verifying') OR
                    (OLD.state = 'verifying' AND NEW.state = 'quality_gate') OR
                    (OLD.state = 'quality_gate' AND NEW.state = 'review_ready') OR
                    (OLD.state = 'review_ready' AND NEW.state IN ('published','rejected')) OR
                    (OLD.state IN ('queued','ingesting','structuring','authoring',
                        'assessing','verifying','quality_gate','review_ready')
                        AND NEW.state IN ('failed','cancelled'));
            END IF;
            IF NOT valid_transition THEN
                RAISE EXCEPTION 'invalid course factory transition';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER learning_course_factory_job_guard
        BEFORE UPDATE OR DELETE ON learning_course_factory_jobs
        FOR EACH ROW EXECUTE FUNCTION learning_guard_factory_job()
        """
    )
    op.execute(
        """
        CREATE FUNCTION learning_guard_factory_attempt()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR NEW.id IS DISTINCT FROM OLD.id
               OR NEW.job_id IS DISTINCT FROM OLD.job_id
               OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
               OR NEW.retry_of_attempt_id IS DISTINCT FROM OLD.retry_of_attempt_id
               OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
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
    op.execute(
        """
        CREATE TRIGGER learning_course_factory_attempt_guard
        BEFORE UPDATE OR DELETE ON learning_course_factory_attempts
        FOR EACH ROW EXECUTE FUNCTION learning_guard_factory_attempt()
        """
    )
    for table_name in (
        "learning_course_factory_transitions",
        "learning_course_factory_stage_artifacts",
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
    count = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM learning_course_factory_jobs")
    ).scalar_one()
    if count:
        raise RuntimeError("cannot downgrade non-empty Course Factory history")
    if op.get_bind().dialect.name == "postgresql":
        for table_name in (
            "learning_course_factory_stage_artifacts",
            "learning_course_factory_transitions",
        ):
            op.execute(f"DROP TRIGGER {table_name}_append_only ON {table_name}")
            op.execute(f"DROP FUNCTION learning_reject_{table_name}_mutation()")
        op.execute(
            "DROP TRIGGER learning_course_factory_attempt_guard "
            "ON learning_course_factory_attempts"
        )
        op.execute("DROP FUNCTION learning_guard_factory_attempt()")
        op.execute(
            "DROP TRIGGER learning_course_factory_job_guard "
            "ON learning_course_factory_jobs"
        )
        op.execute("DROP FUNCTION learning_guard_factory_job()")
    op.drop_table("learning_course_factory_stage_artifacts")
    op.drop_table("learning_course_factory_transitions")
    op.drop_table("learning_course_factory_attempts")
    op.drop_index(
        "ix_learning_course_factory_jobs_course_id",
        table_name="learning_course_factory_jobs",
    )
    op.drop_table("learning_course_factory_jobs")
