"""HTTP wrapper around OpenFang's embeddings gateway.

Embedding requests are sent only to OpenFang.  It owns provider credentials,
costing, tracing, and approval authority for the upstream embedding provider.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, List
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")
MAX_RETRIES = int(os.environ.get("EMBEDDING_MAX_RETRIES", "2"))
RETRY_BACKOFF_SECONDS = float(os.environ.get("EMBEDDING_RETRY_BACKOFF", "0.5"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("EMBEDDING_REQUEST_TIMEOUT", "30"))

logger = logging.getLogger("embedding_service")

app = FastAPI(title="embedding-service")

_gateway_client: httpx.Client | None = None


class EmbedRequest(BaseModel):
    text: str


class EmbedResponse(BaseModel):
    vector: List[float]


class EmbedBatchRequest(BaseModel):
    texts: List[str]


class EmbedBatchResponse(BaseModel):
    vectors: List[List[float]]


def _openfang_base_url() -> str:
    base_url = os.environ.get("OPENFANG_URL", "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if (
        not base_url
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or any(character.isspace() for character in base_url)
    ):
        raise RuntimeError("OpenFang gateway not configured")
    return base_url


def get_gateway_client() -> httpx.Client:
    """Return the singleton client for the configured OpenFang gateway only."""
    global _gateway_client
    if _gateway_client is None:
        gateway_key = os.environ.get("OPENFANG_API_KEY", "").strip()
        headers = {"Authorization": f"Bearer {gateway_key}"} if gateway_key else {}
        _gateway_client = httpx.Client(
            base_url=_openfang_base_url(),
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    return _gateway_client


@app.get("/health")
def health() -> dict[str, str]:
    try:
        get_gateway_client()
    except Exception as exc:
        logger.warning("OpenFang gateway health configuration failed: %s", exc)
        raise HTTPException(status_code=503, detail="OpenFang gateway not configured") from exc
    return {"status": "ok", "model": MODEL}


def _is_transient(exc: Exception) -> bool:
    """Only transport failures, rate limits, and 5xx gateway responses retry."""
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def _vectors_from_gateway_response(response: httpx.Response) -> List[List[float]]:
    payload: Any = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("OpenFang returned an invalid embeddings response")

    vectors: List[List[float]] = []
    for item in payload["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
            raise ValueError("OpenFang returned an invalid embeddings response")
        vectors.append(item["embedding"])
    return vectors


def _embed_with_retry(inputs: List[str]) -> List[List[float]]:
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = get_gateway_client().post(
                "/v1/embeddings", json={"model": MODEL, "input": inputs}
            )
            response.raise_for_status()
            return _vectors_from_gateway_response(response)
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if _is_transient(exc) and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise
    raise last_exc  # pragma: no cover - each loop iteration returns or raises


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    try:
        vectors = _embed_with_retry([req.text])
        vector = vectors[0]
    except Exception as exc:
        logger.warning("/embed via OpenFang failed: %s", exc)
        raise HTTPException(status_code=502, detail="embedding request failed") from exc
    return EmbedResponse(vector=vector)


@app.post("/embed/batch", response_model=EmbedBatchResponse)
def embed_batch(req: EmbedBatchRequest) -> EmbedBatchResponse:
    if not req.texts:
        return EmbedBatchResponse(vectors=[])
    try:
        vectors = _embed_with_retry(req.texts)
        if len(vectors) != len(req.texts):
            raise ValueError(
                f"expected {len(req.texts)} embeddings, got {len(vectors)}"
            )
    except Exception as exc:
        logger.warning("/embed/batch via OpenFang failed: %s", exc)
        raise HTTPException(status_code=502, detail="embedding request failed") from exc
    return EmbedBatchResponse(vectors=vectors)
