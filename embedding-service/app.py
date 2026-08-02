"""Fail-closed HTTP wrapper around the Shared OpenFang embedding factory."""
from __future__ import annotations

import logging
from typing import Any, List
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from vibemind_shared import (
    get_embedding_config,
    get_embedding_model,
    get_provider_info,
)

EMBEDDING_ROLE = "fungus_search"

logger = logging.getLogger("embedding_service")

app = FastAPI(title="embedding-service")


class EmbedRequest(BaseModel):
    text: str


class EmbedResponse(BaseModel):
    vector: List[float]


class EmbedBatchRequest(BaseModel):
    texts: List[str]


class EmbedBatchResponse(BaseModel):
    vectors: List[List[float]]


def _embedding_config() -> dict[str, Any]:
    config = get_embedding_config(EMBEDDING_ROLE)
    if (
        config.get("driver") != "openai"
        or config.get("provider") != "openfang"
        or not isinstance(config.get("model"), str)
        or not config["model"].strip()
        or int(config.get("dim", 0)) <= 0
    ):
        raise RuntimeError("fungus_search must use a configured OpenFang embedding backend")
    return config


def _embedding_backend() -> tuple[dict[str, Any], Any]:
    config = _embedding_config()
    return config, get_embedding_model(EMBEDDING_ROLE)


def _vectors(encoded: Any) -> List[List[float]]:
    value = encoded.tolist() if hasattr(encoded, "tolist") else encoded
    if not isinstance(value, list) or any(not isinstance(vector, list) for vector in value):
        raise ValueError("Shared embedding factory returned an invalid vector response")
    return value


def _openfang_health_target() -> tuple[str, float]:
    provider = get_provider_info()
    if provider.get("provider") != "openfang":
        raise RuntimeError("Shared default provider is not OpenFang")

    base_url = str(provider.get("base_url", ""))
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("Shared OpenFang base URL is invalid")
    if parsed.path.rstrip("/") != "/v1":
        raise RuntimeError("Shared OpenFang base URL must resolve the v1 endpoint")

    timeout = float(provider.get("timeout_seconds", 0))
    if timeout <= 0:
        raise RuntimeError("Shared OpenFang timeout is invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, "/api/health", "", "")), timeout


def _check_openfang_health() -> None:
    health_url, timeout = _openfang_health_target()
    with urlopen(health_url, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"OpenFang health returned HTTP {response.status}")


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        config, _ = _embedding_backend()
        _check_openfang_health()
    except Exception as exc:
        logger.warning("embedding service health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="embedding service unavailable") from exc
    return {"status": "ok", "model": config["model"], "dim": int(config["dim"])}


def _embed(inputs: List[str]) -> List[List[float]]:
    _, backend = _embedding_backend()
    return _vectors(backend.encode(inputs))


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    try:
        vectors = _embed([req.text])
        vector = vectors[0]
    except Exception as exc:
        logger.warning("/embed through Shared OpenFang factory failed: %s", exc)
        raise HTTPException(status_code=502, detail="embedding request failed") from exc
    return EmbedResponse(vector=vector)


@app.post("/embed/batch", response_model=EmbedBatchResponse)
def embed_batch(req: EmbedBatchRequest) -> EmbedBatchResponse:
    if not req.texts:
        return EmbedBatchResponse(vectors=[])
    try:
        vectors = _embed(req.texts)
        if len(vectors) != len(req.texts):
            raise ValueError(
                f"expected {len(req.texts)} embeddings, got {len(vectors)}"
            )
    except Exception as exc:
        logger.warning("/embed/batch through Shared OpenFang factory failed: %s", exc)
        raise HTTPException(status_code=502, detail="embedding request failed") from exc
    return EmbedBatchResponse(vectors=vectors)
