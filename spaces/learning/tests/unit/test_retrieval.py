from __future__ import annotations

from dataclasses import dataclass

import pytest

from spaces.learning.services.ingestion.qdrant_index import QdrantHit
from spaces.learning.services.ingestion.retrieval import (
    GroundedRetrievalUnavailable,
    GroundedRetriever,
)


@dataclass
class FakeEmbedder:
    vectors: list[list[float]]

    def embed(self, texts: list[str]) -> list[list[float]]:
        assert texts == ["What requires review?"]
        return self.vectors


class FakeIndex:
    def __init__(self, hits: tuple[QdrantHit, ...]) -> None:
        self._hits = hits

    def search(
        self, vector: list[float], *, course_id: str, limit: int
    ) -> tuple[QdrantHit, ...]:
        assert vector == [1.0, 0.0]
        assert course_id == "course-1"
        assert limit == 3
        return self._hits


class UnavailableIndex(FakeIndex):
    def search(
        self, vector: list[float], *, course_id: str, limit: int
    ) -> tuple[QdrantHit, ...]:
        del vector, course_id, limit
        raise RuntimeError("qdrant unavailable")


def test_retrieval_returns_grounded_source_revision_and_locator() -> None:
    hit = QdrantHit(
        point_id="chunk-1",
        score=0.91,
        payload={
            "chunk_id": "chunk-1",
            "course_id": "course-1",
            "source_id": "source-1",
            "source_revision": 2,
            "content": "Human review is required.",
            "content_hash": "a" * 64,
            "locator": {"page": 3},
            "metadata": {"kind": "page"},
            "ingestion_spec_version": "ingestion-v1",
            "tombstone": False,
        },
    )
    retriever = GroundedRetriever(FakeEmbedder([[1.0, 0.0]]), FakeIndex((hit,)))

    results = retriever.retrieve(
        "What requires review?", course_id="course-1", limit=3
    )

    assert results[0].content == "Human review is required."
    assert results[0].source_id == "source-1"
    assert results[0].source_revision == 2
    assert results[0].locator == {"page": 3}


def test_retrieval_fails_closed_when_citation_payload_is_incomplete() -> None:
    hit = QdrantHit(
        point_id="chunk-1",
        score=0.91,
        payload={
            "chunk_id": "chunk-1",
            "course_id": "course-1",
            "source_id": "source-1",
            "source_revision": 2,
            "content": "Unsupported citation.",
            "content_hash": "a" * 64,
            "metadata": {},
            "ingestion_spec_version": "ingestion-v1",
            "tombstone": False,
        },
    )
    retriever = GroundedRetriever(FakeEmbedder([[1.0, 0.0]]), FakeIndex((hit,)))

    with pytest.raises(GroundedRetrievalUnavailable, match="citation"):
        retriever.retrieve("What requires review?", course_id="course-1", limit=3)


def test_retrieval_fails_closed_when_qdrant_is_unavailable() -> None:
    retriever = GroundedRetriever(
        FakeEmbedder([[1.0, 0.0]]), UnavailableIndex(())
    )

    with pytest.raises(GroundedRetrievalUnavailable, match="unavailable"):
        retriever.retrieve("What requires review?", course_id="course-1", limit=3)
