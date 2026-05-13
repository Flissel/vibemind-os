"""Phase 11.U.C.9 — Async operations for the supabase: capability target.

Each operation:
  1. Resolves titles / IDs against canvas_nodes
  2. Mutates canvas_edges
  3. Publishes a brain space-event so the bridge -> Electron renderer
     sees the change live
  4. Returns a human-readable string (validator: rule:string_nonempty)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .supabase_ideas_client import SupabaseIdeasClient

logger = logging.getLogger(__name__)


# ─── intent re-extraction (Phase 11.P pattern) ────────────────────────


_CONNECT_INTENT_RE = re.compile(
    r"(?:connect|link|verbinde|verkn[uü]pfe|linke)\s+"
    r"(?:idea(?:s)?|idee(?:n)?\s+)?([\w\d_-]+)"
    r".+?(?:with|to|and|mit|und|zu)\s+"
    r"(?:idea\s+|idee\s+)?([\w\d_-]+)",
    re.IGNORECASE,
)
_DISCONNECT_INTENT_RE = re.compile(
    r"(?:disconnect|unlink|trenne|entferne)\s+"
    r"(?:the\s+|die\s+)?"
    r"(?:idea(?:s)?\s+|ideen\s+)?"
    r"([\w\d_-]+)"
    r"\s+(?:from|with|and|von|und|zu|mit)\s+"
    r"(?:idea\s+|the\s+)?([\w\d_-]+)",
    re.IGNORECASE,
)


def _extract_pair_from_params(
    params: Dict[str, Any], regex: re.Pattern,
) -> Tuple[str, str]:
    """Pull two idea titles out of the param-bag, falling back to _intent
    parsing if not enough explicit names are present. Tolerates the LLM
    planner using random key names like `idea1_name` / `source_node` /
    etc. — anything non-underscore key with a string value is a candidate.

    Phase 11.U.E — if `value` looks like a JSON dict (multi-arg arg_template
    that we JSON-encoded in plan_schema.from_dict), parse it and merge.
    """
    # Phase 0: JSON-encoded multi-arg payload
    if isinstance(params, dict):
        raw_value = params.get("value")
        if isinstance(raw_value, str) and raw_value.strip().startswith("{"):
            try:
                import json as _json
                decoded = _json.loads(raw_value)
                if isinstance(decoded, dict):
                    # Promote decoded keys to top-level params (so the rest
                    # of this function can pick them up)
                    for k, v in decoded.items():
                        params.setdefault(k, v)
            except Exception:
                pass

    # Phase 1: well-known keys (in priority order)
    known1 = (
        params.get("idea1") or params.get("source") or
        params.get("source_id") or params.get("from_idea") or
        params.get("von") or ""
    ).strip() if isinstance(params, dict) else ""
    known2 = (
        params.get("idea2") or params.get("target") or
        params.get("target_id") or params.get("to_idea") or
        params.get("zu") or ""
    ).strip() if isinstance(params, dict) else ""

    if known1 and known2:
        return known1, known2

    # Phase 2: any non-meta string param value as fallback for known1
    if not known1:
        for k, v in (params or {}).items():
            if k.startswith("_"):
                continue
            if k in {"idea1", "idea2", "source", "target", "source_id",
                     "target_id", "from_idea", "to_idea", "von", "zu"}:
                continue
            if isinstance(v, str) and v.strip():
                known1 = v.strip()
                break

    # Phase 3: re-extract from _intent text
    intent = (params.get("_intent") or "").strip() if isinstance(params, dict) else ""
    if intent:
        m = regex.search(intent)
        if m:
            a, b = m.group(1).strip(), m.group(2).strip()
            if not known1:
                return a, b
            if not known2:
                # Match the existing known1 against a/b so we pair correctly
                if a.lower() == known1.lower():
                    return known1, b
                if b.lower() == known1.lower():
                    return known1, a
                # Neither — override
                return a, b

    return known1, known2


# ─── publishing helper ────────────────────────────────────────────────


def _publish(event_id: str, params: Dict[str, Any], result: str, ok: bool) -> None:
    """Best-effort publish to the brain space-event bus. Never raises."""
    try:
        from .space_event_bus import get_bus
        bus = get_bus()
        bus.publish({
            "event_id": event_id,
            "params": params,
            "result": result,
            "ok": ok,
            "source": "supabase_ideas_ops",
        })
    except Exception as e:
        logger.debug(f"[supabase_ideas_ops] publish skipped: {e}")


# ─── operations ───────────────────────────────────────────────────────


async def connect_op(
    client: SupabaseIdeasClient, params: Dict[str, Any],
) -> str:
    title1, title2 = _extract_pair_from_params(params, _CONNECT_INTENT_RE)
    if not title1 or not title2:
        return (
            f"Need two idea names to connect. "
            f"Got: idea1={title1!r}, idea2={title2!r}"
        )
    if title1.lower() == title2.lower():
        return f"'{title1}' cannot be connected to itself."

    hits1 = await client.find_canvas_node_by_title(title1, limit=1)
    hits2 = await client.find_canvas_node_by_title(title2, limit=1)
    if not hits1:
        return f"Idea '{title1}' not found in canvas."
    if not hits2:
        return f"Idea '{title2}' not found in canvas."

    a, b = hits1[0], hits2[0]
    edge = await client.create_edge(a["id"], b["id"], edge_type="related")
    if not edge:
        return f"Failed to create connection between '{title1}' and '{title2}'."

    _publish(
        event_id="idea.connect",
        params={
            "from_id": a["id"], "to_id": b["id"],
            "from_title": a.get("title", title1),
            "to_title": b.get("title", title2),
            "label": "related",
            "edge_id": edge.get("id"),
        },
        result=f"Connected '{title1}' to '{title2}'",
        ok=True,
    )
    return f"'{a.get('title', title1)}' and '{b.get('title', title2)}' are now connected."


async def disconnect_op(
    client: SupabaseIdeasClient, params: Dict[str, Any],
) -> str:
    title1, title2 = _extract_pair_from_params(params, _DISCONNECT_INTENT_RE)
    if not title1 or not title2:
        return f"Need two idea names to disconnect. Got: idea1={title1!r}, idea2={title2!r}"

    hits1 = await client.find_canvas_node_by_title(title1, limit=1)
    hits2 = await client.find_canvas_node_by_title(title2, limit=1)
    if not hits1:
        return f"Idea '{title1}' not found in canvas."
    if not hits2:
        return f"Idea '{title2}' not found in canvas."

    a, b = hits1[0], hits2[0]
    edge = await client.find_edge_between(a["id"], b["id"])
    if edge is None:
        return f"'{title1}' and '{title2}' are not connected."

    ok = await client.delete_edge(edge["id"])
    if not ok:
        return f"Failed to remove connection between '{title1}' and '{title2}'."

    _publish(
        event_id="idea.disconnect",
        params={
            "from_id": a["id"], "to_id": b["id"],
            "from_title": a.get("title", title1),
            "to_title": b.get("title", title2),
            "edge_id": edge.get("id"),
        },
        result=f"Disconnected '{title1}' from '{title2}'",
        ok=True,
    )
    return f"Connection between '{a.get('title', title1)}' and '{b.get('title', title2)}' removed."


async def auto_link_op(
    client: SupabaseIdeasClient, params: Dict[str, Any],
) -> str:
    """Connect all nodes in a bubble that have similar titles.

    For now: pure title-based naive matching (jaccard on word-tokens).
    Embedding-based similarity is a fast follow once we wire the embedder
    cache into Brain. This still produces useful edges and validates the
    end-to-end pipeline.
    """
    bubble_arg = (
        params.get("bubble") or params.get("bubble_id") or
        params.get("bubble_name") or ""
    ).strip()
    threshold = float(params.get("threshold") or 0.30)
    max_links = int(params.get("max_links") or 20)

    logger.info(
        f"[auto_link] bubble_arg={bubble_arg!r} params_keys={list(params.keys())}"
    )

    # Resolve bubble ID. Empty bubble = scan top-level (parent_id IS NULL)
    bubble_id: Optional[str] = None
    if bubble_arg:
        bubbles = await client.list_bubbles(limit=200)
        logger.info(f"[auto_link] {len(bubbles)} bubbles found")
        for b in bubbles:
            if b.get("title", "").strip().lower() == bubble_arg.lower():
                bubble_id = b.get("id")
                break
        if bubble_id is None:
            # Maybe it's already an id
            bubble_id = bubble_arg
    logger.info(f"[auto_link] resolved bubble_id={bubble_id!r}")

    nodes = await client.list_canvas_nodes_in_bubble(bubble_id, limit=200)
    logger.info(f"[auto_link] found {len(nodes)} nodes in bubble {bubble_id!r}")
    if len(nodes) < 2:
        return f"Bubble {bubble_id!r} has {len(nodes)} nodes — need at least 2 to auto-link."

    def _tokens(title: str) -> set[str]:
        return {w for w in re.findall(r"\w+", (title or "").lower()) if len(w) > 2}

    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    # Existing edges so we skip already-connected pairs
    existing_edges = await client.list_edges(limit=500)
    existing_pairs = set()
    for e in existing_edges:
        f, t = e.get("from_node_id"), e.get("to_node_id")
        if f and t:
            existing_pairs.add(tuple(sorted([f, t])))

    candidates: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
    for i in range(len(nodes)):
        ti = _tokens(nodes[i].get("title", ""))
        for j in range(i + 1, len(nodes)):
            pair = tuple(sorted([nodes[i]["id"], nodes[j]["id"]]))
            if pair in existing_pairs:
                continue
            sim = _jaccard(ti, _tokens(nodes[j].get("title", "")))
            if sim >= threshold:
                candidates.append((sim, nodes[i], nodes[j]))

    candidates.sort(key=lambda c: c[0], reverse=True)
    candidates = candidates[:max_links]

    if not candidates:
        return (
            f"No matching pairs found (threshold {threshold:.0%}, "
            f"{len(nodes)} nodes scanned)."
        )

    created: List[Dict[str, Any]] = []
    for sim, a, b in candidates:
        edge = await client.create_edge(a["id"], b["id"], edge_type="related")
        if edge:
            created.append({
                "from_id": a["id"], "to_id": b["id"],
                "from_title": a.get("title", ""), "to_title": b.get("title", ""),
                "score": round(sim, 3),
                "edge_id": edge.get("id"),
                "label": "related",
            })

    if created:
        _publish(
            event_id="idea.auto_link",
            params={
                "bubble_id": bubble_id,
                "edges": created,
                "count": len(created),
                "threshold": threshold,
            },
            result=f"auto-linked {len(created)} edges in bubble {bubble_id}",
            ok=True,
        )

    return (
        f"Auto-linked {len(created)} connections "
        f"(threshold {threshold:.0%}, scanned {len(nodes)} nodes)."
    )
