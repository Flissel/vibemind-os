from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from spaces.learning.services.db.models import Base, utc_now


class LearningSource(Base):
    __tablename__ = "learning_sources"
    __table_args__ = (CheckConstraint("current_revision >= 0"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class LearningSourceRevision(Base):
    __tablename__ = "learning_source_revisions"
    __table_args__ = (
        CheckConstraint("revision >= 1"),
        UniqueConstraint("artifact_id"),
        UniqueConstraint("source_id", "content_hash", "ingestion_spec_version"),
    )

    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_sources.id"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_artifacts.id"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ingestion_spec_version: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class LearningSourceChunk(Base):
    __tablename__ = "learning_source_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "source_revision"],
            [
                "learning_source_revisions.source_id",
                "learning_source_revisions.revision",
            ],
        ),
        CheckConstraint("ordinal >= 0"),
        UniqueConstraint("source_id", "source_revision", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    locator_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
