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
# FaceFusion external install (on E: because C: is full)
# ──────────────────────────────────────────────────────────────────────

# Root of the FaceFusion install. Override with FACEFUSION_ROOT env if needed.
_FACEFUSION_ROOT = Path(os.environ.get(
    "FACEFUSION_ROOT", r"E:\Vibemind_Tools\facefusion"
))
_FACEFUSION_VENV = Path(os.environ.get(
    "FACEFUSION_VENV", r"E:\Vibemind_Tools\facefusion_venv"
))


def _facefusion_available() -> bool:
    return (_FACEFUSION_ROOT / "facefusion.py").exists() and \
           (_FACEFUSION_VENV / "Scripts" / "python.exe").exists()


def _facefusion_env() -> dict:
    """Build env vars so onnxruntime-gpu can find CUDA DLLs from the
    nvidia-cublas-cu12 etc. pip packages inside the venv."""
    nvidia = _FACEFUSION_VENV / "Lib" / "site-packages" / "nvidia"
    dll_dirs = [
        nvidia / "cublas" / "bin",
        nvidia / "cudnn" / "bin",
        nvidia / "cufft" / "bin",
        nvidia / "curand" / "bin",
        nvidia / "cusolver" / "bin",
        nvidia / "cusparse" / "bin",
        nvidia / "cuda_runtime" / "bin",
        nvidia / "cuda_nvrtc" / "bin",
    ]
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        [str(d) for d in dll_dirs if d.exists()]
    ) + os.pathsep + env.get("PATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _facefusion_target_path(target: str) -> Optional[Path]:
    """Resolve a target display-name or slug → absolute path to a source
    face image on disk.

    Resolution order:
    1. Supabase ``face_targets`` table — dump bytes to a stable temp file
       so the FaceFusion subprocess can read it. Cache the temp file by
       slug so repeated calls don't keep dumping the same bytes.
    2. Legacy filesystem ``vibevideo_deepfake/faceswap/targets/*.jpg``.
    """
    # Try DB first
    try:
        import psycopg2  # type: ignore
        db_url = os.environ.get(
            "FACE_DB_URL",
            "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        )
        with psycopg2.connect(db_url) as c, c.cursor() as cur:
            cur.execute(
                """
                select ft.id, ft.primary_image_path
                from public.face_targets ft
                where ft.enabled = true
                  and (ft.id = %s or lower(ft.display_name) = lower(%s))
                limit 1
                """,
                (target, target),
            )
            row = cur.fetchone()
            if row:
                slug, storage_path = row
                cur.execute(
                    "select bytes from public.face_target_blobs where storage_path = %s",
                    (storage_path,),
                )
                blob_row = cur.fetchone()
                if blob_row and blob_row[0]:
                    cache_dir = Path(os.environ.get("TEMP", "/tmp")) / "vibemind_face_cache"
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    out = cache_dir / f"{slug}.jpg"
                    out.write_bytes(bytes(blob_row[0]))
                    return out
    except Exception as e:
        logger.warning("DB target lookup failed (falling back to FS): %s", e)

    # Fallback: filesystem collection via legacy presets module
    try:
        from faceswap.presets import resolve_preset, DISPLAY_NAMES  # type: ignore
    except Exception as e:
        logger.warning("Cannot import faceswap.presets: %s", e)
        return None
    slug = target
    name_to_slug = {v.lower(): k for k, v in DISPLAY_NAMES.items()}
    if target.lower() in name_to_slug:
        slug = name_to_slug[target.lower()]
    try:
        return resolve_preset(slug)
    except Exception:
        return None


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
        # Load video_tools by absolute path so we don't collide with a
        # different `tools` package elsewhere on sys.path.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_vibevideo_tools",
            str(_VIDEO_TOOLS_DIR / "tools" / "video_tools.py"),
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {_VIDEO_TOOLS_DIR}/tools/video_tools.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.scan_video_outputs()
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
    # Engine: "inswapper" (fast, 128px, baked-in) or "facefusion" (slower,
    # higher quality, supports hyperswap / pixel-boost, requires external
    # install at E:\Vibemind_Tools\facefusion). Default is inswapper for
    # backward compat; UI/auto-record sets "facefusion" when available.
    engine: str = "inswapper"
    # FaceFusion-specific knobs (ignored when engine="inswapper").
    # For "swap hair too" use hififace_unofficial_256 or ghost_3_256 —
    # they cover more of the head than hyperswap.
    ff_model: str = "hyperswap_1c_256"   # blendswap_256, ghost_1_256, ghost_2_256, ghost_3_256, hififace_unofficial_256, hyperswap_1a_256, hyperswap_1b_256, hyperswap_1c_256, inswapper_128, simswap_256, simswap_unofficial_512, uniface_256
    ff_pixel_boost: str = "1024x1024"    # 256x256 / 512x512 / 768x768 / 1024x1024
    ff_enhancer: str = "gfpgan_1.4"      # codeformer, gfpgan_1.2-1.4, gpen_bfr_256-2048, restoreformer_plus_plus
    ff_enhancer_blend: int = 80          # 0-100, how much enhancer to mix in


@router.post("/faceswap")
async def start_faceswap(body: FaceswapStartBody):
    input_path = Path(body.input_path).expanduser()
    if not input_path.exists():
        raise HTTPException(400, f"Input video not found: {input_path}")

    # Pick engine; auto-downgrade if FaceFusion not installed
    engine = body.engine
    if engine == "facefusion" and not _facefusion_available():
        logger.warning("FaceFusion requested but not installed; falling back to inswapper")
        engine = "inswapper"

    if engine == "inswapper" and not _DEEPFAKE_CLI.exists():
        raise HTTPException(500, f"deepfake.py not found at {_DEEPFAKE_CLI}")

    # Build output path in advance so the UI knows where to look
    target_slug = body.target.lower().replace(" ", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "ff" if engine == "facefusion" else "swap"
    output_path = _MEDIA_ROOT / f"{input_path.stem}_{suffix}_{target_slug}_{ts}.mp4"

    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "state": "starting",
            "percent": 0.0,
            "frame": 0,
            "total_frames": 0,
            "fps": 0.0,
            "eta_s": 0.0,
            "input_path": str(input_path),
            "output_path": str(output_path),
            "target": body.target,
            "engine": engine,
            "started_at": time.time(),
            "log_tail": [],
            "error": None,
        }

    env_extra: dict = {}
    if engine == "facefusion":
        target_face = _facefusion_target_path(body.target)
        if target_face is None:
            raise HTTPException(404, f"Unknown target preset: {body.target!r}")
        ff_py = _FACEFUSION_VENV / "Scripts" / "python.exe"
        ff_cli = _FACEFUSION_ROOT / "facefusion.py"
        # MAX-Quality preset: hyperswap_1c (best identity) at 1024px pixel-boost
        # + GFPGAN 1.4 face enhancer (sharpens skin/eyes/teeth) +
        # peppa_wutz landmarker (more stable eye tracking) +
        # bisenet_resnet_34 face-parser (cleaner mask edges) +
        # box+occlusion mask (hand/glasses occluded properly) +
        # blur 0.4 for soft blend seam.
        args = [
            str(ff_py), str(ff_cli), "headless-run",
            "-s", str(target_face),
            "-t", str(input_path),
            "-o", str(output_path),
            "--processors", "face_swapper", "face_enhancer",
            "--face-swapper-model", body.ff_model,
            "--face-swapper-pixel-boost", body.ff_pixel_boost,
            "--face-enhancer-model", body.ff_enhancer,
            "--face-enhancer-blend", str(body.ff_enhancer_blend),
            "--face-detector-model", "retinaface",
            "--face-detector-score", "0.6",
            "--face-landmarker-model", "peppa_wutz",
            "--face-parser-model", "bisenet_resnet_34",
            "--face-mask-types", "box", "occlusion",
            "--face-mask-blur", "0.4",
            "--execution-providers", "cuda",
        ]
        env_extra = _facefusion_env()
    else:
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
        args=(job_id, args, env_extra, engine),
        daemon=True,
        name=f"faceswap-{job_id}",
    ).start()

    return {"job_id": job_id, "output_path": str(output_path), "engine": engine}


# FaceFusion tqdm progress regex
#   processing:  45%|====      | 29/65 [00:19<00:06,  5.82frame/s, ...]
_FF_PROGRESS_RE = re.compile(
    r"processing:\s*(\d+)%\|[^|]*\|\s*(\d+)/(\d+)\s*\[(\d+:\d+)<(\d+:\d+),\s*([\d.]+)\s*frame/s"
)


def _run_job(job_id: str, args: list, env_extra: Optional[dict] = None,
             engine: str = "inswapper") -> None:
    """Run subprocess, parse progress lines, update job state."""
    try:
        env = env_extra if env_extra else os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        cwd = str(_FACEFUSION_ROOT) if engine == "facefusion" else str(_DEEPFAKE_DIR)
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=cwd,
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
            log_buf = log_buf[-30:]

            updated = False
            if engine == "facefusion":
                m = _FF_PROGRESS_RE.search(line)
                if m:
                    _set_job(
                        job_id,
                        percent=float(m.group(1)),
                        frame=int(m.group(2)),
                        total_frames=int(m.group(3)),
                        fps=float(m.group(6)),
                        log_tail=list(log_buf),
                    )
                    updated = True
            else:
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
                    updated = True
            if not updated:
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
    "swap_target": None,     # display name / slug, or None for no post-swap
}
_LIVE_REC_LOCK = threading.Lock()


class LiveRecordStartBody(BaseModel):
    source: str = "eyeterm"   # only "eyeterm" supported for now (MJPEG :8099)
    name_hint: Optional[str] = None
    # Optional: after stop, auto-run faceswap with this target. Display name
    # (Marshall) or slug (face101). Result lands as a separate _swap_*.mp4
    # in the gallery — the raw recording stays alongside it.
    swap_target: Optional[str] = None


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
        _LIVE_REC["swap_target"] = body.swap_target

    return {
        "active": True,
        "started_at": _LIVE_REC["started_at"],
        "output_path": str(output_path),
        "source": body.source,
        "swap_target": body.swap_target,
    }


@router.post("/live-record/stop")
async def live_record_stop():
    with _LIVE_REC_LOCK:
        proc = _LIVE_REC["proc"]
        output_path = _LIVE_REC["output_path"]
        started_at = _LIVE_REC["started_at"]
        swap_target = _LIVE_REC["swap_target"]

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
        _LIVE_REC["swap_target"] = None

    size_mb = 0.0
    if output_path and Path(output_path).exists():
        size_mb = round(Path(output_path).stat().st_size / (1024 * 1024), 2)

    # If user picked a swap target during recording, kick off a faceswap
    # job on the freshly recorded mp4. Prefer FaceFusion if available
    # (better quality: hair + ear + forehead masking, hyperswap_1c_256).
    swap_job_id: Optional[str] = None
    if swap_target and output_path and Path(output_path).exists():
        try:
            engine = "facefusion" if _facefusion_available() else "inswapper"
            start_body = FaceswapStartBody(
                input_path=str(output_path),
                target=swap_target,
                no_audio=False,
                engine=engine,
            )
            r = await start_faceswap(start_body)
            swap_job_id = r.get("job_id")
        except Exception as e:
            logger.warning("Auto-faceswap dispatch failed: %s", e)

    return {
        "active": False,
        "stopped": True,
        "output_path": str(output_path) if output_path else None,
        "duration_s": duration,
        "size_mb": size_mb,
        "swap_job_id": swap_job_id,
        "swap_target": swap_target,
    }
