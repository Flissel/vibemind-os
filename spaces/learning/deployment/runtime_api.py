from __future__ import annotations

import hmac
import os
import socket
from collections.abc import Callable
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text

from spaces.learning.deployment.health import HealthService
from spaces.learning.mcp.server import handle_message
from spaces.learning.services.db.models import Base
from spaces.learning.services.db.session import create_learning_engine
from spaces.learning.services.ingestion import models as ingestion_models  # noqa: F401
from spaces.learning.services.ingestion.qdrant_index import QdrantIndex
from spaces.learning.services.ingestion.retrieval import (
    GroundedRetrievalUnavailable,
    GroundedRetriever,
    OpenFangEmbeddingGateway,
)


RETRIEVAL_KEY_ENV = "LEARNING_RETRIEVAL_SERVICE_KEY"
RETRIEVAL_KEY_HEADER = "X-VibeMind-Learning-Retrieval-Key"
RETRIEVAL_KEY_MIN_LENGTH = 32
QDRANT_VECTOR_SIZE = 3072


class RetrievalRequest(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=8_000)]
    course_id: UUID
    limit: Annotated[int, Field(ge=1, le=20)] = 8


class RetrievalChunkResponse(BaseModel):
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


class RetrievalResponse(BaseModel):
    chunks: list[RetrievalChunkResponse]


def _tcp_probe(url: str, default_port: int) -> Callable[[], bool]:
    def probe() -> bool:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or default_port
        if not host:
            return False
        with socket.create_connection((host, port), timeout=2):
            return True

    return probe


def _database_probe(database_url: str) -> bool:
    engine = create_learning_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    finally:
        engine.dispose()


def _embedding_probe(base_url: str) -> bool:
    if not base_url.startswith(("http://", "https://")):
        return False
    response = httpx.get(f"{base_url.rstrip('/')}/health", timeout=3)
    return response.status_code == 200


def _migration_probe(database_url: str) -> bool:
    engine = create_learning_engine(database_url)
    try:
        present = set(inspect(engine).get_table_names())
        expected = set(Base.metadata.tables)
        return expected <= present
    finally:
        engine.dispose()


def build_health_service() -> HealthService:
    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    redis_url = os.environ.get("LEARNING_REDIS_URL", "").strip()
    qdrant_url = os.environ.get("LEARNING_QDRANT_URL", "").strip()
    embedding_url = os.environ.get("LEARNING_EMBEDDING_URL", "").strip()
    return HealthService(
        dependency_probes={
            "postgres": lambda: _database_probe(database_url),
            "redis": _tcp_probe(redis_url, 6379),
            "qdrant": _tcp_probe(qdrant_url, 6333),
            "embedding": lambda: _embedding_probe(embedding_url),
        },
        migration_probe=lambda: _migration_probe(database_url),
        structural_probe=lambda: [],
        golden_path_probe=lambda: None,
    )


@lru_cache(maxsize=1)
def _build_grounded_retriever() -> GroundedRetriever:
    qdrant_url = os.environ.get("LEARNING_QDRANT_URL", "").strip()
    embedding_url = os.environ.get("LEARNING_EMBEDDING_URL", "").strip()
    return GroundedRetriever(
        OpenFangEmbeddingGateway(embedding_url),
        QdrantIndex(qdrant_url, vector_size=QDRANT_VECTOR_SIZE),
    )


def _require_retrieval_key(supplied_key: str) -> None:
    configured_key = os.environ.get(RETRIEVAL_KEY_ENV, "")
    if len(configured_key) < RETRIEVAL_KEY_MIN_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Learning retrieval is not configured securely",
        )
    if not hmac.compare_digest(
        supplied_key.encode("utf-8"), configured_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid learning retrieval credentials",
        )


app = FastAPI(title="VibeMind Learning Runtime", version="1.0.0")


@app.get("/health/live")
def live() -> dict:
    return build_health_service().liveness()


@app.get("/health/ready")
def ready() -> JSONResponse:
    report = build_health_service().readiness()
    return JSONResponse(report, status_code=200 if report["status"] == "ok" else 503)


@app.get("/health/structural")
def structural() -> dict:
    return build_health_service().structural()


@app.get("/health/golden-path")
def golden_path() -> dict:
    return build_health_service().golden_path()


@app.post("/api/v1/retrieval/query", response_model=RetrievalResponse)
def retrieve_course_content(
    payload: RetrievalRequest,
    retrieval_key: Annotated[
        str, Header(alias=RETRIEVAL_KEY_HEADER, max_length=512)
    ] = "",
) -> RetrievalResponse:
    _require_retrieval_key(retrieval_key)
    try:
        chunks = _build_grounded_retriever().retrieve(
            payload.query,
            course_id=str(payload.course_id),
            limit=payload.limit,
        )
    except (GroundedRetrievalUnavailable, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grounded learning retrieval is unavailable",
        ) from error
    return RetrievalResponse(
        chunks=[
            RetrievalChunkResponse.model_validate(chunk, from_attributes=True)
            for chunk in chunks
        ]
    )


@app.post("/mcp")
async def mcp(request: Request) -> JSONResponse:
    response = handle_message(await request.json())
    if response is None:
        return JSONResponse({}, status_code=202)
    return JSONResponse(response)


def main() -> int:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
