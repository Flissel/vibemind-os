"""Create immutable Learning source revisions and deterministic chunks."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_sources_outbox"
down_revision = "0002_learning_receipt_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("current_revision >= 0"),
    )
    op.create_index("ix_learning_sources_course_id", "learning_sources", ["course_id"])
    op.create_table(
        "learning_source_revisions",
        sa.Column(
            "source_id",
            sa.String(36),
            sa.ForeignKey("learning_sources.id"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("learning_artifacts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("ingestion_spec_version", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1"),
        sa.UniqueConstraint(
            "source_id", "content_hash", "ingestion_spec_version"
        ),
    )
    op.create_table(
        "learning_source_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("locator_hash", sa.String(64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id", "source_revision"],
            ["learning_source_revisions.source_id", "learning_source_revisions.revision"],
        ),
        sa.CheckConstraint("ordinal >= 0"),
        sa.UniqueConstraint("source_id", "source_revision", "ordinal"),
    )
    op.create_index(
        "ix_learning_source_chunks_source_id", "learning_source_chunks", ["source_id"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION learning_guard_source_identity()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   OR NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.course_id IS DISTINCT FROM OLD.course_id
                   OR NEW.title IS DISTINCT FROM OLD.title
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'learning sources are append-only';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER learning_source_identity_append_only
            BEFORE UPDATE OR DELETE ON learning_sources
            FOR EACH ROW EXECUTE FUNCTION learning_guard_source_identity()
            """
        )
        op.execute(
            """
            CREATE FUNCTION learning_guard_source_revision_content()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'learning source revisions are append-only';
                END IF;
                IF NEW.source_id IS DISTINCT FROM OLD.source_id
                   OR NEW.revision IS DISTINCT FROM OLD.revision
                   OR NEW.artifact_id IS DISTINCT FROM OLD.artifact_id
                   OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
                   OR NEW.ingestion_spec_version IS DISTINCT FROM OLD.ingestion_spec_version
                   OR NEW.media_type IS DISTINCT FROM OLD.media_type
                   OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'learning source revisions are append-only';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER learning_source_revision_append_only
            BEFORE UPDATE OR DELETE ON learning_source_revisions
            FOR EACH ROW EXECUTE FUNCTION learning_guard_source_revision_content()
            """
        )
        op.execute(
            """
            CREATE FUNCTION learning_reject_source_chunk_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'learning source chunks are append-only';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER learning_source_chunk_append_only
            BEFORE UPDATE OR DELETE ON learning_source_chunks
            FOR EACH ROW EXECUTE FUNCTION learning_reject_source_chunk_mutation()
            """
        )
        op.execute(
            """
            CREATE FUNCTION learning_reject_source_artifact_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.aggregate_type = 'source'
                   OR (TG_OP <> 'DELETE' AND NEW.aggregate_type = 'source') THEN
                    RAISE EXCEPTION 'learning source artifacts are append-only';
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER learning_source_artifact_append_only
            BEFORE UPDATE OR DELETE ON learning_artifacts
            FOR EACH ROW EXECUTE FUNCTION learning_reject_source_artifact_mutation()
            """
        )


def downgrade() -> None:
    source_count = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM learning_source_revisions")
    ).scalar_one()
    if source_count:
        raise RuntimeError("cannot downgrade non-empty source history")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER learning_source_artifact_append_only ON learning_artifacts"
        )
        op.execute("DROP FUNCTION learning_reject_source_artifact_mutation()")
        op.execute(
            "DROP TRIGGER learning_source_chunk_append_only ON learning_source_chunks"
        )
        op.execute("DROP FUNCTION learning_reject_source_chunk_mutation()")
        op.execute(
            "DROP TRIGGER learning_source_revision_append_only "
            "ON learning_source_revisions"
        )
        op.execute("DROP FUNCTION learning_guard_source_revision_content()")
        op.execute(
            "DROP TRIGGER learning_source_identity_append_only ON learning_sources"
        )
        op.execute("DROP FUNCTION learning_guard_source_identity()")
    op.drop_index("ix_learning_source_chunks_source_id", table_name="learning_source_chunks")
    op.drop_table("learning_source_chunks")
    op.drop_table("learning_source_revisions")
    op.drop_index("ix_learning_sources_course_id", table_name="learning_sources")
    op.drop_table("learning_sources")
