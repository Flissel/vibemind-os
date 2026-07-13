"""HTTP wrapper around OpenAI's embeddings API.

Sole purpose: one place that holds the OpenAI credential for embedding
calls, instead of every brain-core variant mounting it separately, and one
interface (`/embed`, `/embed/batch`) that other consumers (mirofish,
rowboat-rag-worker) could adopt later without a redesign.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from openai import OpenAI

MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")

app = FastAPI(title="embedding-service")

_client: OpenAI | None = None


def _read_secret(name: str) -> str:
    """Same precedence as brain-core's core/config.py get_secret(): a
    Swarm-mounted secret file, then the default /run/secrets mount, then a
    plain env var."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path and os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    default_mount = f"/run/secrets/{name.lower()}"
    if os.path.exists(default_mount):
        with open(default_mount, "r", encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get(name, "").strip()


def get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = _read_secret("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not configured (checked _FILE, /run/secrets, env)"
            )
        _client = OpenAI(api_key=api_key)
    return _client


@app.get("/health")
def health():
    try:
        get_client()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"status": "ok", "model": MODEL}
