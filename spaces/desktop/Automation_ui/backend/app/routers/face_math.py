"""face-math API.

Phase A — reference capture:
  POST /api/face-math/reference/start          → kick off a 20s capture
  GET  /api/face-math/reference/status/{id}    → progress / finished state
  GET  /api/face-math/reference/list           → all takes this process has seen
  GET  /api/face-math/reference/preview/{id}   → annotated JPEG with landmarks
  GET  /api/face-math/reference/stats/{id}     → motion_stats.json

Phase D — live region-math swap stream:
  GET  /api/face-math/swap-stream              → MJPEG live composite
  GET  /api/face-math/profiles                 → list swap profiles
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..services.face_math.live_stream import region_math_stream
from ..services.face_math.reference_recorder import (
    DEFAULT_STREAM_URL,
    REFS_ROOT,
    ReferenceRecorder,
)
from ..services.face_math.regions import SWAP_PROFILES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/face-math", tags=["face-math"])


def _status_dict(s) -> dict:
    return {
        "take_id": s.take_id,
        "state": s.state,
        "seconds_target": s.seconds_target,
        "frames_captured": s.frames_captured,
        "frames_with_face": s.frames_with_face,
        "started_at": s.started_at,
        "finished_at": s.finished_at,
        "output_dir": s.output_dir,
        "error": s.error,
    }


@router.post("/reference/start")
def reference_start(
    seconds: float = Query(20.0, gt=0, le=120),
    stream_url: str = Query(DEFAULT_STREAM_URL),
):
    """Start a reference capture. Returns the take status immediately.

    Default source is eyeTerm's MJPEG (:8099/stream) so the geometry is
    in the same coordinate space the swap-stream will see at runtime.
    """
    recorder = ReferenceRecorder.get()
    try:
        status = recorder.start(seconds=seconds, stream_url=stream_url)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _status_dict(status)


@router.get("/reference/status/{take_id}")
def reference_status(take_id: str):
    recorder = ReferenceRecorder.get()
    s = recorder.status(take_id)
    if s is None:
        raise HTTPException(404, f"unknown take_id: {take_id}")
    return _status_dict(s)


@router.get("/reference/list")
def reference_list():
    recorder = ReferenceRecorder.get()
    return {"takes": [_status_dict(s) for s in recorder.list_takes()]}


@router.get("/reference/preview/{take_id}")
def reference_preview(take_id: str):
    preview = REFS_ROOT / take_id / "preview.jpg"
    if not preview.exists():
        raise HTTPException(404, f"no preview for {take_id} — capture in progress or no face seen?")
    return FileResponse(preview, media_type="image/jpeg")


@router.get("/reference/stats/{take_id}")
def reference_stats(take_id: str):
    stats = REFS_ROOT / take_id / "motion_stats.json"
    if not stats.exists():
        raise HTTPException(404, f"no stats for {take_id} — capture not finished?")
    return FileResponse(stats, media_type="application/json")


# ---------------------------------------------------------------------------
# Phase D — Live region-math swap stream
# ---------------------------------------------------------------------------

@router.get("/profiles")
def list_profiles():
    """List the available swap profiles + their region composition."""
    return {
        "profiles": {
            name: list(regions) for name, regions in SWAP_PROFILES.items()
        },
        "default": "inner_face",
    }


@router.get("/swap-stream")
async def swap_stream(
    target: str = Query(..., description="face_target id (e.g. 'marshall_v5')"),
    profile: str = Query("inner_face", description="swap profile name"),
    blend_mode: str = Query("alpha", description="alpha | poisson | hybrid"),
    color_match_method: str = Query("histogram", description="histogram | mean_std"),
    feather_px: int = Query(12, ge=0, le=60),
    quality: int = Query(78, ge=30, le=95),
):
    """Live region-math face-swap MJPEG stream.

    Pulls frames from the backend's own /api/eyeterm/stream proxy, runs
    a per-frame region-composite, re-encodes JPEG. Identity-transfer via
    landmark geometry only — no neural face-swap model needed.

    Typical use from the UI:
        <img src="/api/face-math/swap-stream?target=marshall_v5&profile=inner_face">
    """
    if profile not in SWAP_PROFILES:
        raise HTTPException(
            400,
            f"unknown profile: {profile!r} (known: {list(SWAP_PROFILES)})",
        )
    try:
        gen = region_math_stream(
            target_id=target,
            profile=profile,
            blend_mode=blend_mode,
            color_match_method=color_match_method,
            feather_px=feather_px,
            quality=quality,
        )
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))

    return StreamingResponse(
        gen,
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
