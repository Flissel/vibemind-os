"""eyeTerm REST API router — status and control endpoints.

Proxies to the eyeTerm process running in VibeMind's Python backend.
The eyeTerm MJPEG stream runs on port 8099; this router provides
status and control at /api/eyeterm/*.

Face-swap functionality has moved to vibevideo_deepfake/faceswap/ —
eyeTerm is now pure eye-tracking + cursor + wink.
"""

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

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
    """Get current eyeTerm state for the PiP overlay."""
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
    _eyeterm_state["cursor_enabled"] = not _eyeterm_state["cursor_enabled"]
    return {"cursor_enabled": _eyeterm_state["cursor_enabled"]}


@router.get("/stream")
async def eyeterm_stream():
    """Same-origin proxy of the eyeTerm MJPEG stream.

    Browsers refuse to render <img src=http://127.0.0.1:8099/stream> from a
    file:// or different-origin renderer in some configurations even with
    Access-Control-Allow-Origin:* set. Routing through the backend gives
    the UI a same-origin :8007 endpoint that always works.
    """
    client = httpx.AsyncClient(timeout=None)

    async def gen():
        try:
            async with client.stream("GET", f"{EYETERM_BASE}/stream") as r:
                async for chunk in r.aiter_raw():
                    yield chunk
        finally:
            await client.aclose()

    return StreamingResponse(
        gen(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
