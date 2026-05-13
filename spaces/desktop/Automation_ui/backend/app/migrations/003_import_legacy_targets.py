"""Import vibevideo_deepfake/faceswap/targets/*.jpg into Supabase.

Reads the legacy filesystem-based target collection (face1.jpg..face101.jpg
plus the DISPLAY_NAMES dict in presets.py) and inserts them into
``public.face_targets`` + ``public.face_target_blobs``.

Idempotent: re-running just re-uploads any new files; existing rows
are upserted by slug.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Locate the faceswap package
_HERE = Path(__file__).resolve()
_VIBEMIND_OS = _HERE.parents[6]
_DEEPFAKE_DIR = _VIBEMIND_OS / "spaces" / "video" / "vibevideo_deepfake"
sys.path.insert(0, str(_DEEPFAKE_DIR))

from faceswap.presets import DISPLAY_NAMES, TARGETS_DIR  # type: ignore
import psycopg2
import uuid
import os

DB_URL = os.environ.get(
    "FACE_DB_URL",
    "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
)


def main() -> int:
    if not TARGETS_DIR.is_dir():
        print(f"[error] targets dir not found: {TARGETS_DIR}")
        return 1

    conn = psycopg2.connect(DB_URL)
    # Ensure blob table exists (face_targets.py creates it lazily; mirror here)
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            create table if not exists public.face_target_blobs (
                storage_path text primary key,
                bytes        bytea not null,
                created_at   timestamptz default now()
            )
            """
        )

    imported = 0
    skipped = 0
    for jpg in sorted(TARGETS_DIR.glob("*.jpg")):
        slug = jpg.stem
        display = DISPLAY_NAMES.get(slug, slug.replace("_", " ").title())
        blob = jpg.read_bytes()
        image_id = uuid.uuid4().hex
        storage_path = f"{slug}/{image_id}.jpg"

        with conn, conn.cursor() as cur:
            # Skip if already enabled + has a primary image
            cur.execute(
                "select primary_image_path from public.face_targets "
                "where id = %s and enabled = true",
                (slug,),
            )
            existing = cur.fetchone()
            if existing:
                print(f"  [skip] {slug:10s} -> already imported ({existing[0]})")
                skipped += 1
                continue

            # Insert blob
            cur.execute(
                "insert into public.face_target_blobs(storage_path, bytes) "
                "values (%s, %s) on conflict (storage_path) do nothing",
                (storage_path, psycopg2.Binary(blob)),
            )
            # Insert metadata
            cur.execute(
                """
                insert into public.face_targets
                    (id, display_name, primary_image_path, tags,
                     source_url, consent_status)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (id) do update set
                    display_name = excluded.display_name,
                    primary_image_path = excluded.primary_image_path,
                    enabled = true
                """,
                (
                    slug,
                    display,
                    storage_path,
                    ["legacy", "imported"],
                    None,
                    "private",
                ),
            )
            cur.execute(
                """
                insert into public.face_target_images
                    (target_id, storage_path)
                values (%s, %s)
                """,
                (slug, storage_path),
            )
        imported += 1
        print(f"  [ok]   {slug:10s} -> {display}")

    conn.close()
    print(f"\n{imported} imported, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
