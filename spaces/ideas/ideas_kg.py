"""
IdeasKG — Phase Q.1.

Self-contained Qdrant-backed semantic index for the Ideas-Space.
Pattern: brain/the_brain/core/qdrant_kg.py, but standalone (no Brain
imports) so Ideas-Space stays autonomous.

Single Qdrant collection ``ideas-kg`` on the shared Qdrant server (port
6340 by default). Dual-vector schema is reserved for symmetry with Brain
(`semantic` 1024d Qwen + `neural` 20484d on-disk for future TriBE), but
only the semantic slot is populated today.

Public API
----------
    kg = IdeasKG()
    kg.ensure_collection()
    kg.upsert(idea_dict)
    kg.delete(idea_id)
    kg.search(query, limit=10, threshold=0.3, node_type=None)
    kg.stats()

`idea_dict` shape (matches the SQLite ``ideas`` row):
    {
      "id": "abc12345",
      "title": "...",
      "description": "...",
      "tags": ["..."],
      "source": "...",
      "score": 0.0,
      "status": "raw",
      "parent_id": "..." | None,    # None = bubble (top-level)
      "created_at": "2026-04-29 ...",
    }

Env
---
    QDRANT_URL                default http://127.0.0.1:6340
    IDEAS_KG_COLLECTION       default ideas-kg
    IDEAS_EMBED_MODEL         default Qwen/Qwen3-Embedding-0.6B
    IDEAS_KG_BATCH_SIZE       default 32  (full-resync embed batch)
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6340").rstrip("/")
COLLECTION = os.environ.get("IDEAS_KG_COLLECTION", "ideas-kg")
EMBED_MODEL = os.environ.get("IDEAS_EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")
BATCH_SIZE = int(os.environ.get("IDEAS_KG_BATCH_SIZE", "32"))

SEMANTIC_DIM = 1024
NEURAL_DIM = 20484  # reserved for TriBE symmetry, on-disk

NT_IDEA = "idea"
NT_BUBBLE = "bubble"


# ──────────────────────────────────────────────────────────────────────
# Embedder singleton
# ──────────────────────────────────────────────────────────────────────

class Embedder:
    _instance: Optional["Embedder"] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "Embedder":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
        t0 = time.time()
        logger.info(f"[ideas-kg] loading {EMBED_MODEL} on {device}")
        self._model = SentenceTransformer(EMBED_MODEL, device=device)
        self._encode_lock = threading.Lock()
        self._device = device
        logger.info(f"[ideas-kg] embed model loaded in {time.time()-t0:.1f}s")

    def encode(self, text: str) -> List[float]:
        with self._encode_lock:
            v = self._model.encode(
                text, normalize_embeddings=True,
                convert_to_numpy=True, show_progress_bar=False,
            )
        return v.tolist()

    def encode_batch(self, texts: List[str]) -> List[List[float]]:
        with self._encode_lock:
            v = self._model.encode(
                texts, normalize_embeddings=True,
                convert_to_numpy=True, show_progress_bar=False,
                batch_size=BATCH_SIZE,
            )
        return [row.tolist() for row in v]


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _point_id(external_id: str) -> str:
    """Deterministic UUID derived from the external idea_id so re-upsert
    collapses duplicates."""
    h = hashlib.sha256(external_id.encode("utf-8")).hexdigest()
    return str(uuid.UUID(h[:32]))


def _idea_text(idea: Dict[str, Any]) -> str:
    """Compose the embedding text from title + description."""
    title = str(idea.get("title") or "").strip()
    desc = str(idea.get("description") or idea.get("content") or "").strip()
    if title and desc:
        return f"{title}\n{desc}"
    return title or desc


def _node_type(idea: Dict[str, Any]) -> str:
    """A row is a ``bubble`` if it has no parent_id (top-level container);
    otherwise it's a regular ``idea`` inside a bubble."""
    return NT_BUBBLE if idea.get("parent_id") in (None, "") else NT_IDEA


# ──────────────────────────────────────────────────────────────────────
# IdeasKG
# ──────────────────────────────────────────────────────────────────────

class IdeasKG:
    """Qdrant-backed semantic index for the Ideas-Space.

    Owns its own Qdrant client and Embedder singleton. Brain reads from
    the same Qdrant server but never writes here — single-writer model.
    """

    def __init__(self, url: str = QDRANT_URL, collection: str = COLLECTION) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm
        self._qm = qm
        self.url = url
        self.collection = collection
        self.client = QdrantClient(url=url, timeout=30)
        self._embedder: Optional[Embedder] = None
        self._embed_lock = threading.Lock()
        self._last_full_resync_ts: Optional[float] = None
        self.stats_dict: Dict[str, Any] = {
            "upserts": 0,
            "deletes": 0,
            "searches": 0,
            "errors": 0,
            "last_error": None,
            "last_upsert_id": None,
            "last_full_resync_ts": None,
            "last_full_resync_count": 0,
        }

    # ── Setup ─────────────────────────────────────────────────────────

    def ensure_collection(self) -> None:
        """Create the ideas-kg collection if missing. Idempotent."""
        qm = self._qm
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection in existing:
            logger.debug(f"[ideas-kg] collection '{self.collection}' already exists")
        else:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config={
                    "semantic": qm.VectorParams(
                        size=SEMANTIC_DIM, distance=qm.Distance.COSINE,
                    ),
                    "neural": qm.VectorParams(
                        size=NEURAL_DIM, distance=qm.Distance.COSINE,
                        on_disk=True,
                    ),
                },
            )
            logger.info(
                f"[ideas-kg] created collection '{self.collection}' "
                f"(semantic={SEMANTIC_DIM}d, neural={NEURAL_DIM}d on_disk)"
            )
        # Indexes for fast filter
        for field, schema in (
            ("node_type", qm.PayloadSchemaType.KEYWORD),
            ("idea_id", qm.PayloadSchemaType.KEYWORD),
            ("bubble_id", qm.PayloadSchemaType.KEYWORD),
            ("source", qm.PayloadSchemaType.KEYWORD),
            ("status", qm.PayloadSchemaType.KEYWORD),
            ("created_at_ts", qm.PayloadSchemaType.INTEGER),
        ):
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field, field_schema=schema,
                )
            except Exception as e:
                logger.debug(f"[ideas-kg] index '{field}': {e}")

    # ── Embedder ──────────────────────────────────────────────────────

    def _embedder_ready(self) -> Embedder:
        if self._embedder is None:
            with self._embed_lock:
                if self._embedder is None:
                    self._embedder = Embedder.get()
        return self._embedder

    # ── Upsert / Delete ───────────────────────────────────────────────

    def upsert(self, idea: Dict[str, Any], vec: Optional[List[float]] = None) -> Optional[str]:
        """Upsert one idea/bubble. Returns the Qdrant point id (UUID).

        If `vec` is given, skip embedding (used by batch resync).
        """
        external_id = str(idea.get("id") or "").strip()
        if not external_id:
            return None
        text = _idea_text(idea)
        if not text:
            return None
        try:
            qm = self._qm
            if vec is None:
                vec = self._embedder_ready().encode(text)
            pid = _point_id(external_id)
            created_at = idea.get("created_at")
            try:
                from datetime import datetime
                if isinstance(created_at, str):
                    created_at_ts = int(datetime.fromisoformat(
                        created_at.replace("Z", "+00:00").split(".")[0]
                    ).timestamp())
                else:
                    created_at_ts = int(time.time())
            except Exception:
                created_at_ts = int(time.time())

            payload = {
                "node_type": _node_type(idea),
                "idea_id": external_id,
                "bubble_id": idea.get("parent_id") or "",
                "title": (idea.get("title") or "")[:200],
                "description": (idea.get("description") or "")[:2000],
                "content": text[:2200],
                "tags": idea.get("tags") or [],
                "source": idea.get("source") or "",
                "status": idea.get("status") or "raw",
                "score": float(idea.get("score") or 0.0),
                "created_at": idea.get("created_at"),
                "created_at_ts": created_at_ts,
                "linked": idea.get("linked") or {
                    "ideas": [], "bubbles": [], "concepts": [], "thoughts": [],
                },
            }
            self.client.upsert(
                collection_name=self.collection,
                points=[qm.PointStruct(
                    id=pid, vector={"semantic": vec}, payload=payload,
                )],
                wait=True,
            )
            self.stats_dict["upserts"] += 1
            self.stats_dict["last_upsert_id"] = external_id
            return pid
        except Exception as e:
            self.stats_dict["errors"] += 1
            self.stats_dict["last_error"] = f"upsert({external_id}): {type(e).__name__}: {e}"
            logger.warning(f"[ideas-kg] upsert failed: {e}")
            return None

    def upsert_many(self, ideas: List[Dict[str, Any]]) -> int:
        """Batch-upsert. Embeds in one pass for GPU efficiency.
        Returns the number of points written."""
        if not ideas:
            return 0
        try:
            valid: List[Dict[str, Any]] = []
            texts: List[str] = []
            for it in ideas:
                ext = str(it.get("id") or "").strip()
                txt = _idea_text(it)
                if ext and txt:
                    valid.append(it)
                    texts.append(txt)
            if not valid:
                return 0
            vecs = self._embedder_ready().encode_batch(texts)
            n = 0
            for it, v in zip(valid, vecs):
                if self.upsert(it, vec=v):
                    n += 1
            return n
        except Exception as e:
            self.stats_dict["errors"] += 1
            self.stats_dict["last_error"] = f"upsert_many: {e}"
            logger.warning(f"[ideas-kg] upsert_many failed: {e}")
            return 0

    def delete(self, idea_id: str) -> bool:
        try:
            pid = _point_id(idea_id)
            self.client.delete(
                collection_name=self.collection,
                points_selector=self._qm.PointIdsList(points=[pid]),
            )
            self.stats_dict["deletes"] += 1
            return True
        except Exception as e:
            self.stats_dict["errors"] += 1
            self.stats_dict["last_error"] = f"delete({idea_id}): {e}"
            return False

    # ── Search ────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 10,
        threshold: float = 0.3,
        node_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        try:
            qm = self._qm
            vec = self._embedder_ready().encode(query)
            qfilter = None
            if node_type:
                qfilter = qm.Filter(must=[qm.FieldCondition(
                    key="node_type",
                    match=qm.MatchValue(value=node_type),
                )])
            result = self.client.query_points(
                collection_name=self.collection,
                query=vec,
                using="semantic",
                limit=max(1, min(100, limit)),
                score_threshold=max(0.0, min(1.0, threshold)),
                query_filter=qfilter,
                with_payload=True,
            )
            hits = result.points if hasattr(result, "points") else result
            self.stats_dict["searches"] += 1
            return [{
                "id": str(h.id),
                "score": float(h.score),
                "payload": h.payload or {},
            } for h in hits]
        except Exception as e:
            self.stats_dict["errors"] += 1
            self.stats_dict["last_error"] = f"search: {e}"
            logger.warning(f"[ideas-kg] search failed: {e}")
            return []

    # ── Stats ─────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        try:
            count = self.client.count(
                collection_name=self.collection, exact=False,
            ).count
        except Exception as e:
            count = -1
            self.stats_dict["last_error"] = f"count: {e}"
        return {
            "collection": self.collection,
            "url": self.url,
            "points": int(count),
            **self.stats_dict,
        }

    # ── Resync helper ─────────────────────────────────────────────────

    def mark_full_resync(self, n_points: int) -> None:
        ts = time.time()
        self._last_full_resync_ts = ts
        self.stats_dict["last_full_resync_ts"] = ts
        self.stats_dict["last_full_resync_count"] = n_points
