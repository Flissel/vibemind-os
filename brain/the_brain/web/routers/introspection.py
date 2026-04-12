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
from typing import Any

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
