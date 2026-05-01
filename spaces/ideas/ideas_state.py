"""
IdeasState — Phase Q.4.

Lightweight self-state for the Ideas-Space mini-brain. Tracks:
  - active bubbles (recent CRUD activity)
  - stale ideas (no update in N days)
  - global counters (idea_count, bubble_count, recent_creates_24h)
  - consolidation tick info (mirrored from Konsolidator)

The MCMP-walker is part of this module too but **deferred** — its
activation requires `linked.*` edges in payloads, which only show up
once Block 2 (Connections) is implemented. Today the walker is
instantiated but `start()` is a no-op unless `MCMP_ENABLED=1`.

Env
---
    IDEAS_STATE_TICK_INTERVAL_S    default 60
    IDEAS_STALE_AFTER_DAYS         default 14
    IDEAS_MCMP_ENABLED             default 0  (off until Block 2)
    IDEAS_MCMP_TICK_INTERVAL_S     default 60
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


STATE_TICK_INTERVAL_S = float(os.environ.get("IDEAS_STATE_TICK_INTERVAL_S", "60"))
STALE_AFTER_DAYS = float(os.environ.get("IDEAS_STALE_AFTER_DAYS", "14"))
MCMP_ENABLED = os.environ.get("IDEAS_MCMP_ENABLED", "0").lower() in ("1", "true", "yes")
MCMP_TICK_INTERVAL_S = float(os.environ.get("IDEAS_MCMP_TICK_INTERVAL_S", "60"))


class IdeasState:
    """In-memory state snapshot, refreshed periodically from SQLite."""

    def __init__(self, db_path: str, kg) -> None:
        self.db_path = db_path
        self.kg = kg
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.snapshot: Dict[str, Any] = {
            "idea_count": 0,
            "bubble_count": 0,
            "recent_creates_24h": 0,
            "stale_idea_count": 0,
            "active_bubble_ids": [],
            "stale_idea_ids": [],
            "last_refresh_ts": None,
            "kg_points": None,
        }
        self.stats: Dict[str, Any] = {
            "ticks": 0,
            "errors": 0,
            "last_error": None,
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._loop, daemon=True, name="IdeasState",
        )
        self._worker.start()
        logger.info(f"[ideas-state] started (tick={STATE_TICK_INTERVAL_S}s)")

    def stop(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=5)

    def _loop(self) -> None:
        # Refresh once immediately
        try:
            self.refresh()
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = str(e)
        while not self._stop.is_set():
            self._stop.wait(STATE_TICK_INTERVAL_S)
            if self._stop.is_set():
                break
            try:
                self.refresh()
                self.stats["ticks"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["last_error"] = f"refresh: {e}"

    # ── Refresh ───────────────────────────────────────────────────────

    def refresh(self) -> Dict[str, Any]:
        """Re-read counters from SQLite + KG. Cheap, sub-100ms typical."""
        with self._lock:
            stale_cutoff = time.time() - STALE_AFTER_DAYS * 86400
            recent_cutoff_iso = self._iso(time.time() - 86400)

            try:
                conn = self._connect()

                # Counts
                idea_count = conn.execute(
                    "SELECT COUNT(*) FROM ideas WHERE parent_id IS NOT NULL"
                ).fetchone()[0]
                bubble_count = conn.execute(
                    "SELECT COUNT(*) FROM ideas WHERE parent_id IS NULL"
                ).fetchone()[0]

                # Recent creates (last 24h)
                recent = conn.execute(
                    "SELECT COUNT(*) FROM ideas WHERE created_at >= ?",
                    (recent_cutoff_iso,),
                ).fetchone()[0]

                # Active bubbles: those with at least one child created in
                # last 24h. Cheaper proxy than tracking real activity events.
                active_rows = conn.execute(
                    "SELECT DISTINCT parent_id FROM ideas "
                    "WHERE parent_id IS NOT NULL AND created_at >= ?",
                    (recent_cutoff_iso,),
                ).fetchall()
                active_bubbles = [r["parent_id"] for r in active_rows if r["parent_id"]]

                # Stale ideas: created long ago, score 0, status raw
                stale_rows = conn.execute(
                    "SELECT id FROM ideas WHERE parent_id IS NOT NULL "
                    "AND COALESCE(score, 0) = 0 AND status = 'raw' "
                    "AND created_at <= ? LIMIT 200",
                    (self._iso(stale_cutoff),),
                ).fetchall()
                stale_ids = [r["id"] for r in stale_rows if r["id"]]

                conn.close()

                kg_points = None
                try:
                    s = self.kg.stats()
                    kg_points = s.get("points")
                except Exception:
                    pass

                self.snapshot = {
                    "idea_count": int(idea_count),
                    "bubble_count": int(bubble_count),
                    "recent_creates_24h": int(recent),
                    "stale_idea_count": len(stale_ids),
                    "active_bubble_ids": active_bubbles[:50],
                    "stale_idea_ids": stale_ids[:50],
                    "last_refresh_ts": time.time(),
                    "kg_points": kg_points,
                }
                return dict(self.snapshot)
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["last_error"] = f"refresh: {e}"
                logger.warning(f"[ideas-state] refresh failed: {e}")
                return dict(self.snapshot)

    @staticmethod
    def _iso(ts: float) -> str:
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot": dict(self.snapshot),
            "stats": dict(self.stats),
            "config": {
                "tick_interval_s": STATE_TICK_INTERVAL_S,
                "stale_after_days": STALE_AFTER_DAYS,
            },
            "mcmp_enabled": MCMP_ENABLED,
        }


# ──────────────────────────────────────────────────────────────────────
# IdeasMCMP — deferred Pheromone walker on ideas-kg.
# Hooks reserved; activation requires linked.* edges (Block 2).
# ──────────────────────────────────────────────────────────────────────


class IdeasMCMP:
    """Per-Agent MCMP walker stub. Activates when MCMP_ENABLED=1 AND
    `linked.*` edges exist in the ideas-kg payloads. Until then, calls
    to start() are no-ops (logged once)."""

    def __init__(self, kg) -> None:
        self.kg = kg
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self.stats: Dict[str, Any] = {
            "ticks": 0,
            "walks": 0,
            "steps": 0,
            "activation_bumps": 0,
            "errors": 0,
            "last_error": None,
            "active": False,
        }

    def start(self) -> None:
        if not MCMP_ENABLED:
            logger.info(
                "[ideas-mcmp] deferred — set IDEAS_MCMP_ENABLED=1 after Block 2 "
                "(connections) is in place"
            )
            return
        if self._worker and self._worker.is_alive():
            return
        # Real MCMP implementation lives here once edges exist.
        # For now we just mark the stub as enabled but inactive.
        self.stats["active"] = False
        logger.info("[ideas-mcmp] enabled but no edges yet — staying idle")

    def stop(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=2)

    def stats_dict(self) -> Dict[str, Any]:
        return {
            "enabled": MCMP_ENABLED,
            "tick_interval_s": MCMP_TICK_INTERVAL_S,
            **self.stats,
        }
