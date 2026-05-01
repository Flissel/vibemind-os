"""
Ideas-Space HTTP wrapper — Phase O.1.A.

Thin FastAPI server exposing the Ideas-Space SQLite database as REST.
Brain (and any other consumer) talks to this via plain HTTP instead of
importing the heavyweight tool layer with its global "current bubble"
state.

Endpoints
---------
GET  /api/health
GET  /api/ideas?limit=&query=&parent_id=
POST /api/ideas             {title, content?, tags?, parent_id?}
POST /api/ideas/search      {query, limit?, min_score?}
POST /api/ideas/{id}/expand {prompt?, count?}

Storage: SQLite at vibemind-os/voice/python/vibemind.db (existing DB).
Embeddings: lazy Qwen3-Embedding-0.6B (same model Brain uses).
Expand:   delegates to Brain's subagent dispatcher
          (HTTP POST /api/brain/subagent on Brain server) so we don't
          duplicate LLM-routing logic here.

Run
---
    python -m spaces.ideas.http_server
or
    python vibemind-os/spaces/ideas/http_server.py

Env
---
    IDEAS_HTTP_PORT          default 5101
    IDEAS_DB_PATH            default vibemind-os/voice/python/vibemind.db
    IDEAS_API_KEY            default empty (no auth). If set, requests
                             must send X-API-Key header.
    BRAIN_URL                default http://127.0.0.1:5000 (for expand)
    IDEAS_EMBED_MODEL        default Qwen/Qwen3-Embedding-0.6B
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make local modules (ideas_kg, ideas_sync, ...) importable
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
import requests
import uvicorn

logger = logging.getLogger("ideas-http")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("IDEAS_HTTP_PORT", "5102"))
DB_PATH = os.environ.get(
    "IDEAS_DB_PATH",
    str(Path(__file__).resolve().parent.parent.parent / "voice" / "python" / "vibemind.db"),
)
API_KEY = os.environ.get("IDEAS_API_KEY", "").strip()
BRAIN_URL = os.environ.get("BRAIN_URL", "http://127.0.0.1:5000").rstrip("/")
EMBED_MODEL_NAME = os.environ.get("IDEAS_EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    if not Path(DB_PATH).exists():
        raise RuntimeError(f"Ideas DB not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_idea(row: sqlite3.Row) -> Dict[str, Any]:
    """Serialize an ideas row to JSON. Drops big embedding blob."""
    d = dict(row)
    # tags + metadata are JSON-encoded TEXT columns
    for k in ("tags", "metadata"):
        v = d.get(k)
        if isinstance(v, str) and v:
            try:
                d[k] = json.loads(v)
            except Exception:
                pass
    # Drop embedding fields from list responses
    d.pop("embedding_vector", None)
    d.pop("embedding_hash", None)
    return d


# ---------------------------------------------------------------------------
# Embedder (singleton, lazy)
# ---------------------------------------------------------------------------

class _Embedder:
    _model = None
    _dim = None

    @classmethod
    def get(cls):
        if cls._model is None:
            t0 = time.time()
            from sentence_transformers import SentenceTransformer
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            cls._model = SentenceTransformer(EMBED_MODEL_NAME, device=device)
            cls._dim = cls._model.get_sentence_embedding_dimension()
            logger.info(
                f"[ideas] embed model {EMBED_MODEL_NAME} loaded on {device} "
                f"(dim={cls._dim}) in {time.time()-t0:.1f}s"
            )
        return cls._model

    @classmethod
    def encode(cls, texts: List[str]):
        import numpy as np
        m = cls.get()
        v = m.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(v, dtype=np.float32)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

# ideas-kg singleton — Phase Q.1
_IDEAS_KG = None
_IDEAS_SYNC = None
_IDEAS_CONS = None
_IDEAS_STATE = None
_IDEAS_MCMP = None


def get_kg():
    """Lazily build the shared IdeasKG instance."""
    global _IDEAS_KG
    if _IDEAS_KG is None:
        from ideas_kg import IdeasKG
        _IDEAS_KG = IdeasKG()
        try:
            _IDEAS_KG.ensure_collection()
            logger.info(f"[ideas] kg ready at {_IDEAS_KG.url} ({_IDEAS_KG.collection})")
        except Exception as e:
            logger.warning(f"[ideas] kg ensure_collection failed: {e}")
    return _IDEAS_KG


def get_sync():
    """Lazily build the shared IdeasSync instance."""
    global _IDEAS_SYNC
    if _IDEAS_SYNC is None:
        from ideas_sync import IdeasSync
        _IDEAS_SYNC = IdeasSync(db_path=DB_PATH, kg=get_kg())
    return _IDEAS_SYNC


def _fire_sync(idea_id: Optional[str]) -> None:
    """Fire-and-forget sync_one. Errors are swallowed and logged inside
    IdeasSync.stats — never blocks the HTTP response."""
    if not idea_id:
        return
    try:
        # Run synchronously: write-path is small (one Qdrant upsert) and
        # we want consistency before we return to caller. Sub-second.
        get_sync().sync_one(idea_id)
    except Exception as e:
        logger.debug(f"[ideas] sync_one fired but failed: {e}")


def get_consolidator():
    """Lazily build the shared IdeasConsolidator instance."""
    global _IDEAS_CONS
    if _IDEAS_CONS is None:
        from ideas_consolidator import IdeasConsolidator
        _IDEAS_CONS = IdeasConsolidator(db_path=DB_PATH, kg=get_kg())
    return _IDEAS_CONS


def get_state():
    """Lazily build the shared IdeasState instance."""
    global _IDEAS_STATE
    if _IDEAS_STATE is None:
        from ideas_state import IdeasState
        _IDEAS_STATE = IdeasState(db_path=DB_PATH, kg=get_kg())
    return _IDEAS_STATE


def get_mcmp():
    """Lazily build the shared IdeasMCMP stub (deferred until Block 2)."""
    global _IDEAS_MCMP
    if _IDEAS_MCMP is None:
        from ideas_state import IdeasMCMP
        _IDEAS_MCMP = IdeasMCMP(kg=get_kg())
    return _IDEAS_MCMP


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sanity-check DB on startup
    try:
        conn = _connect()
        cur = conn.execute("SELECT COUNT(*) FROM ideas")
        n = cur.fetchone()[0]
        conn.close()
        logger.info(f"[ideas] db ok ({n} ideas) at {DB_PATH}")
    except Exception as e:
        logger.warning(f"[ideas] db check failed: {e}")
    # Eagerly init KG so the collection exists even before first search
    try:
        get_kg()
    except Exception as e:
        logger.warning(f"[ideas] kg init deferred: {e}")
    # Start sync background loop (initial resync fires after INITIAL_RESYNC_DELAY)
    try:
        get_sync().start()
        logger.info("[ideas] sync loop started")
    except Exception as e:
        logger.warning(f"[ideas] sync deferred: {e}")
    # Start consolidator background loop (Q.3)
    try:
        get_consolidator().start()
        logger.info("[ideas] consolidator loop started")
    except Exception as e:
        logger.warning(f"[ideas] consolidator deferred: {e}")
    # Start state refresher (Q.4)
    try:
        get_state().start()
        logger.info("[ideas] state loop started")
    except Exception as e:
        logger.warning(f"[ideas] state deferred: {e}")
    # MCMP stub (Q.4 — deferred until Block 2)
    try:
        get_mcmp().start()
    except Exception as e:
        logger.debug(f"[ideas] mcmp stub: {e}")
    yield
    try:
        if _IDEAS_MCMP is not None:
            _IDEAS_MCMP.stop()
    except Exception:
        pass
    try:
        if _IDEAS_STATE is not None:
            _IDEAS_STATE.stop()
    except Exception:
        pass
    try:
        if _IDEAS_CONS is not None:
            _IDEAS_CONS.stop()
    except Exception:
        pass
    try:
        if _IDEAS_SYNC is not None:
            _IDEAS_SYNC.stop()
    except Exception:
        pass


app = FastAPI(
    title="VibeMind Ideas HTTP",
    description="Thin REST wrapper over the Ideas SQLite DB for Brain consumption.",
    version="0.2.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Auth middleware (optional)
# ---------------------------------------------------------------------------

@app.middleware("http")
async def _api_key_check(request: Request, call_next):
    if API_KEY and request.url.path != "/api/health":
        if request.headers.get("X-API-Key") != API_KEY:
            return JSONResponse({"error": "invalid api key"}, status_code=401)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    try:
        conn = _connect()
        n = conn.execute("SELECT COUNT(*) FROM ideas").fetchone()[0]
        conn.close()
        return {
            "status": "alive",
            "db": DB_PATH,
            "idea_count": n,
            "embed_model": EMBED_MODEL_NAME,
            "brain_url": BRAIN_URL,
        }
    except Exception as e:
        return JSONResponse({"status": "degraded", "error": str(e)}, status_code=503)


@app.get("/api/ideas")
def list_ideas(
    limit: int = Query(20, ge=1, le=200),
    query: Optional[str] = None,
    parent_id: Optional[str] = None,
):
    """List ideas with optional substring filter and parent scoping."""
    try:
        conn = _connect()
        conds = []
        params: List[Any] = []
        if query:
            conds.append("(LOWER(title) LIKE LOWER(?) OR LOWER(description) LIKE LOWER(?))")
            params.extend([f"%{query}%", f"%{query}%"])
        if parent_id is not None:
            if parent_id == "":
                conds.append("parent_id IS NULL")
            else:
                conds.append("parent_id = ?")
                params.append(parent_id)
        where = " AND ".join(conds) if conds else "1=1"
        sql = f"SELECT * FROM ideas WHERE {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
        conn.close()
        return {
            "count": len(rows),
            "ideas": [_row_to_idea(r) for r in rows],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ideas")
async def create_idea(request: Request):
    """Create a new idea. Body: {title, content?, tags?, parent_id?}."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)
    description = (body.get("content") or body.get("description") or "").strip()
    tags = body.get("tags") or []
    parent_id = body.get("parent_id")
    source = body.get("source") or "brain"

    iid = str(uuid.uuid4())[:8]
    try:
        conn = _connect()
        conn.execute(
            """INSERT INTO ideas
               (id, title, description, source, score, status, tags, metadata, parent_id)
               VALUES (?, ?, ?, ?, 0.0, 'raw', ?, ?, ?)""",
            (
                iid, title, description, source,
                json.dumps(tags) if tags else None,
                json.dumps(body.get("metadata") or {}),
                parent_id,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ideas WHERE id = ?", (iid,)).fetchone()
        conn.close()
        _fire_sync(iid)
        return {"ok": True, "idea": _row_to_idea(row)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ideas/search")
async def search_ideas(request: Request):
    """Semantic search via Qwen embeddings.

    Body: {query, limit?, min_score?}.
    Falls back to substring match if embedder isn't available.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    query = (body.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "query required"}, status_code=400)
    limit = max(1, min(50, int(body.get("limit") or 10)))
    min_score = float(body.get("min_score") or 0.3)

    try:
        import numpy as np
        q_vec = _Embedder.encode([query])[0]

        conn = _connect()
        rows = conn.execute(
            "SELECT id, title, description, source, status, score, tags, parent_id, "
            "embedding_vector FROM ideas"
        ).fetchall()
        conn.close()

        scored: List[Dict[str, Any]] = []
        to_embed: List[tuple] = []  # (idx, text) for rows lacking embeddings
        cached: List[Dict[str, Any]] = []
        cached_vecs: List[Any] = []

        for r in rows:
            d = dict(r)
            ev = d.get("embedding_vector")
            d.pop("embedding_vector", None)
            if ev:
                try:
                    vec = np.asarray(json.loads(ev), dtype=np.float32)
                    if vec.shape[0] == q_vec.shape[0]:
                        cached.append(d)
                        cached_vecs.append(vec)
                        continue
                except Exception:
                    pass
            text = (d.get("title") or "") + "\n" + (d.get("description") or "")
            to_embed.append((d, text))

        # Score cached
        if cached_vecs:
            mat = np.vstack(cached_vecs)
            sims = mat @ q_vec
            for d, s in zip(cached, sims.tolist()):
                if s >= min_score:
                    scored.append({**d, "score": float(s)})

        # Score newly-embedded
        if to_embed:
            texts = [t[1] for t in to_embed]
            new_vecs = _Embedder.encode(texts)
            sims = new_vecs @ q_vec
            for (d, _t), s in zip(to_embed, sims.tolist()):
                if s >= min_score:
                    scored.append({**d, "score": float(s)})

        scored.sort(key=lambda x: x["score"], reverse=True)
        for d in scored:
            t = d.get("tags")
            if isinstance(t, str) and t:
                try:
                    d["tags"] = json.loads(t)
                except Exception:
                    pass

        return {
            "count": len(scored[:limit]),
            "total_scored": len(scored),
            "query": query,
            "ideas": scored[:limit],
        }
    except Exception as e:
        logger.warning(f"[ideas] search failed: {e}")
        # Fallback: substring match
        try:
            conn = _connect()
            rows = conn.execute(
                "SELECT * FROM ideas WHERE LOWER(title) LIKE LOWER(?) "
                "OR LOWER(description) LIKE LOWER(?) LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            conn.close()
            return {
                "count": len(rows),
                "query": query,
                "fallback": "substring",
                "ideas": [_row_to_idea(r) for r in rows],
            }
        except Exception as e2:
            return JSONResponse({"error": str(e2)}, status_code=500)


@app.post("/api/ideas/{idea_id}/expand")
async def expand_idea(idea_id: str, request: Request):
    """Expand an idea via Brain's subagent dispatcher (Groq Llama 3.3 70b).

    Body: {prompt?, count?}.
    Returns the LLM text plus, if Brain returned them, structured suggestions.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    count = int(body.get("count") or 3)
    extra_prompt = (body.get("prompt") or "").strip()

    try:
        conn = _connect()
        row = conn.execute(
            "SELECT id, title, description FROM ideas WHERE id = ?", (idea_id,),
        ).fetchone()
        conn.close()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    if row is None:
        return JSONResponse({"error": f"idea '{idea_id}' not found"}, status_code=404)

    seed = dict(row)
    base = (
        f"Existing idea:\n"
        f"Title: {seed['title']}\n"
        f"Description: {seed.get('description') or '(empty)'}\n\n"
        f"Generate {count} concise related concepts that extend or sharpen "
        f"this idea. One per line. No numbering, no extra explanation. "
        f"Match the language of the input."
    )
    prompt = base + (f"\n\nUser hint: {extra_prompt}" if extra_prompt else "")

    # Delegate to Brain's subagent dispatcher
    try:
        r = requests.post(
            f"{BRAIN_URL}/api/brain/subagent",
            json={
                "tool": "groq_subagent",
                "prompt": prompt,
                "max_tokens": 400,
            },
            timeout=60,
        )
        if r.status_code != 200:
            return JSONResponse({
                "error": f"Brain subagent HTTP {r.status_code}",
                "body": r.text[:500],
            }, status_code=502)
        result = r.json()
    except Exception as e:
        return JSONResponse({
            "error": f"Brain subagent unreachable: {e}",
        }, status_code=503)

    text = (result.get("text") or "").strip()
    suggestions = [
        ln.strip(" -*•\t").strip()
        for ln in text.splitlines()
        if ln.strip()
    ][:count]

    return {
        "ok": True,
        "idea_id": idea_id,
        "seed": {"title": seed["title"], "description": seed.get("description")},
        "expansions": suggestions,
        "raw": text,
        "model": result.get("model"),
        "latency_ms": result.get("latency_ms"),
    }


# ---------------------------------------------------------------------------
# IdeasKG (Phase Q.1) — semantic index endpoints.
# ---------------------------------------------------------------------------


@app.get("/api/kg/stats")
def kg_stats():
    """Counts + status of the ideas-kg Qdrant collection."""
    try:
        return get_kg().stats()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@app.get("/api/sync/stats")
def sync_stats():
    """IdeasSync background-loop stats."""
    try:
        return get_sync().stats_dict()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@app.post("/api/sync/full")
def sync_full():
    """Trigger a full SQLite -> ideas-kg resync now (blocking).
    Useful after manual DB edits or to recover from drift."""
    try:
        return get_sync().full_resync()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Consolidation (Phase Q.3) — DBSCAN-based theme suggestions.
# ---------------------------------------------------------------------------


@app.post("/api/consolidate")
def consolidate_now():
    """Trigger one consolidation pass (DBSCAN + LLM). Blocking. Returns
    a summary; the actual suggestions live in /api/consolidate/suggestions."""
    try:
        c = get_consolidator()
        summary = c.run_once()
        return {"ok": True, "summary": summary, "stats": c.stats_dict()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/consolidate/suggestions")
def consolidate_suggestions(status: str = "pending", limit: int = 20):
    """List pending (or accepted/rejected) consolidation suggestions."""
    try:
        return {"suggestions": get_consolidator().list_suggestions(status=status, limit=limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/consolidate/suggestions/{suggestion_id}/accept")
def consolidate_accept(suggestion_id: str):
    """Accept a suggestion: create theme bubble, move members in, sync."""
    try:
        return get_consolidator().accept(suggestion_id, sync_fn=_fire_sync)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/consolidate/suggestions/{suggestion_id}/reject")
def consolidate_reject(suggestion_id: str):
    """Reject a suggestion (suppressed for future ticks)."""
    try:
        return get_consolidator().reject(suggestion_id)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/consolidate/stats")
def consolidate_stats():
    try:
        return get_consolidator().stats_dict()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


# ---------------------------------------------------------------------------
# State + MCMP (Phase Q.4) — Mini-Brain self-state for Ideas-Space.
# ---------------------------------------------------------------------------


@app.get("/api/state")
def state_snapshot():
    """Mini-Brain state of the Ideas-Space."""
    try:
        return get_state().to_dict()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@app.post("/api/state/refresh")
def state_refresh():
    """Force an immediate state refresh."""
    try:
        return {"snapshot": get_state().refresh()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ideas/{idea_id}/reward")
async def reward_idea(idea_id: str, request: Request):
    """Bump the `score` column of an idea by `delta`. Persists to SQLite
    and triggers KG sync so payload reflects new score.

    Body: {delta: float, reason?: str}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        delta = float(body.get("delta") or 0.0)
    except Exception:
        return JSONResponse({"error": "invalid delta"}, status_code=400)
    if delta == 0.0:
        return {"ok": True, "noop": True, "idea_id": idea_id}
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT id, score FROM ideas WHERE id = ?", (idea_id,),
        ).fetchone()
        if row is None:
            conn.close()
            return JSONResponse(
                {"error": f"idea '{idea_id}' not found"}, status_code=404,
            )
        new_score = float(row["score"] or 0.0) + delta
        conn.execute(
            "UPDATE ideas SET score = ? WHERE id = ?",
            (new_score, idea_id),
        )
        conn.commit()
        conn.close()
        _fire_sync(idea_id)
        return {
            "ok": True,
            "idea_id": idea_id,
            "delta": delta,
            "new_score": new_score,
            "reason": body.get("reason") or "",
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/mcmp/stats")
def mcmp_stats():
    """MCMP walker stats (deferred — only meaningful after Block 2)."""
    try:
        return get_mcmp().stats_dict()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@app.post("/api/kg/search")
async def kg_search(request: Request):
    """Direct semantic search against ideas-kg, bypassing SQLite scan.

    Body: {query, limit?, threshold?, node_type?}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    query = (body.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "query required"}, status_code=400)
    limit = max(1, min(100, int(body.get("limit") or 10)))
    threshold = float(body.get("threshold") or 0.3)
    node_type = body.get("node_type")
    try:
        hits = get_kg().search(
            query=query, limit=limit, threshold=threshold, node_type=node_type,
        )
        return {
            "query": query,
            "count": len(hits),
            "node_type": node_type,
            "hits": hits,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Bubbles (Block 1) — bubbles are top-level ideas (parent_id IS NULL).
# Children of a bubble are ideas with parent_id == bubble.id.
# ---------------------------------------------------------------------------


@app.get("/api/bubbles")
def list_bubbles(limit: int = Query(50, ge=1, le=500)):
    """List all top-level bubbles with their child counts."""
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT id, title, description, source, created_at, score, status, "
            "tags, metadata FROM ideas WHERE parent_id IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        # Child counts in one query
        counts: Dict[str, int] = {}
        for r in conn.execute(
            "SELECT parent_id, COUNT(*) as n FROM ideas "
            "WHERE parent_id IS NOT NULL GROUP BY parent_id"
        ).fetchall():
            counts[r["parent_id"]] = r["n"]
        conn.close()

        out = []
        for r in rows:
            d = _row_to_idea(r)
            d["child_count"] = int(counts.get(d.get("id"), 0))
            out.append(d)
        return {"count": len(out), "bubbles": out}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/bubbles")
async def create_bubble(request: Request):
    """Create a new top-level bubble. Body: {title, description?, tags?}."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)

    bid = str(uuid.uuid4())[:8]
    description = (body.get("description") or body.get("content") or "").strip()
    tags = body.get("tags") or []
    source = body.get("source") or "brain"

    try:
        conn = _connect()
        conn.execute(
            """INSERT INTO ideas
               (id, title, description, source, score, status, tags, metadata, parent_id)
               VALUES (?, ?, ?, ?, 0.0, 'raw', ?, ?, NULL)""",
            (
                bid, title, description, source,
                json.dumps(tags) if tags else None,
                json.dumps(body.get("metadata") or {}),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ideas WHERE id = ?", (bid,)).fetchone()
        conn.close()
        _fire_sync(bid)
        return {"ok": True, "bubble": _row_to_idea(row)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/bubbles/{bubble_id}")
def delete_bubble(bubble_id: str, force: bool = False):
    """Delete a bubble. Refuses if it has children unless `force=true`.

    With `force`, children are also deleted (cascade).
    """
    try:
        conn = _connect()
        target = conn.execute(
            "SELECT id, title, parent_id FROM ideas WHERE id = ?", (bubble_id,),
        ).fetchone()
        if target is None:
            conn.close()
            return JSONResponse(
                {"error": f"bubble '{bubble_id}' not found"}, status_code=404,
            )
        if target["parent_id"] is not None:
            conn.close()
            return JSONResponse(
                {"error": "id refers to a nested idea, not a top-level bubble"},
                status_code=400,
            )
        n_children = conn.execute(
            "SELECT COUNT(*) FROM ideas WHERE parent_id = ?", (bubble_id,),
        ).fetchone()[0]
        if n_children > 0 and not force:
            conn.close()
            return JSONResponse({
                "error": "bubble has children; use force=true to cascade",
                "child_count": int(n_children),
            }, status_code=409)
        child_ids: List[str] = []
        if force and n_children > 0:
            child_rows = conn.execute(
                "SELECT id FROM ideas WHERE parent_id = ?", (bubble_id,),
            ).fetchall()
            child_ids = [r["id"] for r in child_rows if r["id"]]
            conn.execute("DELETE FROM ideas WHERE parent_id = ?", (bubble_id,))
        conn.execute("DELETE FROM ideas WHERE id = ?", (bubble_id,))
        conn.commit()
        conn.close()
        # Mirror deletion to ideas-kg
        _fire_sync(bubble_id)
        for cid in child_ids:
            _fire_sync(cid)
        return {
            "ok": True,
            "deleted": bubble_id,
            "title": target["title"],
            "children_deleted": int(n_children) if force else 0,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ideas/{idea_id}/move")
async def move_idea(idea_id: str, request: Request):
    """Move an idea to a different parent bubble.

    Body: {parent_id} — set to null/empty string to promote to top-level.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    target_parent = body.get("parent_id")
    if target_parent == "":
        target_parent = None

    try:
        conn = _connect()
        idea = conn.execute(
            "SELECT id, title, parent_id FROM ideas WHERE id = ?", (idea_id,),
        ).fetchone()
        if idea is None:
            conn.close()
            return JSONResponse(
                {"error": f"idea '{idea_id}' not found"}, status_code=404,
            )
        if target_parent is not None:
            tgt = conn.execute(
                "SELECT id FROM ideas WHERE id = ?", (target_parent,),
            ).fetchone()
            if tgt is None:
                conn.close()
                return JSONResponse(
                    {"error": f"target bubble '{target_parent}' not found"},
                    status_code=404,
                )
            if target_parent == idea_id:
                conn.close()
                return JSONResponse(
                    {"error": "cannot move idea into itself"}, status_code=400,
                )
        conn.execute(
            "UPDATE ideas SET parent_id = ? WHERE id = ?",
            (target_parent, idea_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
        conn.close()
        _fire_sync(idea_id)
        return {
            "ok": True,
            "moved": idea_id,
            "from_parent": idea["parent_id"],
            "to_parent": target_parent,
            "idea": _row_to_idea(row),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[ideas] starting on :{PORT} db={DB_PATH}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
