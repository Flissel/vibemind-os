"""
IdeasSync — Phase Q.2.

Bidirectional bridge between the SQLite ``ideas`` table (Single Source
of Truth) and the IdeasKG Qdrant collection (semantic index).

Three operating modes:

1. **sync_one(idea_id)** — fired by HTTP-Wrapper after every CRUD op
   (create / update / move / delete). Sub-second latency from DB write
   to searchable index.
2. **full_resync()** — scrolls the entire ``ideas`` table, batch-embeds
   on GPU, upserts everything. Idempotent — runs once on boot and again
   when scheduled by the nightly loop. Also persists embeddings back to
   the DB columns ``embedding_vector`` + ``embedding_hash`` so other
   consumers can read them without recomputing.
3. **nightly_loop()** — daemon thread, ticks every
   ``IDEAS_RESYNC_INTERVAL_S`` seconds (default 86400 = 24h).

Env
---
    IDEAS_DB_PATH               — same as http_server (SQLite path)
    IDEAS_RESYNC_INTERVAL_S     — default 86400
    IDEAS_INITIAL_RESYNC_DELAY  — default 60 (seconds after boot)
    IDEAS_BATCH_SIZE            — default 32
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


RESYNC_INTERVAL_S = float(os.environ.get("IDEAS_RESYNC_INTERVAL_S", "86400"))
INITIAL_RESYNC_DELAY = float(os.environ.get("IDEAS_INITIAL_RESYNC_DELAY", "60"))
BATCH_SIZE = int(os.environ.get("IDEAS_BATCH_SIZE", "32"))


def _hash_for_idea(title: str, description: str) -> str:
    payload = f"{title or ''}|||{description or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class IdeasSync:
    """SQLite → IdeasKG sync pipeline."""

    def __init__(self, db_path: str, kg, write_back_embeddings: bool = True) -> None:
        """
        Args:
            db_path: absolute path to vibemind.db
            kg: IdeasKG instance (already constructed, ensure_collection ran)
            write_back_embeddings: if True (default), persists newly computed
                vectors back to ``embedding_vector`` / ``embedding_hash`` so
                next boot starts warm.
        """
        self.db_path = db_path
        self.kg = kg
        self.write_back = write_back_embeddings

        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._loop_lock = threading.Lock()
        self.stats: Dict[str, Any] = {
            "ticks": 0,
            "sync_one_calls": 0,
            "sync_one_failures": 0,
            "last_sync_one_id": None,
            "last_full_resync_ts": None,
            "last_full_resync_count": 0,
            "last_full_resync_dt_s": None,
            "embeddings_written_back": 0,
            "errors": 0,
            "last_error": None,
        }

    # ── DB helpers ────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_to_idea(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for k in ("tags", "metadata"):
            v = d.get(k)
            if isinstance(v, str) and v:
                try:
                    d[k] = json.loads(v)
                except Exception:
                    pass
        d.pop("embedding_vector", None)
        d.pop("embedding_hash", None)
        return d

    # ── Single sync (fired from HTTP CRUD) ────────────────────────────

    def sync_one(self, idea_id: str) -> bool:
        """Embed + upsert exactly one idea by id. If the row no longer
        exists in SQLite the corresponding KG point is deleted (true
        bidirectional consistency)."""
        if not idea_id:
            return False
        self.stats["sync_one_calls"] += 1
        self.stats["last_sync_one_id"] = idea_id
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM ideas WHERE id = ?", (idea_id,),
            ).fetchone()
            if row is None:
                conn.close()
                ok = self.kg.delete(idea_id)
                if not ok:
                    self.stats["sync_one_failures"] += 1
                return ok
            idea = self._row_to_idea(row)
            pid = self.kg.upsert(idea)
            if pid is None:
                self.stats["sync_one_failures"] += 1
                conn.close()
                return False
            if self.write_back:
                self._write_back_embedding(conn, idea)
            conn.close()
            return True
        except Exception as e:
            self.stats["sync_one_failures"] += 1
            self.stats["last_error"] = f"sync_one({idea_id}): {e}"
            logger.warning(f"[ideas-sync] sync_one({idea_id}) failed: {e}")
            return False

    def _write_back_embedding(
        self, conn: sqlite3.Connection, idea: Dict[str, Any],
    ) -> None:
        """Compute embedding from same `title|description` text and store
        in DB columns. Called only when row content actually changed (hash)."""
        title = idea.get("title") or ""
        description = idea.get("description") or ""
        new_hash = _hash_for_idea(title, description)
        try:
            existing = conn.execute(
                "SELECT embedding_hash FROM ideas WHERE id = ?",
                (idea["id"],),
            ).fetchone()
            existing_hash = existing["embedding_hash"] if existing else None
        except Exception:
            existing_hash = None
        if existing_hash == new_hash:
            return  # already up-to-date
        try:
            text = (title + "\n" + description).strip()
            if not text:
                return
            vec = self.kg._embedder_ready().encode(text)
            conn.execute(
                "UPDATE ideas SET embedding_vector = ?, embedding_hash = ? WHERE id = ?",
                (json.dumps(vec), new_hash, idea["id"]),
            )
            conn.commit()
            self.stats["embeddings_written_back"] += 1
        except Exception as e:
            self.stats["last_error"] = f"write_back({idea.get('id')}): {e}"
            logger.debug(f"[ideas-sync] write_back failed: {e}")

    # ── Full resync ───────────────────────────────────────────────────

    def full_resync(self) -> Dict[str, Any]:
        """Pull every row from `ideas` and upsert into ideas-kg via batch
        embedding. Idempotent. Heavy — only call from nightly_loop or boot."""
        with self._loop_lock:
            t0 = time.time()
            try:
                conn = self._connect()
                rows = conn.execute("SELECT * FROM ideas").fetchall()
                ideas = [self._row_to_idea(r) for r in rows]
                conn.close()
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["last_error"] = f"full_resync fetch: {e}"
                logger.warning(f"[ideas-sync] full_resync fetch failed: {e}")
                return {"ok": False, "error": str(e)}

            n_total = len(ideas)
            written = 0
            for start in range(0, n_total, BATCH_SIZE):
                chunk = ideas[start:start + BATCH_SIZE]
                written += self.kg.upsert_many(chunk)

            # Persist embeddings back so next boot starts warm
            if self.write_back and ideas:
                self._batch_write_back(ideas)

            dt = time.time() - t0
            ts = time.time()
            self.stats["last_full_resync_ts"] = ts
            self.stats["last_full_resync_count"] = written
            self.stats["last_full_resync_dt_s"] = round(dt, 2)
            self.kg.mark_full_resync(written)
            logger.info(
                f"[ideas-sync] full resync: {written}/{n_total} points in {dt:.1f}s"
            )
            return {
                "ok": True, "total": n_total, "written": written,
                "duration_s": round(dt, 2),
            }

    def _batch_write_back(self, ideas: List[Dict[str, Any]]) -> None:
        """Compute fresh embeddings for any ideas whose hash has drifted
        and store them back."""
        try:
            conn = self._connect()
            existing_hashes: Dict[str, str] = {}
            for r in conn.execute(
                "SELECT id, embedding_hash FROM ideas",
            ).fetchall():
                existing_hashes[r["id"]] = r["embedding_hash"] or ""

            stale: List[Dict[str, Any]] = []
            new_hashes: Dict[str, str] = {}
            for it in ideas:
                iid = it.get("id")
                if not iid:
                    continue
                title = it.get("title") or ""
                description = it.get("description") or ""
                h = _hash_for_idea(title, description)
                new_hashes[iid] = h
                if existing_hashes.get(iid) != h:
                    stale.append(it)

            if not stale:
                conn.close()
                return

            texts = [
                ((s.get("title") or "") + "\n" + (s.get("description") or "")).strip()
                for s in stale
            ]
            vecs = self.kg._embedder_ready().encode_batch(texts)
            for s, v in zip(stale, vecs):
                conn.execute(
                    "UPDATE ideas SET embedding_vector = ?, embedding_hash = ? WHERE id = ?",
                    (json.dumps(v), new_hashes[s["id"]], s["id"]),
                )
            conn.commit()
            conn.close()
            self.stats["embeddings_written_back"] += len(stale)
            logger.info(f"[ideas-sync] wrote {len(stale)} embeddings back to DB")
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = f"batch_write_back: {e}"
            logger.warning(f"[ideas-sync] batch_write_back failed: {e}")

    # ── Nightly loop ──────────────────────────────────────────────────

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._loop, daemon=True, name="IdeasSync",
        )
        self._worker.start()
        logger.info(
            f"[ideas-sync] nightly loop started "
            f"(interval={RESYNC_INTERVAL_S}s, initial_delay={INITIAL_RESYNC_DELAY}s)"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=5)

    def _loop(self) -> None:
        # Initial resync after a short grace period (so HTTP is responsive first)
        self._stop.wait(INITIAL_RESYNC_DELAY)
        if self._stop.is_set():
            return
        try:
            self.full_resync()
            self.stats["ticks"] += 1
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = f"initial: {e}"
            logger.warning(f"[ideas-sync] initial resync failed: {e}")
        # Subsequent ticks
        while not self._stop.is_set():
            self._stop.wait(RESYNC_INTERVAL_S)
            if self._stop.is_set():
                break
            try:
                self.full_resync()
                self.stats["ticks"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["last_error"] = f"tick: {e}"
                logger.warning(f"[ideas-sync] tick failed: {e}")

    # ── Stats ─────────────────────────────────────────────────────────

    def stats_dict(self) -> Dict[str, Any]:
        return {
            "interval_s": RESYNC_INTERVAL_S,
            "batch_size": BATCH_SIZE,
            "running": bool(self._worker and self._worker.is_alive()),
            **self.stats,
        }
