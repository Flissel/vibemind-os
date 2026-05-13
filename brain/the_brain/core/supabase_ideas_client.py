"""Phase 11.U.C.7 — Brain's async Supabase REST client for ideas + edges.

Why: Brain runs in a different process from Voice, but both need to read/write
the same canvas_nodes/canvas_edges. Voice talks to Supabase via
`supabase-client.js` (Electron) and `supabase_database.py` (Voice/Python).
Brain previously had no path to this data — connection-related capabilities
(`idea_connect`, `idea_disconnect`, `idea_auto_link`) were wired to
`direct:idea_tools.connect_ideas` which fails because Brain has no
canvas_repo binding.

This module gives Brain its own minimal async path:

  client = SupabaseIdeasClient()
  hits = await client.find_canvas_node_by_title("Alpha")
  ok = await client.create_edge(from_id, to_id, "related")
  edges = await client.list_edges(from_id=..., to_id=...)
  ok = await client.delete_edge(edge_id)

Design:
- Uses the shared httpx.AsyncClient from multi_llm_router (keepalive pool)
- Supabase URL + (optional) anon-key from env: SUPABASE_URL, SUPABASE_ANON_KEY
- No supabase-py dep — plain REST
- All methods return primitive dicts/lists or None on failure
- Errors are logged, not raised; callers decide how to surface
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


SUPABASE_URL = os.environ.get("SUPABASE_URL", "http://localhost:54321").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "anon").strip()


class SupabaseIdeasClient:
    """Async REST client for canvas_nodes + canvas_edges (and ideas)."""

    def __init__(
        self,
        url: Optional[str] = None,
        anon_key: Optional[str] = None,
    ) -> None:
        self.url = (url or SUPABASE_URL).rstrip("/")
        self.key = (anon_key or SUPABASE_KEY).strip() or "anon"
        self.rest = f"{self.url}/rest/v1"
        # Stats for /api/llm/stats-style introspection
        self.stats: Dict[str, Any] = {
            "calls": 0, "errors": 0, "last_error": None,
            "edges_created": 0, "edges_deleted": 0,
        }

    def _headers(self, *, prefer: Optional[str] = None) -> Dict[str, str]:
        h = {
            "apikey": self.key,
            "Content-Type": "application/json",
        }
        # Only set Bearer when key looks like a real JWT (3 dot-separated parts).
        # Local Supabase rejects "Bearer anon" with 401 PGRST301 because it
        # tries to JWT-verify it. Plain `apikey: anon` is enough for anon RLS.
        if self.key and self.key.count(".") == 2:
            h["Authorization"] = f"Bearer {self.key}"
        if prefer:
            h["Prefer"] = prefer
        return h

    async def _request(
        self, method: str, path: str, *,
        params: Optional[Dict[str, Any]] = None,
        json: Any = None,
        prefer: Optional[str] = None,
    ) -> Optional[Any]:
        """Single REST call. Returns parsed JSON (or None on failure).

        Phase 11.U.C — uses a fresh httpx.AsyncClient per call. The
        process-wide cached client from multi_llm_router doesn't survive
        SupabaseExecutor's asyncio.run-per-call pattern (the previous loop
        closes its associated transports). Per-call clients are slower but
        correct; if we ever push enough Supabase traffic to care, we'll
        wire this onto the FastAPI event loop instead.
        """
        import httpx as _httpx
        url = f"{self.rest}{path}"
        self.stats["calls"] += 1
        try:
            async with _httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.request(
                    method, url,
                    headers=self._headers(prefer=prefer),
                    params=params or None,
                    json=json,
                )
            if resp.status_code >= 400:
                body = ""
                try:
                    body = resp.text[:300]
                except Exception:
                    pass
                self.stats["errors"] += 1
                self.stats["last_error"] = f"HTTP {resp.status_code}: {body}"
                logger.warning(f"[supabase] {method} {path} -> {resp.status_code} {body}")
                return None
            if resp.status_code == 204 or not resp.content:
                return True  # DELETE / no-body success
            return resp.json()
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = f"{type(e).__name__}: {e}"
            logger.warning(f"[supabase] {method} {path} crash: {e}")
            return None

    # ── canvas_nodes ──────────────────────────────────────────────────────

    async def find_canvas_node_by_title(
        self, title: str, *, limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Case-insensitive prefix + contains match. Returns ranked hits.

        Strategy: exact (case-insensitive) first; if none, ilike contains.
        Caller picks the top one (or asks the user to disambiguate).
        """
        t = (title or "").strip()
        if not t:
            return []
        # Exact case-insensitive: ilike with no wildcards
        exact = await self._request(
            "GET", "/canvas_nodes",
            params={
                "select": "id,title,linked_idea_id",
                "title": f"ilike.{t}",
                "limit": str(limit),
            },
        )
        if exact:
            return exact
        # Fall back to substring match
        sub = await self._request(
            "GET", "/canvas_nodes",
            params={
                "select": "id,title,linked_idea_id",
                "title": f"ilike.*{t}*",
                "limit": str(limit),
            },
        )
        return sub or []

    async def get_canvas_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        hits = await self._request(
            "GET", "/canvas_nodes",
            params={"select": "*", "id": f"eq.{node_id}", "limit": "1"},
        )
        return hits[0] if hits else None

    async def list_canvas_nodes_in_bubble(
        self, bubble_id: Optional[str], *, limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List nodes in a given bubble (linked_idea_id=bubble_id). If
        bubble_id is None, returns all top-level nodes (linked_idea_id IS NULL)."""
        params = {"select": "id,title,linked_idea_id", "limit": str(limit)}
        if bubble_id:
            params["linked_idea_id"] = f"eq.{bubble_id}"
        else:
            params["linked_idea_id"] = "is.null"
        return await self._request("GET", "/canvas_nodes", params=params) or []

    # ── canvas_edges ──────────────────────────────────────────────────────

    async def list_edges(
        self, *,
        from_id: Optional[str] = None,
        to_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"select": "*", "limit": str(limit)}
        if from_id:
            params["from_node_id"] = f"eq.{from_id}"
        if to_id:
            params["to_node_id"] = f"eq.{to_id}"
        return await self._request("GET", "/canvas_edges", params=params) or []

    async def find_edge_between(
        self, a_id: str, b_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Find an edge between a and b regardless of direction."""
        # Supabase REST `or` filter: or=(...)
        params = {
            "select": "*",
            "or": (
                f"(and(from_node_id.eq.{a_id},to_node_id.eq.{b_id}),"
                f"and(from_node_id.eq.{b_id},to_node_id.eq.{a_id}))"
            ),
            "limit": "1",
        }
        hits = await self._request("GET", "/canvas_edges", params=params)
        return (hits or [None])[0]

    async def create_edge(
        self, from_id: str, to_id: str, edge_type: str = "related",
    ) -> Optional[Dict[str, Any]]:
        """Insert an edge. Returns the created row or None on failure."""
        if not from_id or not to_id:
            return None
        if from_id == to_id:
            logger.warning(f"[supabase] refusing self-loop {from_id}")
            return None
        # Pre-check: edge already exists?
        existing = await self.find_edge_between(from_id, to_id)
        if existing:
            return existing
        edge = {
            "id": uuid.uuid4().hex,
            "from_node_id": from_id,
            "to_node_id": to_id,
            "edge_type": edge_type,
        }
        result = await self._request(
            "POST", "/canvas_edges",
            json=edge, prefer="return=representation",
        )
        if result:
            self.stats["edges_created"] += 1
            return result[0] if isinstance(result, list) and result else edge
        return None

    async def delete_edge(self, edge_id: str) -> bool:
        ok = await self._request(
            "DELETE", "/canvas_edges",
            params={"id": f"eq.{edge_id}"},
        )
        if ok is not None and ok is not False:
            self.stats["edges_deleted"] += 1
            return True
        return False

    async def delete_edge_between(self, a_id: str, b_id: str) -> int:
        """Delete any edge between a and b. Returns number deleted."""
        edge = await self.find_edge_between(a_id, b_id)
        if edge is None:
            return 0
        ok = await self.delete_edge(edge["id"])
        return 1 if ok else 0

    # ── ideas (bubble-level) ──────────────────────────────────────────────

    async def list_bubbles(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        return await self._request(
            "GET", "/ideas",
            params={
                "select": "id,title,parent_id",
                "parent_id": "is.null",
                "limit": str(limit),
            },
        ) or []
