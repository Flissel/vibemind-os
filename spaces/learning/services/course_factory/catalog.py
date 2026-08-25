from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select

from spaces.learning.services.course_factory.repository import (
    SessionFactory,
    SourceProvenance,
)
from spaces.learning.services.course_factory.verification import (
    ChunkEvidence,
    SourceSnapshot,
)
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceChunk,
    LearningSourceRevision,
)


_MAX_SOURCE_CONTEXT_BYTES = 450_000


@dataclass(frozen=True)
class SourceCatalog:
    snapshots: tuple[SourceSnapshot, ...]
    model_refs: tuple[str, ...]


class SourceCatalogLoader:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def load(
        self,
        *,
        course_id: str,
        provenance: tuple[SourceProvenance, ...],
    ) -> SourceCatalog:
        snapshots: list[SourceSnapshot] = []
        refs: list[str] = []
        with self._session_factory() as session:
            for expected in sorted(provenance, key=lambda item: item.source_id):
                source = session.get(LearningSource, expected.source_id)
                revision = session.get(
                    LearningSourceRevision,
                    (expected.source_id, expected.revision),
                )
                if (
                    source is None
                    or source.course_id != course_id
                    or source.current_revision != expected.revision
                    or revision is None
                    or revision.status not in {"stored", "indexed"}
                    or revision.content_hash != expected.content_hash
                ):
                    raise ValueError("course factory source provenance is stale")
                rows = session.scalars(
                    select(LearningSourceChunk)
                    .where(
                        LearningSourceChunk.source_id == expected.source_id,
                        LearningSourceChunk.source_revision == expected.revision,
                    )
                    .order_by(LearningSourceChunk.ordinal)
                ).all()
                if not rows:
                    raise ValueError("course factory source has no grounded chunks")
                chunks = tuple(
                    ChunkEvidence(
                        chunk_id=row.id,
                        content_hash=row.content_hash,
                        locator=row.locator,
                    )
                    for row in rows
                )
                snapshots.append(
                    SourceSnapshot(
                        source_id=expected.source_id,
                        revision=expected.revision,
                        content_hash=expected.content_hash,
                        chunks=chunks,
                    )
                )
                refs.extend(
                    _canonical_json(
                        {
                            "source_id": expected.source_id,
                            "source_revision": expected.revision,
                            "source_content_hash": expected.content_hash,
                            "chunk_id": row.id,
                            "chunk_content_hash": row.content_hash,
                            "locator": row.locator,
                            "content": row.content,
                        }
                    )
                    for row in rows
                )
        if sum(len(ref.encode("utf-8")) for ref in refs) > _MAX_SOURCE_CONTEXT_BYTES:
            raise ValueError("course factory source context exceeds its bound")
        return SourceCatalog(snapshots=tuple(snapshots), model_refs=tuple(refs))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
