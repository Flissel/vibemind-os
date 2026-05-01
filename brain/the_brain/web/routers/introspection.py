"""
Introspection Router -- FastAPI endpoints for brain self-monitoring (Default Mode Network).

Extracted from the old brain_dashboard_server.py (~30+ routes).  Covers:
  - Brain state / gates / activation / strategies
  - Emotional / homeostatic / memory state (graceful fallback)
  - Cognitive loop / agent loop state (graceful fallback)
  - Health sub-endpoints (components, dependencies, readiness, liveness)
  - Frequency controller
  - Monitoring / observability (metrics, audit trail, loop traces, error rates, heatmap)
  - Goals / evolution / CTM / cognitive status
  - Causal / meta / federated subsystems
  - LLM stats
  - Heartbeat / consciousness / neuromodulation proxies
  - Conversation monitoring / simulation
  - Advanced learning health
  - Sensory extract / predict path

All state lives on ``request.app.state.<module>`` attributes.
Every route gracefully handles ``module is None`` (testing mode) —
no HTTP proxying to localhost:5003.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def convert_numpy(obj: Any) -> Any:
    """Convert NumPy types to native Python types for JSON serialisation."""
    try:
        import numpy as np
    except ImportError:
        return obj

    if isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, (np.bool_,)):
        return bool(obj)
    elif isinstance(obj, dict):
        return {k: convert_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_numpy(i) for i in obj]
    return obj



# ===================================================================
# Group 1 — Brain State  (meta_router, brain_monitor, strategy_lib)
# ===================================================================

@router.get("/api/brain/state")
async def brain_state(request: Request):
    """Current brain routing state."""
    mr = request.app.state.meta_router
    if mr is None:
        return JSONResponse({
            "state": None,
            "message": "meta_router not initialized",
            "timestamp": time.time(),
        })
    try:
        state = mr.get_state()
        return JSONResponse({
            "state": convert_numpy(state),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "state": None,
            "error": str(exc),
            "timestamp": time.time(),
        })


@router.get("/api/brain/gates")
async def brain_gates(request: Request):
    """Current gate values from BrainActivityMonitor."""
    bm = request.app.state.brain_monitor
    if bm is None:
        return JSONResponse({
            "gates": None,
            "message": "brain_monitor not initialized",
            "timestamp": time.time(),
        })
    try:
        gates = bm.get_gates()
        return JSONResponse({
            "gates": convert_numpy(gates),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "gates": None,
            "error": str(exc),
            "timestamp": time.time(),
        })


@router.get("/api/brain/activation")
async def brain_activation(request: Request):
    """Activation map from BrainActivityMonitor."""
    bm = request.app.state.brain_monitor
    if bm is None:
        return JSONResponse({
            "activation": None,
            "message": "brain_monitor not initialized",
            "timestamp": time.time(),
        })
    try:
        activation = bm.get_activation()
        return JSONResponse({
            "activation": convert_numpy(activation),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "activation": None,
            "error": str(exc),
            "timestamp": time.time(),
        })


@router.get("/api/brain/strategies")
async def brain_strategies(request: Request):
    """Available strategies from StrategyLibrary."""
    sl = request.app.state.strategy_lib
    if sl is None:
        return JSONResponse({
            "strategies": None,
            "message": "strategy_lib not initialized",
            "timestamp": time.time(),
        })
    try:
        strategies = sl.list_strategies()
        return JSONResponse({
            "strategies": convert_numpy(strategies),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "strategies": None,
            "error": str(exc),
            "timestamp": time.time(),
        })


@router.get("/api/brain/interventions")
async def brain_interventions(request: Request):
    """Recent interventions from LiveBrainMonitor."""
    lm = request.app.state.live_monitor
    if lm is None:
        return JSONResponse({
            "interventions": [],
            "message": "live_monitor not initialized",
            "timestamp": time.time(),
        })
    try:
        interventions = lm.get_interventions()
        return JSONResponse({
            "interventions": convert_numpy(interventions),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "interventions": [],
            "error": str(exc),
            "timestamp": time.time(),
        })


# ===================================================================
# Group 2 — Proxy-replacement routes (graceful fallbacks)
# ===================================================================

@router.get("/api/brain/cognitive_loop")
async def cognitive_loop(request: Request):
    """Cognitive loop state — graceful fallback when unified brain is not wired."""
    return JSONResponse({
        "enabled": False,
        "state": None,
        "message": "cognitive loop not connected to unified brain",
        "timestamp": time.time(),
    })


@router.get("/api/brain/agent_loop_state")
async def agent_loop_state(request: Request):
    """Agent loop state — reads live AgentLoop if wired, else graceful fallback."""
    al = getattr(request.app.state, "agent_loop", None)
    if al is None:
        return JSONResponse({
            "enabled": False,
            "state": None,
            "message": "agent loop not connected to unified brain",
            "timestamp": time.time(),
        })
    try:
        get_state = getattr(al, "get_state", None) or getattr(al, "state", None)
        if callable(get_state):
            state_snapshot = get_state()
        else:
            state_snapshot = {
                "state": getattr(al, "_state", None) or str(getattr(al, "state", "unknown")),
                "tick_count": getattr(al, "_tick_count", None),
                "has_radial": getattr(al, "radial_network", None) is not None,
                "has_seed_encoder": getattr(al, "seed_encoder", None) is not None,
                "has_experience_buffer": getattr(al, "experience_buffer", None) is not None,
            }
        return JSONResponse({
            "enabled": True,
            "state": convert_numpy(state_snapshot),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "enabled": True,
            "state": None,
            "error": str(exc),
            "timestamp": time.time(),
        })


@router.post("/api/brain/agent_loop/submit")
async def agent_loop_submit(request: Request):
    """Submit task to agent loop — 503 until unified brain is wired."""
    return JSONResponse(
        {"error": "agent loop not initialized", "timestamp": time.time()},
        status_code=503,
    )


@router.get("/api/brain/emotional_state")
async def emotional_state(request: Request):
    """Emotional state — graceful fallback."""
    return JSONResponse({
        "enabled": False,
        "state": None,
        "message": "emotional state not connected to unified brain",
        "timestamp": time.time(),
    })


@router.get("/api/brain/homeostatic_state")
async def homeostatic_state(request: Request):
    """Homeostatic state — graceful fallback."""
    return JSONResponse({
        "enabled": False,
        "state": None,
        "message": "homeostatic state not connected to unified brain",
        "timestamp": time.time(),
    })


@router.get("/api/brain/memory_state")
async def memory_state(request: Request):
    """Memory state — graceful fallback."""
    return JSONResponse({
        "enabled": False,
        "state": None,
        "message": "memory state not connected to unified brain",
        "timestamp": time.time(),
    })


@router.get("/api/brain/heartbeat_status")
async def heartbeat_status(request: Request):
    """Heartbeat status — graceful fallback."""
    return JSONResponse({
        "active": False,
        "message": "heartbeat not connected to unified brain",
        "timestamp": time.time(),
    })


@router.post("/api/brain/sensory_extract")
async def sensory_extract(request: Request):
    """Sensory extract — graceful fallback."""
    return JSONResponse({
        "enabled": False,
        "message": "sensory extract not connected to unified brain",
        "timestamp": time.time(),
    })


@router.get("/api/brain/goal_graph_state")
async def goal_graph_state(request: Request):
    """Goal graph state — graceful fallback."""
    return JSONResponse({
        "enabled": False,
        "state": None,
        "message": "goal graph not connected to unified brain",
        "timestamp": time.time(),
    })


@router.get("/api/brain/neuromodulation_state")
async def neuromodulation_state(request: Request):
    """Neuromodulation state — graceful fallback."""
    return JSONResponse({
        "enabled": False,
        "state": None,
        "message": "neuromodulation not connected to unified brain",
        "timestamp": time.time(),
    })


@router.get("/api/brain/consciousness_state")
async def consciousness_state(request: Request):
    """Consciousness state — graceful fallback."""
    return JSONResponse({
        "enabled": False,
        "state": None,
        "message": "consciousness module not connected to unified brain",
        "timestamp": time.time(),
    })


# ===================================================================
# Group 3 — Monitoring & Observability
# ===================================================================

@router.get("/api/brain/metrics")
async def brain_metrics(request: Request):
    """Prometheus-style metrics — returns plain text."""
    return PlainTextResponse(
        "# Metrics unavailable — unified brain not connected\n",
        media_type="text/plain",
    )


@router.get("/api/brain/metrics_json")
async def brain_metrics_json(request: Request):
    """JSON metrics — graceful fallback."""
    return JSONResponse({
        "error": "metrics unavailable",
        "timestamp": time.time(),
    })


@router.get("/api/brain/audit_trail")
async def audit_trail(request: Request):
    """Recent audit trail entries."""
    return JSONResponse({
        "recent": [],
        "stats": {},
        "timestamp": time.time(),
    })


@router.get("/api/brain/loop_traces")
async def loop_traces(request: Request):
    """Cognitive loop traces."""
    return JSONResponse({
        "recent_traces": [],
        "phase_stats": {},
        "total_traces": 0,
        "timestamp": time.time(),
    })


@router.get("/api/brain/error_rates")
async def error_rates(request: Request):
    """Error rates by component."""
    return JSONResponse({
        "error_rates": {},
        "recent_errors": [],
        "timestamp": time.time(),
    })


@router.get("/api/brain/heatmap")
async def heatmap(request: Request):
    """Modality activation heatmap data."""
    return JSONResponse({
        "heatmap": {
            "modalities": [],
            "matrix": [],
        },
        "modality_averages": {},
        "timestamp": time.time(),
    })


# ===================================================================
# Group 4 — Frequency Controller
# ===================================================================

@router.get("/api/brain/frequency")
async def brain_frequency(request: Request):
    """Current brain frequency state."""
    fc = request.app.state.frequency_controller
    if fc is None:
        return JSONResponse({
            "frequency": None,
            "message": "frequency_controller not initialized",
            "timestamp": time.time(),
        })
    try:
        state = fc.get_state()
        return JSONResponse({
            "frequency": convert_numpy(state),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "frequency": None,
            "error": str(exc),
            "timestamp": time.time(),
        })


@router.post("/api/brain/frequency/set")
async def brain_frequency_set(request: Request):
    """Set brain frequency parameters."""
    fc = request.app.state.frequency_controller
    if fc is None:
        return JSONResponse({
            "ok": False,
            "message": "frequency_controller not initialized",
            "timestamp": time.time(),
        })
    try:
        body = await request.json()
        # Whitelist accepted keys to avoid parameter injection
        allowed = {"mode", "band", "target_hz", "ramp_time", "activation", "suppress_others"}
        filtered = {k: v for k, v in body.items() if k in allowed}
        result = fc.set_frequency(**filtered)
        return JSONResponse({
            "ok": True,
            "result": convert_numpy(result),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "ok": False,
            "error": str(exc),
            "timestamp": time.time(),
        })


@router.get("/api/brain/frequency/bands")
async def brain_frequency_bands(request: Request):
    """Available frequency bands."""
    fc = request.app.state.frequency_controller
    if fc is None:
        return JSONResponse({
            "bands": None,
            "message": "frequency_controller not initialized",
            "timestamp": time.time(),
        })
    try:
        bands = fc.get_bands()
        return JSONResponse({
            "bands": convert_numpy(bands),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "bands": None,
            "error": str(exc),
            "timestamp": time.time(),
        })


@router.get("/api/brain/frequency/markers")
async def brain_frequency_markers(request: Request):
    """Frequency event markers."""
    fc = request.app.state.frequency_controller
    if fc is None:
        return JSONResponse({
            "markers": None,
            "message": "frequency_controller not initialized",
            "timestamp": time.time(),
        })
    try:
        markers = fc.get_markers()
        return JSONResponse({
            "markers": convert_numpy(markers),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "markers": None,
            "error": str(exc),
            "timestamp": time.time(),
        })


# ===================================================================
# Group 5 — Health (system-level)
# ===================================================================

@router.get("/api/health/components")
async def health_components(request: Request):
    """Health of individual brain components."""
    state = request.app.state
    components = {
        "meta_router": state.meta_router is not None,
        "brain_monitor": state.brain_monitor is not None,
        "strategy_lib": state.strategy_lib is not None,
        "live_monitor": state.live_monitor is not None,
        "path_planner": state.path_planner is not None,
        "llm_router": state.llm_router is not None,
        "frequency_controller": state.frequency_controller is not None,
        "oscillator": state.oscillator is not None,
        "checkpoint_manager": state.checkpoint_manager is not None,
        "swarm_orchestrator": state.swarm_orchestrator is not None,
    }
    total = len(components)
    healthy = sum(1 for v in components.values() if v)
    return JSONResponse({
        "components": components,
        "healthy": healthy,
        "total": total,
        "status": "healthy" if healthy == total else ("degraded" if healthy > 0 else "not_initialized"),
        "timestamp": time.time(),
    })


@router.get("/api/health/dependencies")
async def health_dependencies(request: Request):
    """External dependency health."""
    return JSONResponse({
        "dependencies": {
            "unified_brain": False,
            "memory_api": False,
            "llm_service": False,
        },
        "message": "dependency checks not yet wired",
        "timestamp": time.time(),
    })


@router.get("/api/health/readiness")
async def health_readiness(request: Request):
    """Kubernetes-style readiness probe."""
    return JSONResponse({
        "ready": True,
        "timestamp": time.time(),
    })


@router.get("/api/health/liveness")
async def health_liveness(request: Request):
    """Kubernetes-style liveness probe."""
    return JSONResponse({
        "alive": True,
        "timestamp": time.time(),
    })


# ===================================================================
# Group 6 — LLM Stats
# ===================================================================

@router.get("/api/llm/probe")
async def llm_probe(request: Request):
    """Diagnostic probe: is MicroAgentPool wired + can it call LLM?"""
    state = request.app.state
    pool = getattr(state, "micro_agent_pool", None)
    init_error = getattr(state, "micro_agent_pool_error", None)
    bc = getattr(state, "brain_chat", None)
    bc_pool = getattr(bc, "_micro_agent_pool", None) if bc else None

    result = {
        "state_pool_present": pool is not None,
        "brain_chat_pool_present": bc_pool is not None,
        "pool_same_instance": (pool is bc_pool) if (pool and bc_pool) else False,
        "init_error": init_error,
    }
    if pool is not None:
        result["pool_router_present"] = pool._router is not None
        result["pool_agents"] = list(pool._agents.keys()) if hasattr(pool, "_agents") else []
        result["pool_total_runs"] = getattr(pool, "_total_runs", None)
        result["pool_total_failures"] = getattr(pool, "_total_failures", None)
        # Try a live call with the responder
        try:
            txt = pool._call_agent("responder", "Say the word PONG and nothing else.")
            result["live_responder_call"] = txt[:200] if txt else None
            result["live_ok"] = bool(txt)
        except Exception as exc:
            result["live_responder_call"] = None
            result["live_ok"] = False
            result["live_error"] = str(exc)
    return JSONResponse(result)


@router.get("/api/llm/stats")
async def llm_stats(request: Request):
    """LLM routing statistics."""
    lr = request.app.state.llm_router
    if lr is None:
        return JSONResponse(
            {"error": "llm_router not initialized", "timestamp": time.time()},
            status_code=503,
        )
    try:
        stats = lr.get_statistics()
        return JSONResponse({
            "stats": convert_numpy(stats),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "stats": None,
            "error": str(exc),
            "timestamp": time.time(),
        })


# ===================================================================
# Group 6b — Knowledge Graph (Qdrant-backed unified KG)
# ===================================================================

@router.get("/api/kg/stats")
async def kg_stats(request: Request):
    """Stats about the unified knowledge graph."""
    kg = getattr(request.app.state, "qdrant_kg", None)
    if kg is None:
        return JSONResponse(
            {"error": "qdrant_kg not initialized", "timestamp": time.time()},
            status_code=503,
        )
    try:
        from core.qdrant_kg import COLLECTIONS
        per_coll: Dict[str, Any] = {}
        total = 0
        for logical, name in COLLECTIONS.items():
            try:
                info = kg.client.get_collection(name)
                per_coll[logical] = {
                    "qdrant_name": name,
                    "points_count": info.points_count,
                }
                total += info.points_count
            except Exception as e:
                per_coll[logical] = {
                    "qdrant_name": name,
                    "points_count": 0,
                    "error": str(e),
                }
        return JSONResponse({
            "collections": per_coll,
            "total_points": total,
            "stats": convert_numpy(dict(kg.stats)),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "error": str(exc), "timestamp": time.time(),
        }, status_code=500)


@router.get("/api/kg/search")
async def kg_search(
    request: Request, q: str, node_type: str = "", collection: str = "",
    limit: int = 10, threshold: float = 0.0,
):
    """Semantic kNN search across cognitive collections.

    Query params:
        q: text query (multilingual via Qwen)
        node_type: optional filter (thought/response/bubble/idea/space/event/snapshot)
        collection: optional logical collection name
            (episodic|semantic|procedural|state|artifacts). If empty,
            searches all cognitive collections and merges by score.
        limit: max hits
        threshold: min cosine score
    """
    kg = getattr(request.app.state, "qdrant_kg", None)
    if kg is None:
        return JSONResponse(
            {"error": "qdrant_kg not initialized", "timestamp": time.time()},
            status_code=503,
        )
    try:
        nt = node_type or None
        coll = collection or None
        hits = kg.search(q, node_type=nt, collection=coll,
                         limit=int(limit), score_threshold=float(threshold))
        return JSONResponse({
            "query": q, "node_type": nt, "collection": coll,
            "count": len(hits),
            "hits": convert_numpy(hits), "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "error": str(exc), "timestamp": time.time(),
        }, status_code=500)


@router.get("/api/kg/route")
async def kg_route(
    request: Request, q: str, limit: int = 3, threshold: float = 0.3,
):
    """Replace space_routing_head.pt / event_routing_head.pt with graph
    kNN search.

    Returns top-k spaces AND top-k events with scores. Brain can blend
    these into its Thalamus routing priors. Every successful route can
    later bump activation_strength on the chosen space/event point,
    yielding usage-weighted routing without gradient descent.
    """
    kg = getattr(request.app.state, "qdrant_kg", None)
    if kg is None:
        return JSONResponse(
            {"error": "qdrant_kg not initialized", "timestamp": time.time()},
            status_code=503,
        )
    try:
        spaces = kg.search(q, node_type="space", limit=int(limit),
                           score_threshold=float(threshold))
        events = kg.search(q, node_type="event", limit=int(limit),
                           score_threshold=float(threshold))
        # Normalize: show only id, score, title for a clean routing payload
        def _trim(hits, id_key):
            return [{
                "id": h["payload"].get(id_key) or h["id"],
                "score": h["score"],
                "title": h["payload"].get("title", ""),
                "target_space": h["payload"].get("target_space"),
            } for h in hits]
        return JSONResponse({
            "query": q,
            "spaces": _trim(spaces, "space_id"),
            "events": _trim(events, "event_id"),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "error": str(exc), "timestamp": time.time(),
        }, status_code=500)


@router.post("/api/kg/confirm_route")
async def kg_confirm_route(request: Request):
    """Bump activation_strength on a chosen space/event after a route
    actually worked. Body: {"kind": "space|event", "id": "<external_id>",
    "delta": 1.0}. No gradient descent — just usage-weighted priors that
    rise over time and make future kNN searches gravitate toward
    historically successful choices.
    """
    kg = getattr(request.app.state, "qdrant_kg", None)
    if kg is None:
        return JSONResponse(
            {"error": "qdrant_kg not initialized"}, status_code=503,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    kind = body.get("kind") or ""
    ext_id = body.get("id") or ""
    delta = float(body.get("delta", 1.0))
    if kind not in ("space", "event") or not ext_id:
        return JSONResponse({
            "error": "need kind=space|event and id",
        }, status_code=400)
    try:
        import hashlib, uuid as _uuid
        # Spaces + events both live in the procedural collection.
        coll_name = kg.collection_for(kind)
        pid = str(_uuid.UUID(
            hashlib.sha256(ext_id.encode("utf-8")).hexdigest()[:32]
        ))
        rec = kg.client.retrieve(
            collection_name=coll_name, ids=[pid], with_payload=True,
        )
        if not rec:
            return JSONResponse({
                "error": f"no point for {kind}:{ext_id} in {coll_name}",
            }, status_code=404)
        payload = rec[0].payload or {}
        current = float(payload.get("activation_strength", 0.0) or 0.0)
        new_val = current + delta
        kg.client.set_payload(
            collection_name=coll_name,
            payload={"activation_strength": new_val},
            points=[pid],
        )
        return JSONResponse({
            "kind": kind, "id": ext_id, "collection": coll_name,
            "prev": current, "new": new_val, "delta": delta,
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "error": str(exc), "timestamp": time.time(),
        }, status_code=500)


@router.get("/api/kg/related")
async def kg_related(request: Request, point_id: str):
    """Return the linked.* edges of a point by its Qdrant UUID."""
    kg = getattr(request.app.state, "qdrant_kg", None)
    if kg is None:
        return JSONResponse(
            {"error": "qdrant_kg not initialized", "timestamp": time.time()},
            status_code=503,
        )
    try:
        from core.qdrant_kg import COLLECTIONS
        # Scan all cognitive collections until we find the point.
        rec = None
        found_coll = None
        for logical, name in COLLECTIONS.items():
            try:
                r = kg.client.retrieve(
                    collection_name=name, ids=[point_id], with_payload=True,
                )
                if r:
                    rec = r
                    found_coll = logical
                    break
            except Exception:
                continue
        if not rec:
            return JSONResponse({
                "point_id": point_id, "found": False,
                "timestamp": time.time(),
            })
        payload = rec[0].payload or {}
        return JSONResponse({
            "collection": found_coll,
            "point_id": point_id, "found": True,
            "node_type": payload.get("node_type"),
            "content": (payload.get("content") or "")[:500],
            "linked": payload.get("linked") or {},
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "error": str(exc), "timestamp": time.time(),
        }, status_code=500)


@router.post("/api/brain/subagent")
async def brain_subagent(request: Request):
    """Dispatch a focused subtask to an LLM subagent (Claude or Groq).

    Body: {
      "tool": "claude_subagent" | "groq_subagent" (default: claude_subagent)
      "prompt": "<the actual task>",
      "system": "<optional system prompt>",
      "model": "<override model id>",
      "max_tokens": <int>,
      "temperature": <float>
    }

    Returns: {ok, tool, model, text, latency_ms, error?}
    """
    disp = getattr(request.app.state, "subagent_dispatcher", None)
    if disp is None:
        return JSONResponse(
            {"error": "subagent_dispatcher not available"}, status_code=503,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    tool = body.get("tool") or "claude_subagent"
    prompt = body.get("prompt") or ""
    if not prompt:
        return JSONResponse({"error": "prompt required"}, status_code=400)
    kwargs = {k: v for k, v in body.items() if k not in ("tool",)}
    try:
        result = disp.dispatch(tool, **kwargs)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({
            "ok": False, "tool": tool, "error": str(exc),
        }, status_code=500)


@router.get("/api/brain/subagent/stats")
async def brain_subagent_stats(request: Request):
    """Stats about subagent dispatcher (calls per tool, failures, etc.)."""
    disp = getattr(request.app.state, "subagent_dispatcher", None)
    if disp is None:
        return JSONResponse(
            {"enabled": False, "message": "subagent_dispatcher not running"},
        )
    return JSONResponse({
        "enabled": True,
        "stats": convert_numpy(dict(disp.stats)),
        "timestamp": time.time(),
    })


@router.post("/api/brain/dispatch")
async def brain_dispatch(request: Request):
    """Dispatch a task to one or more Minibook agents.

    Body: {
      "project_id": "<uuid, optional - default VibeMind Collaboration>",
      "agents": ["vibemind_ideas", ...],
      "intent": "what should they do",
      "task_spec": {...optional structured payload...}
    }

    Returns: {post_id, project_id, agents, online, ts}
    Brain can later poll comments via the same minibook_client.
    """
    DEFAULT_PROJECT = "46daa2f8-6f39-4cde-87c3-c95235bfb557"  # VibeMind Collaboration
    mb = getattr(request.app.state, "minibook_client", None)
    if mb is None:
        al = getattr(request.app.state, "agent_loop", None)
        mb = getattr(al, "minibook_client", None) if al is not None else None
    if mb is None:
        return JSONResponse(
            {"error": "minibook_client not available"}, status_code=503,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    project_id = body.get("project_id") or DEFAULT_PROJECT
    agents = body.get("agents") or []
    intent = body.get("intent") or ""
    task_spec = body.get("task_spec")
    if not agents or not intent:
        return JSONResponse({
            "error": "need agents (list) and intent (str)",
        }, status_code=400)
    try:
        post_id = mb.dispatch_task(
            project_id=project_id,
            target_agents=agents,
            intent=intent,
            task_spec=task_spec,
        )
        return JSONResponse({
            "post_id": post_id,
            "project_id": project_id,
            "agents": agents,
            "online": getattr(mb, "is_online", None),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "error": str(exc), "timestamp": time.time(),
        }, status_code=500)


@router.get("/api/brain/dispatch/{post_id}/comments")
async def brain_dispatch_comments(request: Request, post_id: str):
    """Retrieve agent replies on a previously dispatched task."""
    mb = getattr(request.app.state, "minibook_client", None)
    if mb is None:
        al = getattr(request.app.state, "agent_loop", None)
        mb = getattr(al, "minibook_client", None) if al is not None else None
    if mb is None:
        return JSONResponse(
            {"error": "minibook_client not available"}, status_code=503,
        )
    try:
        comments = mb.get_comments(post_id)
        return JSONResponse({
            "post_id": post_id,
            "count": len(comments),
            "comments": comments,
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "error": str(exc), "timestamp": time.time(),
        }, status_code=500)


@router.post("/api/kg/consolidate")
async def kg_consolidate_now(request: Request):
    """Trigger one consolidation pass (episodic -> semantic). Returns summary."""
    ce = getattr(request.app.state, "consolidation_engine", None)
    if ce is None:
        return JSONResponse(
            {"error": "consolidation_engine not running"}, status_code=503,
        )
    try:
        summary = ce.run_once()
        return JSONResponse({
            "ok": True,
            "summary": convert_numpy(summary),
            "stats": convert_numpy(dict(ce.stats)),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "ok": False, "error": str(exc), "timestamp": time.time(),
        }, status_code=500)


@router.get("/api/kg/consolidation_stats")
async def kg_consolidation_stats(request: Request):
    """Stats about the consolidation engine."""
    ce = getattr(request.app.state, "consolidation_engine", None)
    if ce is None:
        return JSONResponse(
            {"enabled": False, "message": "consolidation_engine not running"},
        )
    return JSONResponse({
        "enabled": True,
        "stats": convert_numpy(dict(ce.stats)),
        "timestamp": time.time(),
    })


@router.post("/api/kg/snapshot")
async def kg_snapshot_now(request: Request):
    """Capture one Brain self-state snapshot into brain-state. Returns snapshot_id."""
    se = getattr(request.app.state, "snapshot_engine", None)
    if se is None:
        return JSONResponse(
            {"error": "snapshot_engine not running"}, status_code=503,
        )
    try:
        sid = se.snapshot_now()
        return JSONResponse({
            "ok": sid is not None,
            "snapshot_id": sid,
            "stats": convert_numpy(dict(se.stats)),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "ok": False, "error": str(exc), "timestamp": time.time(),
        }, status_code=500)


@router.get("/api/kg/snapshot_stats")
async def kg_snapshot_stats(request: Request):
    """Stats about the snapshot engine."""
    se = getattr(request.app.state, "snapshot_engine", None)
    if se is None:
        return JSONResponse(
            {"enabled": False, "message": "snapshot_engine not running"},
        )
    return JSONResponse({
        "enabled": True,
        "stats": convert_numpy(dict(se.stats)),
        "timestamp": time.time(),
    })


@router.get("/api/kg/snapshots")
async def kg_list_snapshots(request: Request, limit: int = 20):
    """List recent snapshots (newest first), payload-only (no vectors)."""
    kg = getattr(request.app.state, "qdrant_kg", None)
    if kg is None:
        return JSONResponse({"error": "qdrant_kg not available"}, status_code=503)
    try:
        from core.qdrant_kg import COLLECTIONS
        from qdrant_client.http import models as qm
        coll = COLLECTIONS["state"]
        limit = max(1, min(200, int(limit)))
        # Scroll, then sort by created_at desc client-side
        batch, _ = kg.client.scroll(
            collection_name=coll, limit=500, with_payload=True, with_vectors=False,
        )
        rows = []
        for rec in batch:
            p = rec.payload or {}
            if p.get("node_type") != "snapshot":
                continue
            rows.append({
                "point_id": str(rec.id),
                "snapshot_id": p.get("snapshot_id"),
                "ts": p.get("ts"),
                "created_at": p.get("created_at"),
                "content": p.get("content", "")[:300],
                "modulation": p.get("modulation"),
                "state_summary": p.get("state_summary"),
            })
        rows.sort(key=lambda r: r.get("ts") or 0, reverse=True)
        return JSONResponse({
            "count": len(rows[:limit]),
            "total": len(rows),
            "snapshots": rows[:limit],
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/tribe/predict")
async def tribe_predict(request: Request):
    """TriBE v2 text -> cortical activation -> bridge levels.

    Body: {"text": "<some text>"}
    Returns: {ok, text, rois, bridges, shape, latency_ms}

    If TriBE weights aren't available (gated Llama-3.2-3B still
    awaiting Meta approval) and TRIBE_DUMMY=1, a deterministic
    pseudo-vector is used so downstream wiring stays testable.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "need 'text'"}, status_code=400)
    try:
        from core.tribe_encoder import TribeEncoder
        enc = TribeEncoder.get()
        t0 = time.time()
        vec = enc.predict(text)
        dt = (time.time() - t0) * 1000
        if vec is None:
            return JSONResponse({
                "ok": False,
                "error": enc.stats.get("last_error")
                         or enc._load_error
                         or "predict returned None",
                "status": enc.status(),
            }, status_code=503)
        rois = enc.aggregate_roi(vec)
        bridges = enc.bridge_levels(vec)
        return JSONResponse({
            "ok": True,
            "text": text[:500],
            "shape": [int(vec.shape[0])],
            "rois": {k: round(float(v), 6) for k, v in rois.items()},
            "bridges": {k: round(float(v), 6) for k, v in bridges.items()},
            "latency_ms": round(dt, 2),
            "mode": enc.stats.get("last_mode", "real"),
        })
    except Exception as exc:
        return JSONResponse({
            "ok": False, "error": str(exc),
        }, status_code=500)


@router.get("/api/tribe/status")
async def tribe_status(request: Request):
    """Diagnostic: is TriBE loaded / what's the last error / stats."""
    try:
        from core.tribe_encoder import TribeEncoder
        enc = TribeEncoder.get()
        return JSONResponse(enc.status())
    except Exception as exc:
        return JSONResponse({"enabled": False, "error": str(exc)})


@router.get("/api/brain/auto_dispatch_stats")
async def auto_dispatch_stats(request: Request):
    """Stats about Phase F.4 AutoDispatcher (BrainChat -> Minibook)."""
    ad = getattr(request.app.state, "auto_dispatcher", None)
    if ad is None:
        return JSONResponse(
            {"enabled": False, "message": "auto_dispatcher not wired"},
        )
    return JSONResponse({
        "enabled": True,
        "stats": convert_numpy(dict(ad.stats)),
        "timestamp": time.time(),
    })


# ──────────────────────────────────────────────────────────────────────
# Ideas-Space proxy (Phase O.1) — Brain forwards to local Ideas HTTP
# wrapper at port 5102 via state.ideas_client.
# ──────────────────────────────────────────────────────────────────────


def _ideas(request: Request):
    return getattr(request.app.state, "ideas_client", None)


@router.get("/api/ideas/health")
async def ideas_health(request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse(
            {"enabled": False, "message": "ideas_client not wired"},
            status_code=503,
        )
    return JSONResponse(ic.health())


@router.get("/api/ideas/list")
async def ideas_list(
    request: Request,
    limit: int = 20,
    query: str = "",
    parent_id: str = "",
):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    return JSONResponse(ic.list_ideas(
        limit=limit,
        query=query or None,
        parent_id=parent_id if parent_id != "" else None,
    ))


@router.post("/api/ideas/create")
async def ideas_create(request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)
    return JSONResponse(ic.create_idea(
        title=title,
        content=body.get("content") or body.get("description") or "",
        tags=body.get("tags"),
        parent_id=body.get("parent_id"),
        source=body.get("source") or "brain",
    ))


@router.post("/api/ideas/search")
async def ideas_search(request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    q = (body.get("query") or "").strip()
    if not q:
        return JSONResponse({"error": "query required"}, status_code=400)
    return JSONResponse(ic.search_ideas(
        query=q,
        limit=int(body.get("limit") or 10),
        min_score=float(body.get("min_score") or 0.3),
    ))


@router.post("/api/ideas/{idea_id}/expand")
async def ideas_expand(idea_id: str, request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return JSONResponse(ic.expand_idea(
        idea_id=idea_id,
        prompt=body.get("prompt"),
        count=int(body.get("count") or 3),
    ))


# Bubbles (Block 1)


@router.get("/api/bubbles/list")
async def bubbles_list(request: Request, limit: int = 50):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    return JSONResponse(ic.list_bubbles(limit=limit))


@router.post("/api/bubbles/create")
async def bubbles_create(request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)
    return JSONResponse(ic.create_bubble(
        title=title,
        description=body.get("description") or body.get("content") or "",
        tags=body.get("tags"),
        source=body.get("source") or "brain",
    ))


@router.delete("/api/bubbles/{bubble_id}")
async def bubbles_delete(request: Request, bubble_id: str, force: bool = False):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    return JSONResponse(ic.delete_bubble(bubble_id, force=force))


@router.post("/api/ideas/{idea_id}/move")
async def ideas_move(idea_id: str, request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    parent_id = body.get("parent_id")
    if parent_id == "":
        parent_id = None
    return JSONResponse(ic.move_idea(idea_id=idea_id, parent_id=parent_id))


# Phase Q.5 — Brain-side proxies for Ideas mini-brain endpoints


@router.get("/api/ideas/kg_stats")
async def ideas_kg_stats(request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    return JSONResponse(ic.kg_stats())


@router.post("/api/ideas/kg_search")
async def ideas_kg_search(request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    q = (body.get("query") or "").strip()
    if not q:
        return JSONResponse({"error": "query required"}, status_code=400)
    return JSONResponse(ic.kg_search(
        query=q,
        limit=int(body.get("limit") or 10),
        threshold=float(body.get("threshold") or 0.3),
        node_type=body.get("node_type"),
    ))


@router.get("/api/ideas/state")
async def ideas_state(request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    return JSONResponse(ic.state())


@router.post("/api/ideas/sync_full")
async def ideas_sync_full(request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    return JSONResponse(ic.sync_full())


@router.post("/api/ideas/consolidate")
async def ideas_consolidate(request: Request):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    return JSONResponse(ic.consolidate_now())


@router.get("/api/ideas/consolidate/suggestions")
async def ideas_consolidate_suggestions(
    request: Request, status: str = "pending", limit: int = 20,
):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    return JSONResponse(ic.consolidate_suggestions(status=status, limit=limit))


@router.post("/api/ideas/consolidate/suggestions/{suggestion_id}/accept")
async def ideas_consolidate_accept(request: Request, suggestion_id: str):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    return JSONResponse(ic.consolidate_accept(suggestion_id))


@router.post("/api/ideas/consolidate/suggestions/{suggestion_id}/reject")
async def ideas_consolidate_reject(request: Request, suggestion_id: str):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    return JSONResponse(ic.consolidate_reject(suggestion_id))


@router.post("/api/ideas/{idea_id}/reward")
async def ideas_reward(request: Request, idea_id: str):
    ic = _ideas(request)
    if ic is None:
        return JSONResponse({"error": "ideas_client not wired"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    return JSONResponse(ic.record_reward(
        idea_id=idea_id,
        delta=float(body.get("delta") or 0.0),
        reason=body.get("reason") or "",
    ))


# ─────────────────────────────────────────────────────────────────────
# Phase R — Self-Discourse + Aggregator + Mirofish-KG-Sync
# ─────────────────────────────────────────────────────────────────────


@router.get("/api/discourse/stats")
async def discourse_stats(request: Request):
    de = getattr(request.app.state, "discourse_engine", None)
    if de is None:
        return JSONResponse({"enabled": False, "message": "discourse_engine not running"})
    return JSONResponse(de.stats_dict())


@router.post("/api/discourse/tick_now")
async def discourse_tick_now(request: Request):
    de = getattr(request.app.state, "discourse_engine", None)
    if de is None:
        return JSONResponse({"error": "discourse_engine not running"}, status_code=503)
    return JSONResponse(de.tick_once())


@router.post("/api/discourse/pause")
async def discourse_pause(request: Request):
    """Pause idle + response loops. Intent on-demand still works.
    Useful while Mirofish-Sim is being set up to stop interview-poll spam."""
    de = getattr(request.app.state, "discourse_engine", None)
    if de is None:
        return JSONResponse({"error": "discourse_engine not running"}, status_code=503)
    de.pause()
    return JSONResponse({"ok": True, "paused": True})


@router.post("/api/discourse/resume")
async def discourse_resume(request: Request):
    de = getattr(request.app.state, "discourse_engine", None)
    if de is None:
        return JSONResponse({"error": "discourse_engine not running"}, status_code=503)
    de.resume()
    return JSONResponse({"ok": True, "paused": False})


@router.get("/api/discourse/aggregate_stats")
async def discourse_agg_stats(request: Request):
    agg = getattr(request.app.state, "discourse_aggregator", None)
    if agg is None:
        return JSONResponse({"enabled": False, "message": "aggregator not running"})
    return JSONResponse(agg.stats_dict())


@router.post("/api/discourse/aggregate_now")
async def discourse_aggregate_now(request: Request):
    agg = getattr(request.app.state, "discourse_aggregator", None)
    if agg is None:
        return JSONResponse({"error": "aggregator not running"}, status_code=503)
    return JSONResponse(agg.tick_once())


@router.get("/api/mirofish/sync_stats")
async def mirofish_sync_stats(request: Request):
    mfs = getattr(request.app.state, "mirofish_kg_sync", None)
    if mfs is None:
        return JSONResponse({"enabled": False, "message": "mirofish_kg_sync not running"})
    return JSONResponse(mfs.stats_dict())


@router.post("/api/mirofish/sync_now")
async def mirofish_sync_now(request: Request):
    mfs = getattr(request.app.state, "mirofish_kg_sync", None)
    if mfs is None:
        return JSONResponse({"error": "mirofish_kg_sync not running"}, status_code=503)
    return JSONResponse(mfs.tick_once())


# ─────────────────────────────────────────────────────────────────────
# Phase S.4 — Self-Awareness Watcher
# ─────────────────────────────────────────────────────────────────────


@router.get("/api/self_awareness/manifest_stats")
async def self_awareness_stats(request: Request):
    saw = getattr(request.app.state, "self_awareness_watcher", None)
    if saw is None:
        return JSONResponse({"enabled": False, "message": "watcher not running"})
    return JSONResponse(saw.stats_dict())


@router.post("/api/self_awareness/reseed")
async def self_awareness_reseed(request: Request):
    """Trigger an immediate self-awareness reseed pass.
    Returns {checked, unchanged, updated, added, removed}."""
    saw = getattr(request.app.state, "self_awareness_watcher", None)
    if saw is None:
        return JSONResponse({"error": "watcher not running"}, status_code=503)
    return JSONResponse(saw.tick_once())


# ─────────────────────────────────────────────────────────────────────
# Phase S.5 — Discourse Memory Consolidator (cross-session)
# ─────────────────────────────────────────────────────────────────────


@router.get("/api/discourse/meta_stats")
async def discourse_meta_stats(request: Request):
    dmc = getattr(request.app.state, "discourse_memory_consolidator", None)
    if dmc is None:
        return JSONResponse({"enabled": False, "message": "consolidator not running"})
    return JSONResponse(dmc.stats_dict())


@router.post("/api/discourse/meta_consolidate_now")
async def discourse_meta_consolidate_now(request: Request):
    """Force one cross-session meta-consolidation pass over aggregated-kg.
    Clusters topics, synthesises meta_topics. Returns delta-dict."""
    dmc = getattr(request.app.state, "discourse_memory_consolidator", None)
    if dmc is None:
        return JSONResponse({"error": "consolidator not running"}, status_code=503)
    return JSONResponse(dmc.run_once())


@router.post("/api/self_awareness/recall")
async def self_awareness_recall(request: Request):
    """Recall meta_topics + topics from aggregated-kg matching the query.
    Body: {"query": "...", "days": 7, "limit": 10}"""
    dmc = getattr(request.app.state, "discourse_memory_consolidator", None)
    if dmc is None:
        return JSONResponse({"error": "consolidator not running"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        body = {}
    query = body.get("query") or ""
    days = int(body.get("days") or 7)
    limit = int(body.get("limit") or 10)
    if not query.strip():
        return JSONResponse({"error": "query required"}, status_code=400)
    return JSONResponse(dmc.recall(query, days=days, limit=limit))


@router.get("/api/fungus/stats")
async def fungus_stats(request: Request):
    """Phase S.3 — fungus client status: online flag, doc count, query
    counter, last error."""
    fc = getattr(request.app.state, "fungus_client", None)
    if fc is None:
        return JSONResponse({"online": False, "message": "fungus_client not initialised"})
    return JSONResponse(fc.stats_dict())


@router.get("/api/capabilities/stats")
async def capability_stats(request: Request):
    """Phase 1 — capability router state: registry size, match counters,
    list of loaded capabilities."""
    cr = getattr(request.app.state, "capability_router", None)
    if cr is None:
        return JSONResponse({
            "loaded": False,
            "message": "capability_router not initialised",
        })
    return JSONResponse({"loaded": True, **cr.stats_dict()})


@router.get("/api/capabilities/list")
async def capability_list(request: Request):
    """Phase 1 — show all loaded capabilities with their primary/supporting
    agents and execution targets, useful for debugging registry-rot."""
    cr = getattr(request.app.state, "capability_router", None)
    if cr is None:
        return JSONResponse({"loaded": False, "capabilities": []})
    return JSONResponse({"loaded": True, "capabilities": cr.list_capabilities()})


@router.post("/api/capabilities/test")
async def capability_test(request: Request):
    """Phase 1 — test the router without running discourse. Body:
    {"intent": "..."}. Returns the match (or no-match) so the YAML
    registry can be debugged without hitting the full discourse stack."""
    cr = getattr(request.app.state, "capability_router", None)
    if cr is None:
        return JSONResponse({"error": "capability_router not loaded"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    intent = (body.get("intent") or body.get("message") or "").strip()
    if not intent:
        return JSONResponse({"error": "intent required"}, status_code=400)
    m = cr.route(intent)
    if m is None:
        return JSONResponse({"matched": False, "intent": intent})
    return JSONResponse({
        "matched": True,
        "intent": intent,
        "capability": m.capability,
        "description": m.description,
        "primary": m.primary_names,
        "supporting": m.supporting_names,
        "matched_pattern": m.matched_pattern,
        "match_method": m.match_method,
        "is_direct": m.is_direct,
        "execution_target": m.execution_target,
    })


@router.get("/api/self_awareness/state")
async def self_awareness_state(request: Request):
    """One-shot snapshot of Brain's self-awareness layer:
    - substrate (S.1): how many concepts in brain-semantic with self_awareness=True
    - watcher (S.4): tick stats + manifest size
    - aggregated (S.5 input): topics/findings/decisions counts
    - meta_topics (S.5 output): how many cross-session themes exist
    - last self-aware tweet preview from DiscourseEngine
    """
    out: Dict[str, Any] = {"timestamp": time.time()}

    # 1. Substrate count via Qdrant
    kg = getattr(request.app.state, "qdrant_kg", None)
    if kg is not None:
        try:
            from qdrant_client.http import models as qm
            from core.qdrant_kg import COLLECTIONS
            sem_coll = COLLECTIONS.get("semantic")
            agg_coll = COLLECTIONS.get("aggregated")

            substrate = kg.client.count(
                collection_name=sem_coll,
                count_filter=qm.Filter(must=[
                    qm.FieldCondition(
                        key="self_awareness", match=qm.MatchValue(value=True),
                    ),
                ]),
                exact=True,
            ).count
            out["substrate_concepts"] = substrate

            # 2. Aggregated counts per node-type
            for nt in ("topic", "finding", "decision", "meta_topic"):
                try:
                    c = kg.client.count(
                        collection_name=agg_coll,
                        count_filter=qm.Filter(must=[
                            qm.FieldCondition(
                                key="node_type", match=qm.MatchValue(value=nt),
                            ),
                        ]),
                        exact=True,
                    ).count
                    out[f"aggregated_{nt}_count"] = c
                except Exception:
                    out[f"aggregated_{nt}_count"] = None
        except Exception as e:
            out["kg_error"] = str(e)

    # 3. Watcher stats (S.4)
    saw = getattr(request.app.state, "self_awareness_watcher", None)
    if saw is not None:
        out["watcher"] = saw.stats_dict()
    else:
        out["watcher"] = {"enabled": False}

    # 4. Meta-consolidator stats (S.5)
    dmc = getattr(request.app.state, "discourse_memory_consolidator", None)
    if dmc is not None:
        out["meta_consolidator"] = dmc.stats_dict()
    else:
        out["meta_consolidator"] = {"enabled": False}

    # 5. Last self-aware discourse tweet
    de = getattr(request.app.state, "discourse_engine", None)
    if de is not None:
        s = de.stats_dict()
        out["discourse_engine"] = {
            "running": s.get("running"),
            "ticks": s.get("ticks"),
            "tweets_posted": s.get("tweets_posted"),
            "last_tweet_preview": s.get("last_tweet_preview"),
            "agents_loaded": s.get("agents_loaded"),
        }

    # 6. Manifest path + last seed time
    try:
        from pathlib import Path
        import json as _json
        mf = (Path(__file__).resolve().parent.parent.parent
              / "data" / "self_awareness_manifest.json")
        if mf.exists():
            data = _json.loads(mf.read_text(encoding="utf-8"))
            out["manifest"] = {
                "path": str(mf),
                "last_full_seed_at": data.get("last_full_seed_at"),
                "last_checked_at": data.get("last_checked_at"),
                "source_count": len(data.get("sources") or {}),
            }
    except Exception as e:
        out["manifest_error"] = str(e)

    return JSONResponse(out)


# ─────────────────────────────────────────────────────────────────────
# Phase R+ — Three-Mode Discourse (Intent + Response triggers)
# ─────────────────────────────────────────────────────────────────────


@router.post("/api/discourse/intent")
async def discourse_intent(request: Request):
    """Trigger an Intent-Mode discourse round.

    Body: {"message": "...", "context": {...}, "auto_dispatch": true}

    Runs all 26 phi3-clones in parallel against the user intent. Returns
    the aggregator's decision JSON. If `auto_dispatch=true` (default) AND
    the decision confidence ≥ threshold, also fires a real OpenFang call
    to the chosen primary agent and includes its answer in the response.

    Returns:
        {
          "decision": {primary, supporting, risks, confidence, reasoning},
          "tweet_count": int,
          "high_confidence": bool,
          "dispatched": {agent_id, agent_name, response, ...} | null,
        }
    """
    de = getattr(request.app.state, "discourse_engine", None)
    if de is None:
        return JSONResponse({"error": "discourse_engine not running"},
                            status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    msg = (body.get("message") or "").strip()
    if not msg:
        return JSONResponse({"error": "message required"}, status_code=400)
    auto = bool(body.get("auto_dispatch", True))
    ctx = body.get("context") or {}

    # Run the discourse — synchronous; can take 30-60s
    discourse = de.tick_intent(msg, ctx)

    out = {
        "ok":              bool(discourse.get("ok", True)),
        "decision":        discourse.get("decision") or {},
        "tweets":          discourse.get("tweets") or [],
        "tweet_count":     discourse.get("tweet_count", 0),
        "high_confidence": bool(discourse.get("high_confidence")),
        "dispatched":      None,
        # Phase 1 capability routing fields — None when no match
        "capability":      discourse.get("capability"),
        "matched_pattern": discourse.get("matched_pattern"),
        "agents_targeted": discourse.get("agents_targeted"),
        "agents_total":    discourse.get("agents_total"),
        # Phase 1.5 direct-execution fields — None for normal broadcast path
        "is_direct":        discourse.get("is_direct"),
        "direct_target":    discourse.get("direct_target"),
        "direct_elapsed_s": discourse.get("direct_elapsed_s"),
        "direct_error":     discourse.get("direct_error"),
        "result":           discourse.get("result"),
    }

    # Confidence-aware dispatch (R+.8) — Mode A in plan
    if auto and discourse.get("high_confidence"):
        decision = discourse.get("decision") or {}
        primary = decision.get("primary")
        if primary:
            dispatched = _dispatch_to_openfang(primary, msg, decision)
            out["dispatched"] = dispatched

    return JSONResponse(out)


def _dispatch_to_openfang(
    agent_name: str, task: str, decision: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Find the agent in OpenFang by name (resolve UUID), POST the task,
    return the agent's response payload."""
    import os
    import requests
    of_url = os.environ.get("OPENFANG_URL", "http://127.0.0.1:4200").rstrip("/")
    try:
        r = requests.get(f"{of_url}/api/agents", timeout=10)
        agents = r.json() if r.ok else []
        # Prefer non-phi3 (Sonnet) for actual execution
        target = None
        norm = (agent_name or "").lower().rstrip("-phi3")
        for a in agents:
            n = (a.get("name") or "").lower()
            if n == norm or n == agent_name.lower():
                target = a
                break
        if target is None:
            return {"error": f"agent '{agent_name}' not found in OpenFang"}
        agent_id = target.get("id")
        # Compose context: include supporting + risks for the agent
        supporting = decision.get("supporting") or []
        risks = decision.get("risks") or []
        context_parts = [f"Task: {task[:1500]}"]
        if supporting:
            context_parts.append(f"Supporting agents flagged: {', '.join(supporting)}")
        if risks:
            context_parts.append(
                "Risks raised by other agents:\n" +
                "\n".join(f"  - {r}" for r in risks[:5])
            )
        composed = "\n\n".join(context_parts)
        r = requests.post(
            f"{of_url}/api/agents/{agent_id}/message",
            json={"message": composed[:60000], "sender_name": "Brain"},
            timeout=600,
        )
        if not r.ok:
            return {"error": f"openfang HTTP {r.status_code}",
                    "body": r.text[:300]}
        return {
            "agent_id":  agent_id,
            "agent_name": target.get("name"),
            "response":  ((r.json() or {}).get("response") or "")[:5000],
            "input_tokens":  (r.json() or {}).get("input_tokens"),
            "output_tokens": (r.json() or {}).get("output_tokens"),
            "iterations":    (r.json() or {}).get("iterations"),
            "cost_usd":      (r.json() or {}).get("cost_usd"),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@router.post("/api/discourse/response")
async def discourse_response(request: Request):
    """Manually queue a Brain-response for the next Mode-3 tick.

    Body: {"response_text": "..."}
    Returns: {"queued": true, "queue_depth": N}
    """
    de = getattr(request.app.state, "discourse_engine", None)
    if de is None:
        return JSONResponse({"error": "discourse_engine not running"},
                            status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    text = (body.get("response_text") or "").strip()
    if not text:
        return JSONResponse({"error": "response_text required"}, status_code=400)
    de.queue_response(text, body.get("context") or {})
    return JSONResponse({
        "queued": True,
        "queue_depth": len(getattr(de, "_response_queue", []) or []),
    })


@router.post("/api/discourse/response_tick_now")
async def discourse_response_tick_now(request: Request):
    """Force one Mode-3 tick. Pulls oldest from queue (if any) and asks
    3-5 random agents to assess."""
    de = getattr(request.app.state, "discourse_engine", None)
    if de is None:
        return JSONResponse({"error": "discourse_engine not running"},
                            status_code=503)
    return JSONResponse(de.tick_response())


@router.get("/api/discourse/intent_decisions")
async def discourse_intent_decisions(request: Request, limit: int = 10):
    """Last N intent-mode decisions (in-memory ring buffer)."""
    de = getattr(request.app.state, "discourse_engine", None)
    if de is None:
        return JSONResponse({"error": "discourse_engine not running"},
                            status_code=503)
    return JSONResponse({
        "count": len(de.intent_decisions(limit)) if hasattr(de, "intent_decisions") else 0,
        "decisions": de.intent_decisions(limit) if hasattr(de, "intent_decisions") else [],
    })


@router.get("/api/kg/mcmp_stats")
async def kg_mcmp_stats(request: Request):
    """Stats about the MCMP gardener (random walker + pruner)."""
    g = getattr(request.app.state, "mcmp_gardener", None)
    if g is None:
        return JSONResponse(
            {"enabled": False, "message": "mcmp_gardener not running"},
        )
    return JSONResponse({
        "enabled": True,
        "stats": convert_numpy(dict(g.stats)),
        "config": {
            "tick_interval_s": float(__import__("core.mcmp_gardener", fromlist=["TICK_INTERVAL_S"]).TICK_INTERVAL_S),
        },
        "timestamp": time.time(),
    })


# ===================================================================
# Group 7 — Goals / Evolution / CTM / Cognitive Status
# ===================================================================

@router.get("/api/brain/goals")
async def brain_goals(request: Request):
    """Current goals — graceful fallback."""
    return JSONResponse({
        "goals": [],
        "enabled": False,
        "message": "goal system not connected to unified brain",
        "timestamp": time.time(),
    })


@router.post("/api/brain/goals/add")
async def brain_goals_add(request: Request):
    """Add a goal — 503 until wired."""
    return JSONResponse(
        {"error": "goal system not initialized", "timestamp": time.time()},
        status_code=503,
    )


@router.post("/api/brain/goals/{goal_id}/complete")
async def brain_goals_complete(goal_id: str, request: Request):
    """Mark goal complete — 503 until wired."""
    return JSONResponse(
        {"error": "goal system not initialized", "goal_id": goal_id, "timestamp": time.time()},
        status_code=503,
    )


@router.post("/api/brain/goals/{goal_id}/fail")
async def brain_goals_fail(goal_id: str, request: Request):
    """Mark goal failed — 503 until wired."""
    return JSONResponse(
        {"error": "goal system not initialized", "goal_id": goal_id, "timestamp": time.time()},
        status_code=503,
    )


@router.get("/api/brain/evolution")
async def brain_evolution(request: Request):
    """Evolution state — graceful fallback."""
    return JSONResponse({
        "evolution": None,
        "enabled": False,
        "message": "evolution system not connected to unified brain",
        "timestamp": time.time(),
    })


@router.post("/api/brain/evolution/evolve")
async def brain_evolution_evolve(request: Request):
    """Trigger evolution step — 503 until wired."""
    return JSONResponse(
        {"error": "evolution system not initialized", "timestamp": time.time()},
        status_code=503,
    )


@router.get("/api/brain/ctm_health")
async def ctm_health(request: Request):
    """CTM health — graceful fallback."""
    return JSONResponse({
        "ctm_health": None,
        "enabled": False,
        "message": "CTM not connected to unified brain",
        "timestamp": time.time(),
    })


@router.get("/api/brain/cognitive_status")
async def cognitive_status(request: Request):
    """Cognitive status summary — graceful fallback."""
    return JSONResponse({
        "cognitive_status": None,
        "enabled": False,
        "message": "cognitive status not connected to unified brain",
        "timestamp": time.time(),
    })


# ===================================================================
# Group 8 — Causal / Meta / Federated / Advanced Learning
# ===================================================================

@router.get("/api/causal/status")
async def causal_status(request: Request):
    """Causal reasoning status — not yet wired."""
    return JSONResponse(
        {"error": "causal reasoning not available", "timestamp": time.time()},
        status_code=503,
    )


@router.get("/api/causal/graph")
async def causal_graph(request: Request):
    """Causal graph — not yet wired."""
    return JSONResponse(
        {"error": "causal reasoning not available", "timestamp": time.time()},
        status_code=503,
    )


@router.post("/api/causal/analyze")
async def causal_analyze(request: Request):
    """Causal analysis — not yet wired."""
    return JSONResponse(
        {"error": "causal reasoning not available", "timestamp": time.time()},
        status_code=503,
    )


@router.get("/api/meta/status")
async def meta_status(request: Request):
    """Meta-learning status — not yet wired."""
    return JSONResponse(
        {"error": "meta-learning not available", "timestamp": time.time()},
        status_code=503,
    )


@router.post("/api/meta/adapt")
async def meta_adapt(request: Request):
    """Meta-learning adapt — not yet wired."""
    return JSONResponse(
        {"error": "meta-learning not available", "timestamp": time.time()},
        status_code=503,
    )


@router.get("/api/federated/status")
async def federated_status(request: Request):
    """Federated learning status — not yet wired."""
    return JSONResponse(
        {"error": "federated learning not available", "timestamp": time.time()},
        status_code=503,
    )


@router.get("/api/federated/nodes")
async def federated_nodes(request: Request):
    """Federated learning nodes — not yet wired."""
    return JSONResponse(
        {"error": "federated learning not available", "timestamp": time.time()},
        status_code=503,
    )


@router.get("/api/federated/rounds")
async def federated_rounds(request: Request):
    """Federated learning rounds — not yet wired."""
    return JSONResponse(
        {"error": "federated learning not available", "timestamp": time.time()},
        status_code=503,
    )


@router.get("/api/advanced_learning/health")
async def advanced_learning_health(request: Request):
    """Advanced learning health — not yet wired."""
    return JSONResponse(
        {"error": "advanced learning not available", "timestamp": time.time()},
        status_code=503,
    )


# ===================================================================
# Group 9 — Conversation Monitoring & Simulation
# ===================================================================

@router.get("/api/conversation/active")
async def conversation_active(request: Request):
    """Active conversations from LiveBrainMonitor."""
    lm = request.app.state.live_monitor
    if lm is None:
        return JSONResponse({
            "conversations": [],
            "message": "live_monitor not initialized",
            "timestamp": time.time(),
        })
    try:
        convos = lm.get_active_conversations()
        return JSONResponse({
            "conversations": convert_numpy(convos),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "conversations": [],
            "error": str(exc),
            "timestamp": time.time(),
        })


@router.get("/api/conversation/history")
async def conversation_history(request: Request):
    """Conversation history."""
    lm = request.app.state.live_monitor
    if lm is None:
        return JSONResponse({
            "history": [],
            "message": "live_monitor not initialized",
            "timestamp": time.time(),
        })
    try:
        history = lm.get_conversation_history()
        return JSONResponse({
            "history": convert_numpy(history),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "history": [],
            "error": str(exc),
            "timestamp": time.time(),
        })


@router.post("/api/simulate/conversation")
async def simulate_conversation(request: Request):
    """Simulate a conversation for testing — 503 until wired."""
    return JSONResponse(
        {"error": "simulation not available", "timestamp": time.time()},
        status_code=503,
    )


# ===================================================================
# Group 10 — Predict Path
# ===================================================================

@router.post("/api/predict/path")
async def predict_path(request: Request):
    """Predict conversation path using ConversationPathPlanner."""
    pp = request.app.state.path_planner
    if pp is None:
        return JSONResponse({
            "path": None,
            "message": "path_planner not initialized",
            "timestamp": time.time(),
        })
    try:
        body = await request.json()
        task = body.get("task", "")
        if not task:
            return JSONResponse({"error": "task is required"}, status_code=400)
        result = pp.predict_path(task)
        return JSONResponse({
            "path": convert_numpy(result),
            "timestamp": time.time(),
        })
    except Exception as exc:
        return JSONResponse({
            "path": None,
            "error": str(exc),
            "timestamp": time.time(),
        })


# ===================================================================
# UI page
# ===================================================================

@router.get("/ui/brain", response_class=HTMLResponse)
async def brain_ui(request: Request) -> HTMLResponse:
    """Render brain dashboard."""
    return request.app.state.templates.TemplateResponse(
        request, "brain_dashboard.html"
    )
