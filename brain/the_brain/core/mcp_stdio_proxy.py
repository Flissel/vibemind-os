"""
Brain stdio-MCP proxy.

Claude Code speaks stdio-MCP. Brain speaks HTTP on port 5000. This file
bridges the two: it starts a stdio-MCP server that forwards each tool call
to the running Brain FastAPI instance.

Design:
  - Brain must already be running on BRAIN_URL (default http://127.0.0.1:5000).
  - If Brain is offline, each tool returns a structured error instead of
    crashing the MCP connection. Claude Code stays happy.
  - No Brain internals are imported here — this file only does HTTP.

Run directly for local testing:
    python mcp_stdio_proxy.py

Or via .mcp.json:
    {
      "brain-core": {
        "type": "stdio",
        "command": "python",
        "args": [".../core/mcp_stdio_proxy.py"],
        "env": { "BRAIN_URL": "http://127.0.0.1:5000" }
      }
    }
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

import requests
from mcp.server.fastmcp import FastMCP


BRAIN_URL = os.environ.get("BRAIN_URL", "http://127.0.0.1:5000").rstrip("/")
HTTP_TIMEOUT = float(os.environ.get("BRAIN_HTTP_TIMEOUT", "120"))


mcp = FastMCP(
    "Brain Core",
    instructions=(
        "Stdio-MCP bridge to the running Brain HTTP server. "
        "Use 'think' to chat with the Brain (routes through Thalamus + "
        "MicroAgentPool + TalkerModule). Use 'brain_state' / 'bridges' / "
        "'diagnostics' for introspection. Brain must be running on "
        f"{BRAIN_URL} — start it with "
        "`python vibemind-os/brain/the_brain/start_server.py` if offline."
    ),
)


# ──────────────────────────────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────────────────────────────

def _get(path: str, timeout: Optional[float] = None) -> Dict[str, Any]:
    try:
        r = requests.get(f"{BRAIN_URL}{path}", timeout=timeout or HTTP_TIMEOUT)
        if r.status_code >= 400:
            return {"error": f"HTTP {r.status_code}", "body": r.text[:500], "url": path}
        try:
            return r.json()
        except ValueError:
            return {"text": r.text}
    except requests.exceptions.ConnectionError:
        return {"error": "brain_offline", "detail": f"cannot reach {BRAIN_URL}{path}"}
    except requests.exceptions.Timeout:
        return {"error": "timeout", "detail": f"no response from {path} within {timeout or HTTP_TIMEOUT}s"}
    except Exception as e:
        return {"error": type(e).__name__, "detail": str(e)}


def _post(path: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
    try:
        r = requests.post(
            f"{BRAIN_URL}{path}",
            json=payload,
            timeout=timeout or HTTP_TIMEOUT,
            allow_redirects=True,
        )
        if r.status_code >= 400:
            return {"error": f"HTTP {r.status_code}", "body": r.text[:500], "url": path}
        try:
            return r.json()
        except ValueError:
            return {"text": r.text}
    except requests.exceptions.ConnectionError:
        return {"error": "brain_offline", "detail": f"cannot reach {BRAIN_URL}{path}"}
    except requests.exceptions.Timeout:
        return {"error": "timeout", "detail": f"no response from {path} within {timeout or HTTP_TIMEOUT}s"}
    except Exception as e:
        return {"error": type(e).__name__, "detail": str(e)}


# ──────────────────────────────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def think(message: str) -> Dict[str, Any]:
    """Send a message to the Brain for cognitive processing.

    Routes through Thalamus → Knowledge retrieval → InternalMonologue →
    MicroAgentPool (LLM) → TalkerModule. Returns the final response plus
    routing info, confidence, and a trace of modules that ran.

    Args:
        message: The prompt / question for the Brain.
    """
    result = _post("/api/brain/chat", {"message": message})
    # Slim down for MCP response — trace can be huge
    if isinstance(result, dict) and "thought_trace" in result:
        trace = result.get("thought_trace") or []
        result["thought_trace"] = [
            {
                "module": t.get("module"),
                "category": t.get("category"),
                "content": (t.get("content") or "")[:200],
                "confidence": t.get("confidence"),
            }
            for t in trace
        ]
    return result


@mcp.tool()
def brain_state() -> Dict[str, Any]:
    """Get the Brain's current cognitive state snapshot.

    Returns the radial meta-router state including attention gain, precision
    boost, FFN throughput, threshold modulation, and consciousness level.
    """
    return _get("/api/brain/state")


@mcp.tool()
def bridges() -> Dict[str, Any]:
    """Get the state of all 10 Brain bridges.

    Returns neuromodulation, cortex, limbic, sleep_wake, motor, defense,
    memory, integration, visceral, and social bridge activations.
    """
    return _get("/api/bridges")


@mcp.tool()
def diagnostics() -> Dict[str, Any]:
    """Get a high-level Brain health report.

    Returns boolean flags for every major subsystem (brain_chat,
    continuous_thinking, agent_loop, radial_network, etc.) plus counts
    (moltbook_entries, cte_thought_count).
    """
    return _get("/api/brain/diagnostics")


@mcp.tool()
def thoughts(limit: int = 10) -> Dict[str, Any]:
    """Get the Brain's recent autonomous thoughts (ThoughtStream).

    These are the 'idle thoughts' the ContinuousThinkingEngine generates
    in the background — not responses to user input.

    Args:
        limit: Max thoughts to return (default 10, max 100).
    """
    limit = max(1, min(100, int(limit)))
    return _get(f"/api/brain/thoughts?limit={limit}")


@mcp.tool()
def llm_stats() -> Dict[str, Any]:
    """Get LLM router call statistics.

    Returns per-model call counts, failures, token usage, estimated cost,
    and success rate. Useful for checking if the Brain is actually calling
    LLMs or falling back to template responses.
    """
    return _get("/api/llm/stats")


@mcp.tool()
def llm_probe() -> Dict[str, Any]:
    """Diagnostic: is the MicroAgentPool wired + can it call an LLM right now?

    Performs a live test call with the 'responder' agent. Useful for
    debugging 'Brain gives template answers' — tells you whether the LLM
    pipeline is the problem or something downstream.
    """
    return _get("/api/llm/probe", timeout=60)


@mcp.tool()
def modulation() -> Dict[str, Any]:
    """Get the 4 composite modulation factors + consciousness level."""
    return _get("/api/modulation")


@mcp.tool()
def health() -> Dict[str, Any]:
    """Quick liveness check for the Brain HTTP server."""
    return _get("/api/health", timeout=5)


# ──────────────────────────────────────────────────────────────────────
# Knowledge Graph tools (Qdrant-backed unified KG)
# ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def kg_search(
    query: str,
    node_type: str = "",
    collection: str = "",
    limit: int = 10,
    threshold: float = 0.0,
) -> Dict[str, Any]:
    """Semantic kNN search across cognitive Brain Knowledge Graph collections.

    The KG is split into cognitive categories:
      - episodic:   thoughts, chat responses (Hippocampus-like, flüchtig)
      - semantic:   facts, concepts (consolidated knowledge)
      - procedural: spaces, events (routing/action patterns)
      - state:      snapshots (working memory)
      - artifacts:  Rowboat bubbles + ideas (external refs)

    Args:
        query: Free-text query. Multilingual (DE/EN) via Qwen3-Embedding.
        node_type: Optional node_type filter (thought|response|bubble|
            idea|space|event|snapshot|fact|concept). Auto-narrows the
            collection.
        collection: Optional logical collection (episodic|semantic|
            procedural|state|artifacts). If empty, searches all cognitive
            collections and merges by score.
        limit: Max hits (default 10, capped at 50).
        threshold: Min cosine score (0.0–1.0). 0 = no filter.

    Returns: {query, node_type, collection, count, hits: [...]}
    """
    limit = max(1, min(50, int(limit)))
    from urllib.parse import urlencode
    params = {"q": query, "limit": limit, "threshold": threshold}
    if node_type:
        params["node_type"] = node_type
    if collection:
        params["collection"] = collection
    return _get(f"/api/kg/search?{urlencode(params)}", timeout=30)


@mcp.tool()
def kg_related(point_id: str) -> Dict[str, Any]:
    """Return the bidirectional edges (linked.*) of a KG point.

    Args:
        point_id: Qdrant UUID of the point (from a previous kg_search hit).

    Returns: {point_id, node_type, content, linked: {thoughts, responses,
        bubbles, ideas, spaces, events}, ...}
    """
    from urllib.parse import urlencode
    return _get(f"/api/kg/related?{urlencode({'point_id': point_id})}")


@mcp.tool()
def kg_stats() -> Dict[str, Any]:
    """Stats about the unified Brain Knowledge Graph.

    Returns point counts per node type, edges built, errors, and the
    underlying Qdrant collection name.
    """
    return _get("/api/kg/stats")


# ──────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Log to stderr only — stdout is reserved for MCP JSON-RPC frames.
    print(f"[brain-core-mcp] proxy starting, target={BRAIN_URL}", file=sys.stderr)
    mcp.run()
