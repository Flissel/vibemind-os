"""
IdeasConsolidator — Phase Q.3.

Periodically detects clusters of semantically-similar ideas (DBSCAN on
ideas-kg embeddings), asks Brain's groq_subagent to synthesise a
"theme" describing each cluster, and stores the result as a
**suggestion** in a small SQLite table. The user must explicitly
accept a suggestion before any bubble is actually created (User-
Decision C: "propose, user confirms").

Key principles:
  - Idempotent: same cluster → same suggestion id (hash of sorted member ids)
  - LLM is optional: if Brain offline, suggestion has empty `theme_text`
  - User actions:
      accept → creates a new bubble, moves all members in, marks suggestion `accepted`
      reject → marks suggestion `rejected` (suppressed for future ticks)

Env
---
    IDEAS_CONSOLIDATION_INTERVAL_S    default 3600  (1h)
    IDEAS_CONSOLIDATION_INITIAL_DELAY default 120
    IDEAS_MIN_CLUSTER_SIZE            default 3
    IDEAS_DBSCAN_EPS                  default 0.18
    IDEAS_MAX_NEW_PER_TICK            default 3
    IDEAS_CONSOLIDATION_ENABLED       default 1
    BRAIN_URL                         default http://127.0.0.1:5000
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


TICK_INTERVAL_S = float(os.environ.get("IDEAS_CONSOLIDATION_INTERVAL_S", "3600"))
INITIAL_DELAY_S = float(os.environ.get("IDEAS_CONSOLIDATION_INITIAL_DELAY", "120"))
MIN_CLUSTER_SIZE = int(os.environ.get("IDEAS_MIN_CLUSTER_SIZE", "3"))
DBSCAN_EPS = float(os.environ.get("IDEAS_DBSCAN_EPS", "0.18"))
MAX_NEW_PER_TICK = int(os.environ.get("IDEAS_MAX_NEW_PER_TICK", "3"))
ENABLED = os.environ.get("IDEAS_CONSOLIDATION_ENABLED", "1").lower() in ("1", "true", "yes")
BRAIN_URL = os.environ.get("BRAIN_URL", "http://127.0.0.1:5000").rstrip("/")

SYNTH_PROMPT = """Below are {n} ideas a user has captured that cluster
together semantically. Distill ONE concise theme (1-2 sentences,
present tense, German if inputs are mostly German else English) that
captures the common thread. Output ONLY the theme text, no preamble.

IDEAS:
{ideas}

THEME:"""

SUGGESTIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS consolidation_suggestions (
    id TEXT PRIMARY KEY,
    cluster_hash TEXT,
    member_ids TEXT,
    member_count INTEGER,
    theme_text TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    new_bubble_id TEXT
)
"""


def _suggestion_id(member_ids: List[str]) -> str:
    """Deterministic suggestion id derived from sorted member ids."""
    key = "|".join(sorted(member_ids))
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"sugg-{h[:16]}"


class IdeasConsolidator:
    """Background DBSCAN-based theme detector + suggestion queue."""

    def __init__(self, db_path: str, kg) -> None:
        """
        Args:
            db_path: SQLite path (same vibemind.db)
            kg: IdeasKG instance — used to scroll embeddings
        """
        self.db_path = db_path
        self.kg = kg
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.stats: Dict[str, Any] = {
            "ticks": 0,
            "ideas_scanned": 0,
            "clusters_found": 0,
            "suggestions_created": 0,
            "suggestions_refreshed": 0,
            "accepted": 0,
            "rejected": 0,
            "errors": 0,
            "last_error": None,
            "last_tick_ts": None,
            "last_run_summary": None,
        }
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        try:
            conn = self._connect()
            conn.execute(SUGGESTIONS_TABLE_DDL)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[ideas-cons] ensure_table failed: {e}")

    # ── Lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        if not ENABLED:
            logger.info("[ideas-cons] disabled via IDEAS_CONSOLIDATION_ENABLED=0")
            return
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._loop, daemon=True, name="IdeasConsolidator",
        )
        self._worker.start()
        logger.info(
            f"[ideas-cons] started (tick={TICK_INTERVAL_S}s, "
            f"min_cluster={MIN_CLUSTER_SIZE}, eps={DBSCAN_EPS})"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=5)

    def _loop(self) -> None:
        self._stop.wait(INITIAL_DELAY_S)
        while not self._stop.is_set():
            try:
                summary = self.run_once()
                self.stats["ticks"] += 1
                self.stats["last_tick_ts"] = time.time()
                self.stats["last_run_summary"] = summary
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["last_error"] = f"tick: {e}"
                logger.warning(f"[ideas-cons] tick failed: {e}")
            self._stop.wait(TICK_INTERVAL_S)

    # ── Core ─────────────────────────────────────────────────────────

    def run_once(self) -> Dict[str, Any]:
        """One consolidation pass over current ideas-kg state."""
        items, vectors = self._fetch_ideas()
        self.stats["ideas_scanned"] += len(items)
        if len(items) < MIN_CLUSTER_SIZE:
            return {
                "ideas": len(items), "clusters": 0, "suggestions": 0,
                "skip_reason": "too few ideas",
            }

        clusters = self._dbscan_cluster(vectors)
        valid = {
            lbl: idxs for lbl, idxs in clusters.items()
            if lbl != -1 and len(idxs) >= MIN_CLUSTER_SIZE
        }
        self.stats["clusters_found"] += len(valid)
        if not valid:
            return {
                "ideas": len(items), "clusters": 0, "suggestions": 0,
                "skip_reason": "no valid clusters",
            }

        cluster_items = list(valid.items())
        cluster_items.sort(key=lambda x: -len(x[1]))
        cluster_items = cluster_items[:MAX_NEW_PER_TICK]

        created = refreshed = 0
        for label, idxs in cluster_items:
            members = [items[i] for i in idxs]
            member_ids = [m["idea_id"] for m in members if m.get("idea_id")]
            if len(member_ids) < MIN_CLUSTER_SIZE:
                continue
            sid = _suggestion_id(member_ids)
            existing = self._get_suggestion(sid)
            if existing is not None and existing.get("status") in ("rejected", "accepted"):
                continue  # respect prior decisions
            theme_text = self._synthesise_theme(members) or ""
            if existing is None:
                self._insert_suggestion(sid, member_ids, theme_text)
                created += 1
                self.stats["suggestions_created"] += 1
            else:
                # Refresh theme if the cluster grew
                self._refresh_suggestion(sid, member_ids, theme_text)
                refreshed += 1
                self.stats["suggestions_refreshed"] += 1

        return {
            "ideas": len(items),
            "clusters_total": len(valid),
            "suggestions_created": created,
            "suggestions_refreshed": refreshed,
        }

    # ── Suggestion CRUD ──────────────────────────────────────────────

    def list_suggestions(
        self, status: str = "pending", limit: int = 20,
    ) -> List[Dict[str, Any]]:
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM consolidation_suggestions WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, max(1, min(200, int(limit)))),
            ).fetchall()
            conn.close()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["member_ids"] = json.loads(d.get("member_ids") or "[]")
                except Exception:
                    d["member_ids"] = []
                out.append(d)
            return out
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = f"list: {e}"
            return []

    def accept(self, suggestion_id: str, sync_fn=None) -> Dict[str, Any]:
        """Accept a suggestion: create a new bubble, move all members in.

        sync_fn: optional callable(idea_id) to sync each touched row to ideas-kg.
        """
        s = self._get_suggestion(suggestion_id)
        if s is None:
            return {"ok": False, "error": "suggestion not found"}
        if s.get("status") != "pending":
            return {"ok": False, "error": f"suggestion is {s.get('status')}"}
        try:
            member_ids = json.loads(s.get("member_ids") or "[]")
        except Exception:
            member_ids = []
        if not member_ids:
            return {"ok": False, "error": "no members"}
        title = (s.get("theme_text") or "Theme")[:80].strip() or "Theme"

        try:
            conn = self._connect()
            new_bid = str(uuid.uuid4())[:8]
            conn.execute(
                "INSERT INTO ideas (id, title, description, source, score, status, parent_id) "
                "VALUES (?, ?, ?, 'consolidator', 0.0, 'raw', NULL)",
                (new_bid, title, s.get("theme_text") or ""),
            )
            conn.execute(
                f"UPDATE ideas SET parent_id = ? "
                f"WHERE id IN ({','.join(['?'] * len(member_ids))})",
                (new_bid, *member_ids),
            )
            conn.execute(
                "UPDATE consolidation_suggestions SET status = 'accepted', "
                "resolved_at = CURRENT_TIMESTAMP, new_bubble_id = ? WHERE id = ?",
                (new_bid, suggestion_id),
            )
            conn.commit()
            conn.close()

            # Sync touched rows to ideas-kg
            if sync_fn is not None:
                try:
                    sync_fn(new_bid)
                except Exception:
                    pass
                for mid in member_ids:
                    try:
                        sync_fn(mid)
                    except Exception:
                        pass

            self.stats["accepted"] += 1
            return {
                "ok": True,
                "suggestion_id": suggestion_id,
                "new_bubble_id": new_bid,
                "moved": len(member_ids),
                "title": title,
            }
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = f"accept: {e}"
            return {"ok": False, "error": str(e)}

    def reject(self, suggestion_id: str) -> Dict[str, Any]:
        s = self._get_suggestion(suggestion_id)
        if s is None:
            return {"ok": False, "error": "suggestion not found"}
        try:
            conn = self._connect()
            conn.execute(
                "UPDATE consolidation_suggestions SET status = 'rejected', "
                "resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                (suggestion_id,),
            )
            conn.commit()
            conn.close()
            self.stats["rejected"] += 1
            return {"ok": True, "suggestion_id": suggestion_id}
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = f"reject: {e}"
            return {"ok": False, "error": str(e)}

    def _get_suggestion(self, sid: str) -> Optional[Dict[str, Any]]:
        try:
            conn = self._connect()
            r = conn.execute(
                "SELECT * FROM consolidation_suggestions WHERE id = ?", (sid,),
            ).fetchone()
            conn.close()
            return dict(r) if r else None
        except Exception:
            return None

    def _insert_suggestion(
        self, sid: str, member_ids: List[str], theme_text: str,
    ) -> None:
        try:
            conn = self._connect()
            conn.execute(
                "INSERT INTO consolidation_suggestions "
                "(id, cluster_hash, member_ids, member_count, theme_text, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending')",
                (
                    sid, sid.split("-", 1)[1] if "-" in sid else sid,
                    json.dumps(member_ids), len(member_ids), theme_text,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = f"insert: {e}"

    def _refresh_suggestion(
        self, sid: str, member_ids: List[str], theme_text: str,
    ) -> None:
        try:
            conn = self._connect()
            conn.execute(
                "UPDATE consolidation_suggestions SET member_ids = ?, "
                "member_count = ?, theme_text = ? WHERE id = ? AND status = 'pending'",
                (json.dumps(member_ids), len(member_ids), theme_text, sid),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            self.stats["last_error"] = f"refresh: {e}"

    # ── Implementation details ───────────────────────────────────────

    def _fetch_ideas(self) -> Tuple[List[Dict[str, Any]], List[List[float]]]:
        """Scroll all ideas + bubbles from ideas-kg with their semantic vectors."""
        items: List[Dict[str, Any]] = []
        vectors: List[List[float]] = []
        try:
            offset = None
            while True:
                batch, next_off = self.kg.client.scroll(
                    collection_name=self.kg.collection,
                    limit=200, offset=offset,
                    with_payload=True, with_vectors=["semantic"],
                )
                if not batch:
                    break
                for rec in batch:
                    p = rec.payload or {}
                    if not rec.vector or "semantic" not in rec.vector:
                        continue
                    iid = p.get("idea_id")
                    if not iid:
                        continue
                    items.append({
                        "pid": str(rec.id),
                        "idea_id": iid,
                        "title": p.get("title", ""),
                        "description": p.get("description", ""),
                        "parent_id": p.get("bubble_id") or None,
                    })
                    vectors.append(rec.vector["semantic"])
                if next_off is None:
                    break
                offset = next_off
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = f"fetch: {e}"
            logger.debug(f"[ideas-cons] fetch failed: {e}")
        return items, vectors

    def _dbscan_cluster(self, vectors: List[List[float]]) -> Dict[int, List[int]]:
        try:
            import numpy as np
            from sklearn.cluster import DBSCAN
            X = np.asarray(vectors, dtype=np.float32)
            db = DBSCAN(
                eps=DBSCAN_EPS, min_samples=MIN_CLUSTER_SIZE,
                metric="cosine", n_jobs=-1,
            )
            labels = db.fit_predict(X)
            clusters: Dict[int, List[int]] = {}
            for i, lbl in enumerate(labels):
                clusters.setdefault(int(lbl), []).append(i)
            return clusters
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = f"dbscan: {e}"
            logger.warning(f"[ideas-cons] DBSCAN failed: {e}")
            return {}

    def _synthesise_theme(self, members: List[Dict[str, Any]]) -> Optional[str]:
        """Ask Brain's groq_subagent for a theme. Returns None if Brain offline."""
        if not members:
            return None
        examples = members[:8]
        ideas_block = "\n".join(
            f"- {(m.get('title') or '')}: {(m.get('description') or '')[:150]}"
            for m in examples
        )
        prompt = SYNTH_PROMPT.format(n=len(members), ideas=ideas_block)
        try:
            r = requests.post(
                f"{BRAIN_URL}/api/brain/subagent",
                json={
                    "tool": "groq_subagent",
                    "prompt": prompt,
                    "max_tokens": 200,
                },
                timeout=60,
            )
            if r.status_code != 200:
                return None
            result = r.json()
            text = (result.get("text") or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return text[:500] if text else None
        except Exception as e:
            self.stats["last_error"] = f"synth: {e}"
            return None

    # ── Stats ─────────────────────────────────────────────────────────

    def stats_dict(self) -> Dict[str, Any]:
        return {
            "enabled": ENABLED,
            "interval_s": TICK_INTERVAL_S,
            "min_cluster_size": MIN_CLUSTER_SIZE,
            "dbscan_eps": DBSCAN_EPS,
            "running": bool(self._worker and self._worker.is_alive()),
            **self.stats,
        }
