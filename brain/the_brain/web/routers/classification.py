"""Brain classification endpoints — fast intent classification via SBERT + EventRoutingHead.

Encodes text via sentence-transformers (MiniLM, 384-dim) and routes to one of
~120 event_types via cosine similarity against learned centroids. The SeedEncoder
+ RadialNetwork pipeline was tested first but its hashed-bag-of-words signal
turned out too weak to separate this many classes — see the seed test in
docs/event_classifier_separability.md.
"""
from __future__ import annotations

import logging
import time

import torch
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger('brain.classification')

router = APIRouter()


def _embed(text: str, sbert) -> torch.Tensor:
    """Encode text via SBERT into a (1, 384) tensor."""
    vec = sbert.encode([text[:200]], convert_to_numpy=True)
    return torch.tensor(vec, dtype=torch.float32)


@router.post("/api/cortex/classify")
async def brain_classify(request: Request) -> JSONResponse:
    """Fast intent classification via RadialNetwork + EventRoutingHead.

    Body: {user_text: str, context?: dict}
    Returns: {event_type, confidence, alternatives, routing_id, latency_ms}

    Target latency: <100ms.
    """
    try:
        body = await request.json()
    except Exception:
        # Handle Latin-1 encoding from Windows clients
        raw = await request.body()
        import json as _json
        body = _json.loads(raw.decode('utf-8', errors='replace'))

    user_text = body.get("user_text", "")
    context = body.get("context", {})
    user_id = body.get("user_id")  # optional — enables per-user personalization

    # Same context-prefix trick as the routing endpoint, so identical inputs
    # produce identical embeddings across both heads.
    if context:
        prefix_parts = []
        if context.get("current_space"):
            prefix_parts.append(f"space:{context['current_space']}")
        if context.get("current_bubble"):
            prefix_parts.append(f"bubble:{context['current_bubble']}")
        if context.get("idea_count"):
            prefix_parts.append(f"ideas:{context['idea_count']}")
        if prefix_parts:
            user_text = f"[{' '.join(prefix_parts)}] {user_text}"

    sbert = getattr(request.app.state, 'sbert_encoder', None)
    event_head = getattr(request.app.state, 'event_routing_head', None)

    if sbert is None or event_head is None:
        return JSONResponse({"error": "classification not available"}, status_code=503)
    if not user_text:
        return JSONResponse({"error": "user_text required"}, status_code=400)

    t0 = time.time()
    try:
        emb = _embed(user_text, sbert)
        decision = event_head.route(emb, user_id=user_id)
        decision['latency_ms'] = round((time.time() - t0) * 1000, 1)
        if user_id:
            decision['user_id'] = user_id
        return JSONResponse(decision)
    except Exception as e:
        logger.error(f"Classification failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/cortex/classify/reward")
async def brain_classify_reward(request: Request) -> JSONResponse:
    """Reward signal for a previous classification.

    Body: {routing_id: str, success: bool}
    Called after the tool actually executes — success strengthens the
    centroid, failure weakens it.
    """
    body = await request.json()
    event_head = getattr(request.app.state, 'event_routing_head', None)
    if event_head is None:
        return JSONResponse({"error": "classification not available"}, status_code=503)

    routing_id = body.get("routing_id", "")
    success = body.get("success", False)
    if not routing_id:
        return JSONResponse({"error": "routing_id required"}, status_code=400)

    applied = event_head.reward(routing_id, success)
    return JSONResponse({"ok": applied, "routing_id": routing_id})


@router.post("/api/cortex/classify/train")
async def brain_classify_train(request: Request) -> JSONResponse:
    """Supervised training from ground truth (shadow observer or bootstrap).

    Body: {user_text: str, correct_event_type: str}
    Directly attracts the correct centroid toward the input embedding.
    """
    try:
        body = await request.json()
    except Exception:
        raw = await request.body()
        import json as _json
        body = _json.loads(raw.decode('utf-8', errors='replace'))

    user_text = body.get("user_text", "")
    correct_event = body.get("correct_event_type", "")
    user_id = body.get("user_id")  # optional — trains the per-user delta too

    sbert = getattr(request.app.state, 'sbert_encoder', None)
    event_head = getattr(request.app.state, 'event_routing_head', None)

    if not sbert or not event_head:
        return JSONResponse({"error": "classification not available"}, status_code=503)
    if not user_text or not correct_event:
        return JSONResponse(
            {"error": "user_text and correct_event_type required"}, status_code=400
        )

    try:
        emb = _embed(user_text, sbert)
        applied = event_head.train_supervised(emb, correct_event, user_id=user_id)

        # Autosave periodically so accumulated training survives a crash
        if applied and event_head.should_autosave():
            try:
                ckpt_path = getattr(
                    request.app.state, 'event_routing_head_ckpt', None
                )
                if ckpt_path:
                    event_head.save(ckpt_path)
            except Exception as e:
                logger.warning(f"Autosave failed: {e}")

        return JSONResponse({"ok": applied, "trained_event": correct_event})
    except Exception as e:
        logger.error(f"Training failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/cortex/classify/stats")
async def brain_classify_stats(request: Request) -> JSONResponse:
    """Stats for the EventRoutingHead — routes, rewards, top centroids."""
    event_head = getattr(request.app.state, 'event_routing_head', None)
    if event_head is None:
        return JSONResponse({"error": "classification not available"}, status_code=503)
    return JSONResponse(event_head.get_stats())
