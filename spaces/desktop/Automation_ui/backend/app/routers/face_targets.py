"""Face-target persistence via Supabase Postgres.

The legacy filesystem-based ``vibevideo_deepfake/faceswap/targets/*.jpg``
collection is still consulted as a fallback, but new targets are stored
here with multi-image support, embeddings, and tags.

Endpoints
---------
GET    /api/face-targets                → list all enabled targets
GET    /api/face-targets/{id}/image    → first image bytes (JPEG)
POST   /api/face-targets                → upload new target (multipart)
DELETE /api/face-targets/{id}           → soft-disable
GET    /api/face-targets/{id}/images    → list all images for one target
POST   /api/face-targets/{id}/images    → add another source image
"""

from __future__ import annotations

import io
import logging
import os
import re
import uuid
from pathlib import Path
from typing import List, Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/face-targets", tags=["face-targets"])


# ───────────────────────────────────────────────────────────────────────
# Connection
# ───────────────────────────────────────────────────────────────────────

# Supabase local postgres (mapped to host port 54322 in the docker-compose).
# Override with FACE_DB_URL in .env for prod.
_DB_URL = os.environ.get(
    "FACE_DB_URL",
    "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
)


def _conn():
    return psycopg2.connect(_DB_URL)


# Storage: bytea column on a side table. Keeps things atomic with the
# metadata transaction and avoids the JWT dance for tiny local installs.
# If you want true Supabase Storage with CDN later, swap _save_bytes /
# _load_bytes — the table-level API stays the same.

def _save_bytes(target_id: str, image_id: str, blob: bytes) -> str:
    """Store image bytes; returns a stable storage_path identifier."""
    storage_path = f"{target_id}/{image_id}.jpg"
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            create table if not exists public.face_target_blobs (
                storage_path text primary key,
                bytes        bytea not null,
                created_at   timestamptz default now()
            )
            """
        )
        cur.execute(
            "insert into public.face_target_blobs(storage_path, bytes) values (%s, %s) "
            "on conflict (storage_path) do update set bytes = excluded.bytes",
            (storage_path, psycopg2.Binary(blob)),
        )
    return storage_path


def _load_bytes(storage_path: str) -> Optional[bytes]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select bytes from public.face_target_blobs where storage_path = %s",
            (storage_path,),
        )
        row = cur.fetchone()
        return bytes(row[0]) if row else None


# ───────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slugify(name: str) -> str:
    s = name.strip().lower().replace(" ", "_")
    s = _SLUG_RE.sub("", s)
    return s[:64] or f"face_{uuid.uuid4().hex[:8]}"


def _detect_face_and_embedding(blob: bytes) -> tuple[Optional[bytes], Optional[list[float]], Optional[tuple[int, int]]]:
    """Crop face + compute identity embedding. Best-effort; on any failure
    we still save the original blob and skip the embedding column.

    Heavy ML (insightface) lives in voice/.venv312 only, so we run a quick
    subprocess to do the detection. Keeps backend .venv free of CUDA deps.
    """
    # For now, skip detection here and store raw upload — UI will pick up
    # whatever the user gave us. A later cron job can backfill embeddings.
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(blob, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None, None, None
        h, w = img.shape[:2]
        return blob, None, (w, h)
    except Exception as e:
        logger.warning("face detection skipped: %s", e)
        return blob, None, None


# ───────────────────────────────────────────────────────────────────────
# Endpoints
# ───────────────────────────────────────────────────────────────────────

@router.get("")
async def list_face_targets():
    """Return [{id, display_name, primary_image_url, tags}, ...]."""
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            select id, display_name, primary_image_path, tags, source_url,
                   consent_status, created_at
            from public.face_targets
            where enabled = true
            order by display_name asc
            """
        )
        rows = cur.fetchall()
    return {
        "targets": [
            {
                "id": r["id"],
                "name": r["display_name"],
                "image_url": f"/api/face-targets/{r['id']}/image",
                "tags": list(r["tags"] or []),
                "source_url": r["source_url"],
                "consent_status": r["consent_status"],
            }
            for r in rows
        ]
    }


@router.get("/{target_id}/image")
async def get_face_image(target_id: str):
    """Return JPEG bytes for the primary image of this target."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select primary_image_path from public.face_targets "
            "where id = %s and enabled = true",
            (target_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"target {target_id!r} not found")
    blob = _load_bytes(row[0])
    if blob is None:
        raise HTTPException(404, f"image bytes missing for {target_id!r}")
    return Response(content=blob, media_type="image/jpeg")


@router.post("")
async def upload_face_target(
    display_name: str = Form(...),
    image: UploadFile = File(...),
    tags: str = Form(""),
    source_url: str = Form(""),
    consent_status: str = Form("private"),
):
    """Add a new face-target. Auto-generates slug from display_name."""
    slug = _slugify(display_name)
    blob = await image.read()
    if not blob:
        raise HTTPException(400, "empty image upload")

    # Light validation + size capture (full insightface embedding later)
    detected, embedding, dims = _detect_face_and_embedding(blob)
    if detected is None:
        raise HTTPException(400, "cannot decode image as JPEG/PNG/WebP")

    image_id = uuid.uuid4().hex
    storage_path = _save_bytes(slug, image_id, detected)

    with _conn() as c, c.cursor() as cur:
        # Upsert the parent row (idempotent on slug)
        cur.execute(
            """
            insert into public.face_targets
                (id, display_name, primary_image_path, tags, source_url, consent_status)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (id) do update set
                display_name = excluded.display_name,
                primary_image_path = excluded.primary_image_path,
                tags = excluded.tags,
                source_url = excluded.source_url,
                consent_status = excluded.consent_status,
                enabled = true
            """,
            (
                slug,
                display_name,
                storage_path,
                [t.strip() for t in tags.split(",") if t.strip()],
                source_url or None,
                consent_status,
            ),
        )
        # Always log a row in the images side-table
        cur.execute(
            """
            insert into public.face_target_images
                (target_id, storage_path, width, height)
            values (%s, %s, %s, %s)
            """,
            (
                slug,
                storage_path,
                dims[0] if dims else None,
                dims[1] if dims else None,
            ),
        )
    return {
        "id": slug,
        "name": display_name,
        "image_url": f"/api/face-targets/{slug}/image",
    }


@router.delete("/{target_id}")
async def disable_face_target(target_id: str):
    """Soft-disable. Bytes stay so it can be re-enabled."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "update public.face_targets set enabled = false where id = %s",
            (target_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, f"target {target_id!r} not found")
    return {"id": target_id, "disabled": True}
