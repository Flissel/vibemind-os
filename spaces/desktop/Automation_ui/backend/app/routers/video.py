"""Video router — face-swap batch jobs.

Spawns ``vibevideo_deepfake/deepfake.py faceswap batch ...`` as a background
subprocess and tracks progress in-memory. The Video Studio UI (electron-app/
video-ui/src/features/VideoProduction.tsx) creates jobs, polls status, and
the Gallery picks up the resulting MP4 from ``~/.rowboat/Videos/`` once
``scan_video_outputs`` re-scans (the gallery has a 30s auto-refresh).

Routes
------
GET  /api/video/presets       → list 100 face presets (id + display name)
POST /api/video/faceswap      → start a job, returns {job_id, output_path}
GET  /api/video/job/{id}      → poll progress {state, percent, fps, eta_s, ...}
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/video", tags=["video"])

# ──────────────────────────────────────────────────────────────────────
# Paths — discovered relative to vibemind-os checkout
# ──────────────────────────────────────────────────────────────────────

# This file: vibemind-os/spaces/desktop/Automation_ui/backend/app/routers/video.py
# parents[6] → vibemind-os/ ; parents[7] → repo root
_VIBEMIND_OS = Path(__file__).resolve().parents[6]
_REPO_ROOT = _VIBEMIND_OS.parent
_DEEPFAKE_DIR = _VIBEMIND_OS / "spaces" / "video" / "vibevideo_deepfake"
_DEEPFAKE_CLI = _DEEPFAKE_DIR / "deepfake.py"
_MEDIA_ROOT = Path.home() / ".rowboat" / "Videos"

# Add faceswap package to sys path so we can import presets directly
_FACESWAP_SRC = _DEEPFAKE_DIR
if str(_FACESWAP_SRC) not in sys.path:
    sys.path.insert(0, str(_FACESWAP_SRC))


def _resolve_faceswap_python() -> str:
    """Return a Python interpreter that has the full deepfake ML stack
    (insightface + onnxruntime-gpu) installed. Backend's own .venv
    deliberately doesn't carry the multi-GB CUDA stack — face-swap runs
    out of vibemind-os/voice/.venv312 which keeps it isolated."""
    candidates = [
        # Voice venv has the curated insightface 0.7.3 + onnxruntime-gpu install
        _VIBEMIND_OS / "voice" / ".venv312" / "Scripts" / "python.exe",
        # Fallback: shared root venv (only works if user installed insightface there)
        Path(sys.executable),
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return sys.executable


# ──────────────────────────────────────────────────────────────────────
# Job state — in-memory only, survives one backend lifetime
# ──────────────────────────────────────────────────────────────────────

_JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()

# Match `[frame] 12/300 ( 4.0%) 28.5 fps eta 12s` lines from batch.py
_PROGRESS_RE = re.compile(
    r"\[frame\]\s+(\d+)/(\d+)\s+\(\s*([\d.]+)%\)\s+([\d.]+)\s*fps\s+eta\s+([\d.]+)s"
)


def _set_job(job_id: str, **patch) -> None:
    with _JOBS_LOCK:
        if job_id not in _JOBS:
            return
        _JOBS[job_id].update(patch)


def _get_job(job_id: str) -> Optional[dict]:
    with _JOBS_LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else None


# ──────────────────────────────────────────────────────────────────────
# Presets endpoint — read-only
# ──────────────────────────────────────────────────────────────────────

@router.get("/presets")
async def list_presets():
    """Return [{id, name}, ...] for all installed face presets."""
    try:
        from faceswap.presets import list_presets_detailed  # type: ignore
        return {"presets": list_presets_detailed()}
    except Exception as e:
        logger.warning("Failed to import faceswap.presets: %s", e)
        return {"presets": [], "error": str(e)}


# ──────────────────────────────────────────────────────────────────────
# Gallery — HTTP fallback for the Electron IPC video_list call
# ──────────────────────────────────────────────────────────────────────

# Make spaces/video reachable so we can import scan_video_outputs
_VIDEO_TOOLS_DIR = _VIBEMIND_OS / "spaces" / "video"
if str(_VIDEO_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_VIDEO_TOOLS_DIR))


@router.get("/gallery")
async def list_gallery_videos():
    """List all videos in the gallery (Rowboat Videos + vibevideo + deepfake outputs).
    Same payload as the Electron IPC ``video_list`` call — UI can use either."""
    try:
        from tools.video_tools import scan_video_outputs  # type: ignore
        return scan_video_outputs()
    except Exception as e:
        logger.warning("scan_video_outputs failed: %s", e)
        return {"success": False, "error": str(e), "videos": []}


# ──────────────────────────────────────────────────────────────────────
# Start a face-swap batch job
# ──────────────────────────────────────────────────────────────────────

class FaceswapStartBody(BaseModel):
    input_path: str           # absolute path to input mp4 (from gallery)
    target: str               # display name OR slug ("Diego" / "face1")
    no_alignment: bool = False
    smoothing: float = 0.8
    no_audio: bool = False


@router.post("/faceswap")
async def start_faceswap(body: FaceswapStartBody):
    if not _DEEPFAKE_CLI.exists():
        raise HTTPException(500, f"deepfake.py not found at {_DEEPFAKE_CLI}")

    input_path = Path(body.input_path).expanduser()
    if not input_path.exists():
        raise HTTPException(400, f"Input video not found: {input_path}")

    # Build output path in advance so the UI knows where to look
    target_slug = body.target.lower().replace(" ", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = _MEDIA_ROOT / f"{input_path.stem}_swap_{target_slug}_{ts}.mp4"

    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "state": "starting",     # starting | running | done | failed
            "percent": 0.0,
            "frame": 0,
            "total_frames": 0,
            "fps": 0.0,
            "eta_s": 0.0,
            "input_path": str(input_path),
            "output_path": str(output_path),
            "target": body.target,
            "started_at": time.time(),
            "log_tail": [],          # last 20 stdout lines
            "error": None,
        }

    args = [
        _resolve_faceswap_python(),
        str(_DEEPFAKE_CLI),
        "faceswap", "batch",
        str(input_path),
        "--target", body.target,
        "--output", str(output_path),
        "--smoothing", str(body.smoothing),
    ]
    if body.no_alignment:
        args.append("--no-alignment")
    if body.no_audio:
        args.append("--no-audio")

    threading.Thread(
        target=_run_job,
        args=(job_id, args),
        daemon=True,
        name=f"faceswap-{job_id}",
    ).start()

    return {"job_id": job_id, "output_path": str(output_path)}


def _run_job(job_id: str, args: list) -> None:
    """Run subprocess, parse progress lines, update job state."""
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(_DEEPFAKE_DIR),
        )
    except Exception as e:
        _set_job(job_id, state="failed", error=f"spawn failed: {e}")
        return

    _set_job(job_id, state="running")

    log_buf: list = []
    try:
        for line in proc.stdout:                # type: ignore[union-attr]
            line = line.rstrip()
            if not line:
                continue
            log_buf.append(line)
            log_buf = log_buf[-20:]

            m = _PROGRESS_RE.search(line)
            if m:
                _set_job(
                    job_id,
                    frame=int(m.group(1)),
                    total_frames=int(m.group(2)),
                    percent=float(m.group(3)),
                    fps=float(m.group(4)),
                    eta_s=float(m.group(5)),
                    log_tail=list(log_buf),
                )
            else:
                _set_job(job_id, log_tail=list(log_buf))

        rc = proc.wait()
        if rc == 0:
            _set_job(job_id, state="done", percent=100.0, eta_s=0.0)
        else:
            _set_job(
                job_id,
                state="failed",
                error=f"exit code {rc}",
                log_tail=list(log_buf),
            )
    except Exception as e:
        _set_job(job_id, state="failed", error=str(e), log_tail=list(log_buf))


# ──────────────────────────────────────────────────────────────────────
# Job polling
# ──────────────────────────────────────────────────────────────────────

@router.get("/job/{job_id}")
async def get_job(job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs")
async def list_jobs():
    """Convenience: list active + recent jobs (last 50)."""
    with _JOBS_LOCK:
        jobs = sorted(_JOBS.values(), key=lambda j: j.get("started_at", 0), reverse=True)
    return {"jobs": jobs[:50]}


# ──────────────────────────────────────────────────────────────────────
# Live recording — pipes eyeTerm MJPEG (:8099/stream) into an MP4 via ffmpeg
# ──────────────────────────────────────────────────────────────────────

import shutil  # noqa: E402


def _resolve_ffmpeg() -> Optional[str]:
    """Find ffmpeg binary across the common Windows install patterns.

    Resolution order:
    1. ``FFMPEG_PATH`` env var (advanced users / CI override)
    2. ``shutil.which("ffmpeg")`` — anything on PATH
    3. Common Windows install locations (winget / scoop / chocolatey / manual)

    Returns the absolute path or ``None`` if ffmpeg cannot be found.
    """
    override = os.environ.get("FFMPEG_PATH", "").strip()
    if override:
        if Path(override).exists():
            return override
        logger.warning("FFMPEG_PATH=%r does not exist, falling back to auto-detect", override)

    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    home = Path.home()
    candidates = [
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe"),
        home / "scoop" / "shims" / "ffmpeg.exe",
        home / "AppData" / "Local" / "Programs" / "ffmpeg" / "bin" / "ffmpeg.exe",
        # winget MSIX install pattern
        home / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _resolve_audio_device(ffmpeg_bin: str) -> Optional[str]:
    """Pick a DirectShow audio input device for live capture.

    Resolution order:
    1. ``LIVE_AUDIO_DEVICE`` env var (exact dshow name; empty disables audio)
    2. First device matching a curated quality preference (HyperX, Yeti, etc.)
    3. First detected ``(audio)`` device
    Returns ``None`` if no devices are available — recording then runs silent.
    """
    override = os.environ.get("LIVE_AUDIO_DEVICE", "").strip()
    if override:
        return override if override.lower() != "none" else None
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-list_devices", "true",
             "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, timeout=8,
        )
        # ffmpeg writes device list to stderr
        text = (result.stderr or "") + (result.stdout or "")
    except Exception as e:
        logger.warning("dshow device probe failed: %s", e)
        return None

    audio_names: list[str] = []
    for line in text.splitlines():
        m = re.search(r'"([^"]+)"\s*\(audio\)', line)
        if m:
            audio_names.append(m.group(1))

    if not audio_names:
        return None

    # Curated quality preference — pick the first match
    preferred = ["hyperx", "yeti", "shure", "rode", "elgato"]
    for needle in preferred:
        for name in audio_names:
            if needle in name.lower():
                return name
    # Fallback: first non-virtual device
    for name in audio_names:
        if "virtual" not in name.lower() and "stereomix" not in name.lower():
            return name
    return audio_names[0]


# Single global recorder (only one live capture at a time)
_LIVE_REC: dict = {
    "proc": None,            # subprocess.Popen | None
    "output_path": None,     # Path
    "started_at": None,      # epoch seconds
    "source": None,          # "eyeterm" / "camera-N"
}
_LIVE_REC_LOCK = threading.Lock()


class LiveRecordStartBody(BaseModel):
    source: str = "eyeterm"   # only "eyeterm" supported for now (MJPEG :8099)
    name_hint: Optional[str] = None


@router.get("/live-record/status")
async def live_record_status():
    with _LIVE_REC_LOCK:
        proc = _LIVE_REC["proc"]
        if proc is None or proc.poll() is not None:
            return {"active": False}
        return {
            "active": True,
            "started_at": _LIVE_REC["started_at"],
            "duration_s": round(time.time() - (_LIVE_REC["started_at"] or 0), 1),
            "output_path": str(_LIVE_REC["output_path"]) if _LIVE_REC["output_path"] else None,
            "source": _LIVE_REC["source"],
        }


@router.post("/live-record/start")
async def live_record_start(body: LiveRecordStartBody):
    ffmpeg_bin = _resolve_ffmpeg()
    if not ffmpeg_bin:
        raise HTTPException(
            500,
            "ffmpeg not found. Install via 'winget install ffmpeg' or 'scoop install ffmpeg', "
            "or set FFMPEG_PATH=<absolute_path_to_ffmpeg.exe> in your .env",
        )

    with _LIVE_REC_LOCK:
        proc = _LIVE_REC["proc"]
        if proc is not None and proc.poll() is None:
            raise HTTPException(409, "Live recording already active — stop it first")

    if body.source != "eyeterm":
        raise HTTPException(400, f"Unsupported source: {body.source!r}")

    # Verify eyeTerm stream is reachable before spawning ffmpeg
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:8099/status", timeout=2)
    except Exception as e:
        raise HTTPException(503, f"eyeTerm MJPEG stream not reachable on :8099 ({e})")

    _MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    hint = (body.name_hint or "live").strip() or "live"
    safe_hint = "".join(c if c.isalnum() or c in "_-" else "_" for c in hint)[:32]
    output_path = _MEDIA_ROOT / f"eyeterm_live_{safe_hint}_{ts}.mp4"

    # ffmpeg: MJPEG input + DirectShow microphone → H.264 + AAC mp4
    audio_device = _resolve_audio_device(ffmpeg_bin)
    args = [
        ffmpeg_bin, "-y",
        "-loglevel", "warning",
        # video input
        "-f", "mjpeg",
        "-i", "http://127.0.0.1:8099/stream",
    ]
    if audio_device:
        args += [
            "-f", "dshow",
            "-i", f"audio={audio_device}",
        ]
    args += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart+frag_keyframe+empty_moov",
    ]
    if audio_device:
        args += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    args.append(str(output_path))

    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,           # so we can send 'q' to stop cleanly
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        raise HTTPException(500, f"ffmpeg spawn failed: {e}")

    with _LIVE_REC_LOCK:
        _LIVE_REC["proc"] = proc
        _LIVE_REC["output_path"] = output_path
        _LIVE_REC["started_at"] = time.time()
        _LIVE_REC["source"] = body.source

    return {
        "active": True,
        "started_at": _LIVE_REC["started_at"],
        "output_path": str(output_path),
        "source": body.source,
    }


@router.post("/live-record/stop")
async def live_record_stop():
    with _LIVE_REC_LOCK:
        proc = _LIVE_REC["proc"]
        output_path = _LIVE_REC["output_path"]
        started_at = _LIVE_REC["started_at"]

    if proc is None or proc.poll() is not None:
        return {"active": False, "stopped": False, "message": "no active recording"}

    # Send 'q' to ffmpeg stdin → graceful finalize. Fall back to terminate.
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write(b"q")
            proc.stdin.flush()
            proc.stdin.close()
    except Exception:
        pass

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    duration = round(time.time() - (started_at or 0), 1)
    with _LIVE_REC_LOCK:
        _LIVE_REC["proc"] = None
        _LIVE_REC["output_path"] = None
        _LIVE_REC["started_at"] = None
        _LIVE_REC["source"] = None

    size_mb = 0.0
    if output_path and Path(output_path).exists():
        size_mb = round(Path(output_path).stat().st_size / (1024 * 1024), 2)

    return {
        "active": False,
        "stopped": True,
        "output_path": str(output_path) if output_path else None,
        "duration_s": duration,
        "size_mb": size_mb,
    }
