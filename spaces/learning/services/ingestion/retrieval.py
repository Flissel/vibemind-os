from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol

import httpx

from spaces.learning.services.ingestion.qdrant_index import QdrantHit


class GroundedRetrievalUnavailable(RuntimeError):
    pass


class EmbeddingGateway(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SearchIndex(Protocol):
    def search(
        self, vector: list[float], *, course_id: str, limit: int
    ) -> tuple[QdrantHit, ...]: ...


@dataclass(frozen=True)
class GroundedChunk:
    chunk_id: str
    score: float
    course_id: str
    source_id: str
    source_revision: int
    content: str
    content_hash: str
    locator: dict[str, object]
    metadata: dict[str, object]
    ingestion_spec_version: str


class OpenFangEmbeddingGateway:
    def __init__(self, base_url: str, *, timeout_seconds: float = 20.0) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("embedding service URL must use HTTP or HTTPS")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("embedding inputs must not be empty")
        try:
            response = self._client.post("/embed/batch", json={"texts": texts})
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise GroundedRetrievalUnavailable(
                "OpenFang embedding service is unavailable"
            ) from error
        vectors = response.json().get("vectors")
        if (
            not isinstance(vectors, list)
            or len(vectors) != len(texts)
            or any(not isinstance(vector, list) for vector in vectors)
        ):
            raise GroundedRetrievalUnavailable(
                "OpenFang embedding response is invalid"
            )
        normalized: list[list[float]] = []
        for vector in vectors:
            if any(
                isinstance(component, bool)
                or not isinstance(component, (int, float))
                or not math.isfinite(component)
                for component in vector
            ):
                raise GroundedRetrievalUnavailable(
                    "OpenFang embedding response is invalid"
                )
            normalized.append([float(component) for component in vector])
        return normalized


class GroundedRetriever:
    def __init__(self, embedder: EmbeddingGateway, index: SearchIndex) -> None:
        self._embedder = embedder
        self._index = index

    def retrieve(
        self, query: str, *, course_id: str, limit: int = 8
    ) -> tuple[GroundedChunk, ...]:
        if not query.strip() or not course_id or limit < 1 or limit > 20:
            raise ValueError("retrieval request is invalid")
        try:
            vectors = self._embedder.embed([query])
            if len(vectors) != 1 or not vectors[0]:
                raise GroundedRetrievalUnavailable("query embedding is unavailable")
            hits = self._index.search(vectors[0], course_id=course_id, limit=limit)
            return tuple(_grounded_chunk(hit) for hit in hits)
        except GroundedRetrievalUnavailable:
            raise
        except Exception as error:
            raise GroundedRetrievalUnavailable(
                "grounded retrieval is unavailable"
            ) from error


def _grounded_chunk(hit: QdrantHit) -> GroundedChunk:
    payload = hit.payload
    required = {
        "chunk_id",
        "course_id",
        "source_id",
        "source_revision",
        "content",
        "content_hash",
        "locator",
        "metadata",
        "ingestion_spec_version",
    }
    if (
        not required.issubset(payload)
        or not isinstance(payload.get("locator"), dict)
        or not payload["locator"]
    ):
        raise GroundedRetrievalUnavailable("citation payload is incomplete")
    if not isinstance(payload.get("metadata"), dict) or payload.get("tombstone"):
        raise GroundedRetrievalUnavailable("citation payload is invalid")
    return GroundedChunk(
        chunk_id=str(payload["chunk_id"]),
        score=hit.score,
        course_id=str(payload["course_id"]),
        source_id=str(payload["source_id"]),
        source_revision=int(payload["source_revision"]),
        content=str(payload["content"]),
        content_hash=str(payload["content_hash"]),
        locator=dict(payload["locator"]),
        metadata=dict(payload["metadata"]),
        ingestion_spec_version=str(payload["ingestion_spec_version"]),
    )
