"""eyeTerm REST API router — status and control endpoints.

Proxies to the eyeTerm process running in VibeMind's Python backend.
The eyeTerm MJPEG stream runs on port 8099; this router provides
status and control at /api/eyeterm/*.
"""

from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/eyeterm", tags=["eyeterm"])

EYETERM_BASE = "http://127.0.0.1:8099"
_HTTP_TIMEOUT = 2.0

# Shared state — set by VibeMind's electron_backend when eyeTerm starts
_eyeterm_state = {
    "running": False,
    "state": "idle",
    "cursor_enabled": False,
    "stream_port": 8099,
}


def update_eyeterm_state(state: dict):
    """Called from eyeTerm's heartbeat to update shared state."""
    _eyeterm_state.update(state)


@router.get("/status")
async def eyeterm_status():
    """Get current eyeTerm state for the PiP overlay.

    Merges local VibeMind-tracked state with eyeTerm's live /status
    (which carries active_preset + has_frame).
    """
    merged = dict(_eyeterm_state)
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.get(f"{EYETERM_BASE}/status")
            if r.status_code == 200:
                merged.update(r.json())
                merged["running"] = True
    except httpx.HTTPError:
        merged["running"] = False
    return merged


@router.post("/toggle-cursor")
async def toggle_cursor():
    """Toggle cursor control on/off."""
    # This will be wired to the actual eyeTerm instance
    _eyeterm_state["cursor_enabled"] = not _eyeterm_state["cursor_enabled"]
    return {"cursor_enabled": _eyeterm_state["cursor_enabled"]}


@router.get("/presets")
async def list_presets():
    """List face-swap target presets + currently active one.

    Proxies eyeTerm's GET /presets. Returns 503 if eyeTerm isn't running.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.get(f"{EYETERM_BASE}/presets")
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail=f"eyeTerm not reachable on {EYETERM_BASE}: {e}",
        )


class SetPresetBody(BaseModel):
    name: Optional[str] = None   # None disables face-swap


@router.post("/set-preset")
async def set_preset(body: SetPresetBody):
    """Switch face-swap target at runtime.

    - `name=null` → disable face-swap (pure camera)
    - `name="face1"` → activate swap with that preset
    """
    payload = {"type": "set_preset", "name": body.name}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(f"{EYETERM_BASE}/command", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail=f"eyeTerm not reachable on {EYETERM_BASE}: {e}",
        )


class StartRecordingBody(BaseModel):
    name_hint: Optional[str] = None   # optional suffix for the MP4 filename


@router.get("/recording")
async def recording_status():
    """Current recording state (active, filename, duration_s, ...)."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.get(f"{EYETERM_BASE}/recording")
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail=f"eyeTerm not reachable on {EYETERM_BASE}: {e}",
        )


@router.post("/recording/start")
async def recording_start(body: StartRecordingBody):
    """Start recording the UI frame (full HUD) to ~/.rowboat/Videos/*.mp4.

    Files are picked up by the Rowboat space + Video space automatically
    since both read from the same media_server MEDIA_ROOT.
    """
    payload = {"type": "start_recording", "name_hint": body.name_hint}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(f"{EYETERM_BASE}/command", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail=f"eyeTerm not reachable on {EYETERM_BASE}: {e}",
        )


@router.post("/recording/stop")
async def recording_stop():
    """Finalize the current recording and return the filename + stats."""
    payload = {"type": "stop_recording"}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(f"{EYETERM_BASE}/command", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail=f"eyeTerm not reachable on {EYETERM_BASE}: {e}",
        )
