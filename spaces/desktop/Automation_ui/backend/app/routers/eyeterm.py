"""eyeTerm REST API router — status and control endpoints.

Proxies to the eyeTerm process running in VibeMind's Python backend.
The eyeTerm MJPEG stream runs on port 8099; this router provides
status and control at /api/eyeterm/*.

Includes a realtime face-swap stream at /api/eyeterm/swap-stream which
pipes the eyeTerm MJPEG through InsightFace inswapper for live preview.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/eyeterm", tags=["eyeterm"])

EYETERM_BASE = "http://127.0.0.1:8099"
_HTTP_TIMEOUT = 2.0

# Lazy-init faceswap helpers — make the package importable so we can resolve
# target paths + load FaceSwapper. Same logic as routers/video.py.
_VIBEMIND_OS = Path(__file__).resolve().parents[6]
_DEEPFAKE_DIR = _VIBEMIND_OS / "spaces" / "video" / "vibevideo_deepfake"
if str(_DEEPFAKE_DIR) not in sys.path:
    sys.path.insert(0, str(_DEEPFAKE_DIR))

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


# ───────────────────────────────────────────────────────────────────────
# Realtime face-swap stream
#
# Heavy ML stack (insightface + onnxruntime-gpu) lives in voice/.venv312,
# not in the backend's .venv. So we spawn a subprocess (live_server.py)
# on first request, then proxy the MJPEG stream from there.
#
# The subprocess listens on 127.0.0.1:8098, self-terminates after 5 min
# of idle, gets respawned on next request.
# ───────────────────────────────────────────────────────────────────────

import subprocess

LIVE_SWAP_PORT = 8098
LIVE_SWAP_BASE = f"http://127.0.0.1:{LIVE_SWAP_PORT}"
_LIVE_PROC: Optional[subprocess.Popen] = None
_LIVE_LOCK = threading.Lock()

# GPU-offload switch — same env contract as video.py (read independently,
# no cross-router import). local = spawn live_server subprocess on this
# host; remote = proxy to a GPU server's live-swap endpoint (Phase 1).
# See tasks/gpu-offload-architecture-plan.md.
_FACESWAP_BACKEND = os.environ.get("FACESWAP_BACKEND", "local").strip().lower()
# For the live stream the remote endpoint is the live_server base URL on
# the GPU host (e.g. http://gpuhost:8098). Falls back to FACESWAP_REMOTE_URL
# if a dedicated live URL isn't given.
_LIVE_SWAP_REMOTE = (
    os.environ.get("FACESWAP_LIVE_REMOTE_URL", "").strip()
    or os.environ.get("FACESWAP_REMOTE_URL", "").strip()
)


def _resolve_live_swap_base() -> Optional[str]:
    """Where the live-swap MJPEG server lives.

    local  → local subprocess base (spawned on demand, today's behaviour)
    remote → the GPU host's live_server base (no local spawn). Returns
             None if remote is selected but no URL configured, so the
             caller can fail loud instead of silently spawning locally.
    """
    if _FACESWAP_BACKEND == "remote":
        return _LIVE_SWAP_REMOTE or None
    return LIVE_SWAP_BASE


def _live_server_alive() -> bool:
    """Quick health probe."""
    try:
        with httpx.Client(timeout=1.5) as c:
            r = c.get(f"{LIVE_SWAP_BASE}/health")
            return r.status_code == 200
    except Exception:
        return False


def _spawn_live_server() -> bool:
    """Spawn the live-swap subprocess. Returns True once /health responds."""
    global _LIVE_PROC
    voice_py = _VIBEMIND_OS / "voice" / ".venv312" / "Scripts" / "python.exe"
    if not voice_py.exists():
        logger.warning("voice/.venv312 python not found at %s", voice_py)
        return False
    with _LIVE_LOCK:
        if _LIVE_PROC is not None and _LIVE_PROC.poll() is None:
            return True  # someone else spawned while we waited
        if _live_server_alive():
            return True
        logger.info("Spawning faceswap live_server subprocess")
        try:
            _LIVE_PROC = subprocess.Popen(
                [
                    str(voice_py),
                    "-m", "faceswap.live_server",
                    "--port", str(LIVE_SWAP_PORT),
                ],
                cwd=str(_DEEPFAKE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            logger.error("Failed to spawn live_server: %s", e)
            return False
    # Wait up to 20s for it to bind + import insightface
    import time as _time
    deadline = _time.time() + 20
    while _time.time() < deadline:
        if _live_server_alive():
            return True
        _time.sleep(0.5)
    logger.warning("live_server did not respond within 20s")
    return False


@router.get("/swap-stream")
async def eyeterm_swap_stream(target: str):
    """Realtime face-swap proxy.

    Delegates to a subprocess (live_server.py) running in voice/.venv312
    because that's where InsightFace lives. First call per target pays
    ~12s init; subsequent calls reuse the cached FaceSwapper.
    """
    swap_base = _resolve_live_swap_base()
    if swap_base is None:
        raise HTTPException(
            503,
            "FACESWAP_BACKEND=remote but no FACESWAP_LIVE_REMOTE_URL / "
            "FACESWAP_REMOTE_URL configured — remote live-swap not set up "
            "(Phase 1, see tasks/gpu-offload-architecture-plan.md)",
        )

    loop = asyncio.get_running_loop()
    # Only spawn a local subprocess in local mode; in remote mode the
    # GPU host already runs live_server — we just proxy to it.
    if _FACESWAP_BACKEND != "remote" and not _live_server_alive():
        ok = await loop.run_in_executor(None, _spawn_live_server)
        if not ok:
            raise HTTPException(503, "live-swap subprocess failed to start")

    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=None, write=None, pool=None))

    async def gen():
        try:
            url = f"{swap_base}/stream?target={target}"
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    body = (await r.aread())[:200].decode("ascii", "ignore")
                    raise HTTPException(r.status_code, f"live_server: {body}")
                async for chunk in r.aiter_raw():
                    yield chunk
        finally:
            await client.aclose()

    return StreamingResponse(
        gen(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
