"""Create traceable adaptive learning evidence tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_adaptive_learning"
down_revision = "0006_factory_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _create_catalog()
    _create_sessions()
    _create_evidence()
    if op.get_bind().dialect.name == "postgresql":
        _create_postgresql_guards()


def _create_catalog() -> None:
    op.create_table(
        "learning_adaptive_concepts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("course_revision", sa.Integer(), nullable=False),
        sa.Column("concept_key", sa.String(200), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("course_id", "course_revision", "concept_key"),
        sa.UniqueConstraint("id", "course_id", "course_revision"),
        sa.CheckConstraint("course_revision >= 1"),
    )
    op.create_index(
        "ix_learning_adaptive_concepts_course_id",
        "learning_adaptive_concepts",
        ["course_id"],
    )
    op.create_table(
        "learning_adaptive_concept_dependencies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("course_revision", sa.Integer(), nullable=False),
        sa.Column("prerequisite_concept_id", sa.String(36), nullable=False),
        sa.Column("dependent_concept_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["prerequisite_concept_id", "course_id", "course_revision"],
            [
                "learning_adaptive_concepts.id",
                "learning_adaptive_concepts.course_id",
                "learning_adaptive_concepts.course_revision",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["dependent_concept_id", "course_id", "course_revision"],
            [
                "learning_adaptive_concepts.id",
                "learning_adaptive_concepts.course_id",
                "learning_adaptive_concepts.course_revision",
            ],
        ),
        sa.UniqueConstraint(
            "course_id",
            "course_revision",
            "prerequisite_concept_id",
            "dependent_concept_id",
        ),
        sa.CheckConstraint("course_revision >= 1"),
        sa.CheckConstraint("prerequisite_concept_id <> dependent_concept_id"),
    )
    op.create_table(
        "learning_adaptive_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("course_revision", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.String(200), nullable=False),
        sa.Column("item_type", sa.String(32), nullable=False),
        sa.Column("difficulty", sa.Float(), nullable=False),
        sa.Column("quality_status", sa.String(16), nullable=False),
        sa.Column("expected_answer", sa.JSON(), nullable=False),
        sa.Column("scoring_config", sa.JSON(), nullable=False),
        sa.Column("rubric", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("course_id", "course_revision", "activity_id"),
        sa.UniqueConstraint("id", "course_id", "course_revision"),
        sa.CheckConstraint("course_revision >= 1"),
        sa.CheckConstraint("difficulty >= 0 AND difficulty <= 1"),
        sa.CheckConstraint(
            "item_type IN ('single_choice','multiple_choice','matching','ordering',"
            "'numeric','fill_in','open','case','code','penecho_canvas')"
        ),
        sa.CheckConstraint("quality_status IN ('approved','review','rejected')"),
    )
    op.create_index(
        "ix_learning_adaptive_items_course_id",
        "learning_adaptive_items",
        ["course_id"],
    )
    op.create_table(
        "learning_adaptive_item_concepts",
        sa.Column("item_id", sa.String(36), primary_key=True),
        sa.Column("concept_id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("course_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id", "course_id", "course_revision"],
            [
                "learning_adaptive_items.id",
                "learning_adaptive_items.course_id",
                "learning_adaptive_items.course_revision",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["concept_id", "course_id", "course_revision"],
            [
                "learning_adaptive_concepts.id",
                "learning_adaptive_concepts.course_id",
                "learning_adaptive_concepts.course_revision",
            ],
        ),
        sa.CheckConstraint("course_revision >= 1"),
    )


def _create_sessions() -> None:
    op.create_table(
        "learning_adaptive_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("course_revision", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("blueprint", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "course_id", "course_revision"),
        sa.CheckConstraint("course_revision >= 1"),
        sa.CheckConstraint("revision >= 1"),
        sa.CheckConstraint("mode IN ('training','exam')"),
        sa.CheckConstraint("state IN ('active','completed','cancelled')"),
    )
    op.create_index(
        "ix_learning_adaptive_sessions_course_id",
        "learning_adaptive_sessions",
        ["course_id"],
    )
    op.create_index(
        "ix_learning_adaptive_sessions_actor_id",
        "learning_adaptive_sessions",
        ["actor_id"],
    )
    op.create_table(
        "learning_adaptive_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("course_revision", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("selection_reason", sa.JSON(), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "course_id", "course_revision"],
            [
                "learning_adaptive_sessions.id",
                "learning_adaptive_sessions.course_id",
                "learning_adaptive_sessions.course_revision",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "course_id", "course_revision"],
            [
                "learning_adaptive_items.id",
                "learning_adaptive_items.course_id",
                "learning_adaptive_items.course_revision",
            ],
        ),
        sa.UniqueConstraint("session_id", "ordinal"),
        sa.CheckConstraint("ordinal >= 1"),
        sa.CheckConstraint("course_revision >= 1"),
    )
    op.create_index(
        "ix_learning_adaptive_attempts_session_id",
        "learning_adaptive_attempts",
        ["session_id"],
    )
    op.create_table(
        "learning_adaptive_responses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_attempts.id"),
            nullable=False,
        ),
        sa.Column("response_revision", sa.Integer(), nullable=False),
        sa.Column("answer", sa.JSON(), nullable=False),
        sa.Column("answer_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("attempt_id", "response_revision"),
        sa.CheckConstraint("response_revision >= 1"),
    )


def _create_evidence() -> None:
    op.create_table(
        "learning_adaptive_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "response_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_responses.id"),
            nullable=False,
        ),
        sa.Column("evaluator_type", sa.String(24), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("rationale_codes", sa.JSON(), nullable=False),
        sa.Column("evaluator_versions", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("response_id"),
        sa.CheckConstraint("score >= 0 AND score <= 1"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1"),
        sa.CheckConstraint("evaluator_type IN ('deterministic','rubric','manual')"),
    )
    op.create_table(
        "learning_adaptive_mastery",
        sa.Column("actor_id", sa.String(128), primary_key=True),
        sa.Column(
            "concept_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_concepts.id"),
            primary_key=True,
        ),
        sa.Column("alpha", sa.Float(), nullable=False),
        sa.Column("beta", sa.Float(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("alpha > 0"),
        sa.CheckConstraint("beta > 0"),
        sa.CheckConstraint("revision >= 1"),
    )
    op.create_table(
        "learning_adaptive_mastery_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "evaluation_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_evaluations.id"),
            nullable=False,
        ),
        sa.Column(
            "concept_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_concepts.id"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("applied_weight", sa.Float(), nullable=False),
        sa.Column("applied_score", sa.Float(), nullable=False),
        sa.Column("alpha_before", sa.Float(), nullable=False),
        sa.Column("beta_before", sa.Float(), nullable=False),
        sa.Column("alpha_after", sa.Float(), nullable=False),
        sa.Column("beta_after", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("evaluation_id", "concept_id"),
        sa.CheckConstraint("applied_weight > 0 AND applied_weight <= 1"),
        sa.CheckConstraint("applied_score >= 0 AND applied_score <= 1"),
        sa.CheckConstraint("alpha_before > 0 AND beta_before > 0"),
        sa.CheckConstraint("alpha_after > 0 AND beta_after > 0"),
    )
    op.create_table(
        "learning_adaptive_review_schedules",
        sa.Column("actor_id", sa.String(128), primary_key=True),
        sa.Column(
            "concept_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_concepts.id"),
            primary_key=True,
        ),
        sa.Column("interval_index", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("maintenance", sa.Boolean(), nullable=False),
        sa.Column(
            "last_evaluation_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_evaluations.id"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("interval_index >= 0 AND interval_index <= 4"),
        sa.CheckConstraint("revision >= 1"),
    )
    op.create_table(
        "learning_adaptive_misconceptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column(
            "concept_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_concepts.id"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "first_evaluation_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_evaluations.id"),
            nullable=False,
        ),
        sa.Column(
            "latest_evaluation_id",
            sa.String(36),
            sa.ForeignKey("learning_adaptive_evaluations.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("actor_id", "concept_id", "tag"),
        sa.CheckConstraint("status IN ('unresolved','resolved')"),
    )
    op.create_index(
        "ix_learning_adaptive_misconceptions_actor_id",
        "learning_adaptive_misconceptions",
        ["actor_id"],
    )


_APPEND_ONLY_TABLES = (
    "learning_adaptive_concepts",
    "learning_adaptive_concept_dependencies",
    "learning_adaptive_items",
    "learning_adaptive_item_concepts",
    "learning_adaptive_attempts",
    "learning_adaptive_responses",
    "learning_adaptive_evaluations",
    "learning_adaptive_mastery_evidence",
)


def _create_postgresql_guards() -> None:
    for table_name in _APPEND_ONLY_TABLES:
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
    op.execute(
        """
        CREATE FUNCTION learning_require_accepted_mastery_evaluation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM learning_adaptive_evaluations
                WHERE id = NEW.evaluation_id AND accepted IS TRUE
            ) THEN
                RAISE EXCEPTION 'mastery evidence requires an accepted evaluation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER learning_adaptive_mastery_evidence_acceptance
        BEFORE INSERT ON learning_adaptive_mastery_evidence
        FOR EACH ROW EXECUTE FUNCTION learning_require_accepted_mastery_evaluation()
        """
    )


def downgrade() -> None:
    tables = (
        "learning_adaptive_misconceptions",
        "learning_adaptive_review_schedules",
        "learning_adaptive_mastery_evidence",
        "learning_adaptive_mastery",
        "learning_adaptive_evaluations",
        "learning_adaptive_responses",
        "learning_adaptive_attempts",
        "learning_adaptive_sessions",
        "learning_adaptive_item_concepts",
        "learning_adaptive_items",
        "learning_adaptive_concept_dependencies",
        "learning_adaptive_concepts",
    )
    for table_name in tables:
        count = op.get_bind().execute(
            sa.text(f"SELECT COUNT(*) FROM {table_name}")
        ).scalar_one()
        if count:
            raise RuntimeError("cannot downgrade persisted adaptive learning data")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER learning_adaptive_mastery_evidence_acceptance "
            "ON learning_adaptive_mastery_evidence"
        )
        op.execute("DROP FUNCTION learning_require_accepted_mastery_evaluation()")
        for table_name in reversed(_APPEND_ONLY_TABLES):
            op.execute(f"DROP TRIGGER {table_name}_append_only ON {table_name}")
            op.execute(f"DROP FUNCTION learning_reject_{table_name}_mutation()")
    for table_name in tables:
        op.drop_table(table_name)
