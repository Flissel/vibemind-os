"""
Migrate every cognitive Qdrant collection from the old 1024-dim Qwen
embedding space to the new 3072-dim embedding-service space, preserving
point IDs and payload — only the `semantic` vector changes.

Prerequisite: the embedding-service container must be reachable (this
script calls it directly over HTTP, the same way qdrant_kg.Embedder does)
and brain-core (+ siblings) should be scaled to replicas:0 for the
duration of a --commit / --cutover run, so no new writes land in the old
collections mid-migration (see docs/superpowers/specs/2026-07-13-brain-
embedder-external-api-design.md, "Migration" section).

Usage:
    # 1. Show what would happen, no writes (safe to run any time)
    python scripts/migrate_embeddings_v3072.py --dry-run

    # 2. Populate the new 3072-dim physical collections (old ones untouched)
    python scripts/migrate_embeddings_v3072.py --commit

    # 3. After manually comparing old vs. new point counts printed above:
    #    snapshot + delete the old raw collection + create the alias that
    #    makes the logical name resolve to the new physical collection.
    python scripts/migrate_embeddings_v3072.py --cutover
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_BRAIN_ROOT = os.path.dirname(_HERE)  # .../the_brain
if _BRAIN_ROOT not in sys.path:
    sys.path.insert(0, _BRAIN_ROOT)

from core.qdrant_kg import (  # noqa: E402
    COLLECTIONS, QDRANT_URL, PHYSICAL_VERSION_SUFFIX, NEURAL_DIM, SEMANTIC_DIM,
)
from core import config as _cfg  # noqa: E402

SCROLL_BATCH = 100  # also the embedding-service /embed/batch chunk size


def _embed_batch(base_url: str, texts: List[str]) -> List[List[float]]:
    import requests
    resp = requests.post(f"{base_url}/embed/batch", json={"texts": texts}, timeout=60)
    resp.raise_for_status()
    return resp.json()["vectors"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print plan, don't write (default)")
    ap.add_argument("--commit", action="store_true",
                     help="re-embed and populate new 3072-dim collections")
    ap.add_argument("--cutover", action="store_true",
                     help="snapshot + delete old raw collection + create alias "
                          "(run only after verifying --commit's point counts)")
    ap.add_argument("--url", default=QDRANT_URL, help=f"Qdrant URL (default: {QDRANT_URL})")
    ap.add_argument("--embedding-service-url", default=_cfg.embedding_service_url(),
                     help="embedding-service base URL")
    args = ap.parse_args()

    if not (args.dry_run or args.commit or args.cutover):
        args.dry_run = True  # be safe

    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm

    client = QdrantClient(url=args.url, timeout=60)

    print(f"[migrate] Qdrant URL:     {args.url}")
    print(f"[migrate] Embed service:  {args.embedding_service_url}")
    print(f"[migrate] Collections:    {len(COLLECTIONS)}")
    print()

    vectors_config = {
        "semantic": qm.VectorParams(size=SEMANTIC_DIM, distance=qm.Distance.COSINE),
        "neural": qm.VectorParams(size=NEURAL_DIM, distance=qm.Distance.COSINE, on_disk=True),
    }

    if args.cutover:
        for logical, old_name in COLLECTIONS.items():
            new_name = f"{old_name}{PHYSICAL_VERSION_SUFFIX}"
            try:
                old_info = client.get_collection(old_name)
                new_info = client.get_collection(new_name)
            except Exception as e:
                print(f"[migrate] SKIP {old_name}: {e}")
                continue
            if old_info.points_count != new_info.points_count:
                print(f"[migrate] REFUSING cutover for {old_name}: "
                      f"old={old_info.points_count} new={new_info.points_count} "
                      f"point counts differ — investigate before cutting over.")
                continue
            print(f"[migrate] snapshotting '{old_name}' before deleting it...")
            client.create_snapshot(collection_name=old_name, wait=True)
            client.delete_collection(collection_name=old_name)
            client.update_collection_aliases(change_aliases_operations=[
                qm.CreateAliasOperation(create_alias=qm.CreateAlias(
                    collection_name=new_name, alias_name=old_name,
                )),
            ])
            print(f"[migrate] cutover done: '{old_name}' now aliases '{new_name}'")
        return 0

    total_migrated = 0
    for logical, old_name in COLLECTIONS.items():
        new_name = f"{old_name}{PHYSICAL_VERSION_SUFFIX}"
        try:
            old_info = client.get_collection(old_name)
        except Exception as e:
            print(f"[migrate] source '{old_name}' not found, skipping: {e}")
            continue
        print(f"[migrate] {old_name}: {old_info.points_count} points -> {new_name}")

        if args.commit:
            existing = {c.name for c in client.get_collections().collections}
            if new_name not in existing:
                client.create_collection(collection_name=new_name, vectors_config=vectors_config)
                print(f"[migrate]   created '{new_name}'")

        offset = None
        moved = 0
        t0 = time.time()
        while True:
            batch, next_offset = client.scroll(
                collection_name=old_name, limit=SCROLL_BATCH, offset=offset,
                with_payload=True, with_vectors=False,
            )
            if not batch:
                break
            texts = [rec.payload.get("content", "") for rec in batch]
            if args.commit:
                new_vectors = _embed_batch(args.embedding_service_url, texts)
                points = [
                    qm.PointStruct(
                        id=rec.id,
                        vector={"semantic": new_vectors[i]},
                        payload=rec.payload,
                    )
                    for i, rec in enumerate(batch)
                ]
                client.upsert(collection_name=new_name, points=points, wait=True)
            moved += len(batch)
            if next_offset is None:
                break
            offset = next_offset
        dt = time.time() - t0
        verb = "would re-embed" if args.dry_run else "re-embedded"
        print(f"[migrate]   {verb} {moved} points in {dt:.1f}s")
        total_migrated += moved

    print()
    if args.dry_run:
        print(f"[migrate] DRY RUN — {total_migrated} points would be migrated. "
              f"Re-run with --commit to actually write.")
        return 0

    print("[migrate] verifying counts old vs. new...")
    for logical, old_name in COLLECTIONS.items():
        new_name = f"{old_name}{PHYSICAL_VERSION_SUFFIX}"
        try:
            old_count = client.get_collection(old_name).points_count
            new_count = client.get_collection(new_name).points_count
            flag = "OK" if old_count == new_count else "MISMATCH"
            print(f"[migrate]   {old_name}: old={old_count} new={new_count} [{flag}]")
        except Exception as e:
            print(f"[migrate]   {old_name}: ERROR {e}")

    print()
    print("[migrate] If all counts show OK, re-run with --cutover to swap the aliases.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
