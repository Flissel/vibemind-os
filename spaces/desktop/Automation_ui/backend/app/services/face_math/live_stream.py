"""Phase D — live region-math swap stream.

Reads eyeTerm's MJPEG, runs FaceLandmarker per frame, fetches a cached
target face (Marshall_v5 / Eminem) from Supabase ONCE per stream, runs
the region-composite engine, re-encodes JPEG → MJPEG output.

The output is itself a multipart/x-mixed-replace stream that the
FastAPI router wraps as a StreamingResponse — same shape as the
existing inswapper /swap-stream.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import httpx
import numpy as np
import psycopg2

from .compositor import compose, compose_fast
from .landmark_detector import FaceLandmarkDetector, LandmarkResult
from .regions import SWAP_PROFILES

logger = logging.getLogger(__name__)

# Same upstream contract as the reference-recorder: pull frames from the
# backend proxy (NOT directly :8099) so eyeTerm's one-connection limit
# doesn't bite us. Caller can override via env for testing.
DEFAULT_UPSTREAM = os.environ.get(
    "FACE_MATH_STREAM_UPSTREAM",
    "http://127.0.0.1:8007/api/eyeterm/stream",
)

# Supabase Postgres for face_target source images.
_DB_URL = os.environ.get(
    "FACE_DB_URL",
    "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
)


@dataclass
class TargetFace:
    """A cached source face with its precomputed landmarks."""

    target_id: str
    image_bgr: np.ndarray
    landmarks_px: np.ndarray  # (478, 2)


class TargetCache:
    """Process-wide cache: fetch face_targets from Supabase once, reuse."""

    _instance: Optional["TargetCache"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._cache: dict[str, TargetFace] = {}
        self._cache_lock = threading.Lock()

    @classmethod
    def get(cls) -> "TargetCache":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def fetch(self, target_id: str) -> Optional[TargetFace]:
        with self._cache_lock:
            if target_id in self._cache:
                return self._cache[target_id]

        # 1. Look up primary_image_path for this target
        try:
            with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
                cur.execute(
                    "select primary_image_path from public.face_targets "
                    "where id = %s and enabled = true",
                    (target_id,),
                )
                row = cur.fetchone()
                if not row:
                    logger.warning("face_math: target %s not in face_targets", target_id)
                    return None
                storage_path = row[0]
                # 2. Load the blob
                cur.execute(
                    "select bytes from public.face_target_blobs where storage_path = %s",
                    (storage_path,),
                )
                blob_row = cur.fetchone()
                if not blob_row:
                    logger.warning(
                        "face_math: target %s storage %s missing in blobs",
                        target_id, storage_path,
                    )
                    return None
                data = bytes(blob_row[0])
        except psycopg2.Error as e:
            logger.error("face_math: supabase fetch failed for %s: %s", target_id, e)
            return None

        # 3. Decode and detect landmarks
        arr = np.frombuffer(data, dtype=np.uint8)
        image_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image_bgr is None:
            logger.warning("face_math: target %s blob is not a valid image", target_id)
            return None
        detector = FaceLandmarkDetector.get()
        result = detector.detect(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
        if result is None:
            logger.warning("face_math: no face detected in target %s", target_id)
            return None

        face = TargetFace(
            target_id=target_id,
            image_bgr=image_bgr,
            landmarks_px=result.landmarks_px,
        )
        with self._cache_lock:
            self._cache[target_id] = face
        logger.info(
            "face_math: cached target %s (image %dx%d, 478 landmarks)",
            target_id, image_bgr.shape[1], image_bgr.shape[0],
        )
        return face


def parse_mjpeg(chunks):
    """Yield decoded BGR frames from a multipart MJPEG byte-stream."""
    buf = bytearray()
    for chunk in chunks:
        buf.extend(chunk)
        while True:
            start = buf.find(b"\xff\xd8")
            end = buf.find(b"\xff\xd9", start + 2)
            if start == -1 or end == -1:
                break
            jpeg = bytes(buf[start : end + 2])
            del buf[: end + 2]
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is not None:
                yield frame


def encode_mjpeg_frame(bgr: np.ndarray, quality: int = 80) -> bytes:
    """Encode one BGR frame as a multipart/x-mixed-replace chunk."""
    ok, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return b""
    body = jpeg.tobytes()
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n"
        + body + b"\r\n"
    )


def passthrough_frame(bgr: np.ndarray) -> bytes:
    """Pass the raw target frame through (used when no face is detected)."""
    return encode_mjpeg_frame(bgr, quality=80)


def _frame_iter_webcam(device_index: int = 0):
    """Local cv2.VideoCapture source — synchronous generator yielding
    BGR frames. Useful for Phase-D live tests without depending on
    eyeTerm's MJPEG server.

    Trade-off: holds an exclusive lock on the webcam device for the
    duration of the stream. If eyeTerm or any other process also wants
    the camera, one of them will fail.
    """
    cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        # Fall back to default backend
        cap = cv2.VideoCapture(device_index)
    if not cap.isOpened():
        raise RuntimeError(f"webcam device {device_index} could not be opened")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield frame
    finally:
        cap.release()


async def region_math_stream(
    target_id: str,
    profile: str = "inner_face",
    blend_mode: str = "alpha",
    color_match_method: str = "histogram",
    feather_px: int = 12,
    warp_method: str = "affine",
    upstream: str = DEFAULT_UPSTREAM,
    quality: int = 78,
    source: str = "eyeterm",
    webcam_device: int = 0,
):
    """Async generator yielding MJPEG chunks.

    Args:
        target_id: face_target id from Supabase (e.g. 'marshall_v5')
        profile: SWAP_PROFILES key (default 'inner_face')
        blend_mode: 'alpha' (default), 'poisson', 'hybrid'
        color_match_method: 'histogram' (default), 'mean_std'
        feather_px: mask edge softening
        warp_method: 'affine' (default — ~2ms, visually 99% as good as TPS
            for typical webcam poses) or 'tps' (~1500ms, pixel-exact, use
            for preview/static frames only)
        upstream: source MJPEG URL (default = backend's own eyeTerm proxy)
        quality: output JPEG quality (1-100)

    Yields:
        Multipart frame chunks for FastAPI's StreamingResponse.
    """
    if profile not in SWAP_PROFILES:
        raise ValueError(f"unknown profile: {profile!r} (known: {list(SWAP_PROFILES)})")

    # Load target face up front — fail loud if missing rather than
    # silently passing through every frame.
    cache = TargetCache.get()
    target_face = cache.fetch(target_id)
    if target_face is None:
        raise RuntimeError(
            f"face-math: target {target_id!r} not found or no face detected"
        )

    detector = FaceLandmarkDetector.get()

    # Stats every 60 frames
    frame_count = 0
    detected_count = 0
    t0 = time.time()
    last_log = t0

    def _process_frame(frame_bgr: np.ndarray) -> bytes:
        nonlocal frame_count, detected_count, last_log
        frame_count += 1
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        user_result = detector.detect(rgb)
        if user_result is None:
            return passthrough_frame(frame_bgr)
        detected_count += 1
        try:
            if warp_method == "affine" and blend_mode == "alpha" and color_match_method == "histogram":
                composed_bgr = compose_fast(
                    target_frame_bgr=frame_bgr,
                    target_landmarks_px=user_result.landmarks_px,
                    source_image_bgr=target_face.image_bgr,
                    source_landmarks_px=target_face.landmarks_px,
                    profile=profile,
                    feather_px=feather_px,
                    blend_mode=blend_mode,
                )
            else:
                result = compose(
                    target_frame_bgr=frame_bgr,
                    target_landmarks_px=user_result.landmarks_px,
                    source_image_bgr=target_face.image_bgr,
                    source_landmarks_px=target_face.landmarks_px,
                    profile=profile,
                    feather_px=feather_px,
                    warp_method=warp_method,
                    apply_color_match=True,
                    color_match_method=color_match_method,
                    blend_mode=blend_mode,
                )
                composed_bgr = result.composite
        except Exception as e:
            logger.exception("face_math: compose failed: %s", e)
            return passthrough_frame(frame_bgr)
        out = encode_mjpeg_frame(composed_bgr, quality=quality)
        now = time.time()
        if now - last_log >= 5.0:
            fps = frame_count / max(now - t0, 1e-6)
            det_rate = detected_count / max(frame_count, 1)
            logger.info(
                "face_math stream %s/%s: %d frames, %.1f fps, %.0f%% face-detect",
                target_id, profile, frame_count, fps, det_rate * 100,
            )
            last_log = now
        return out

    # ----- Source selection ----------------------------------------
    if source == "webcam":
        # Local cv2.VideoCapture — bypasses eyeTerm entirely.
        import asyncio
        loop = asyncio.get_event_loop()
        for frame in _frame_iter_webcam(webcam_device):
            chunk = await loop.run_in_executor(None, _process_frame, frame)
            yield chunk
        return

    # Default: MJPEG over HTTP (eyeTerm proxy)
    # httpx.Timeout needs either a default or all four explicit params.
    # connect bounded (10s), read None so the long-lived MJPEG stream
    # never times out mid-frame.
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    )
    try:
        async with client.stream("GET", upstream) as response:
            if response.status_code != 200:
                raise RuntimeError(
                    f"upstream {upstream} returned {response.status_code}"
                )
            buf = bytearray()
            async for chunk in response.aiter_raw():
                buf.extend(chunk)
                while True:
                    start = buf.find(b"\xff\xd8")
                    end = buf.find(b"\xff\xd9", start + 2)
                    if start == -1 or end == -1:
                        break
                    jpeg = bytes(buf[start : end + 2])
                    del buf[: end + 2]
                    arr = np.frombuffer(jpeg, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue

                    frame_count += 1
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    user_result = detector.detect(rgb)
                    if user_result is None:
                        yield passthrough_frame(frame)
                        continue
                    detected_count += 1

                    try:
                        if warp_method == "affine" and blend_mode == "alpha" and color_match_method == "histogram":
                            # Hot path: ~90ms full frame, 10-11 fps.
                            composed_bgr = compose_fast(
                                target_frame_bgr=frame,
                                target_landmarks_px=user_result.landmarks_px,
                                source_image_bgr=target_face.image_bgr,
                                source_landmarks_px=target_face.landmarks_px,
                                profile=profile,
                                feather_px=feather_px,
                                blend_mode=blend_mode,
                            )
                        else:
                            # Slow path: full compose() with whatever the
                            # user explicitly requested (TPS, poisson, …).
                            result = compose(
                                target_frame_bgr=frame,
                                target_landmarks_px=user_result.landmarks_px,
                                source_image_bgr=target_face.image_bgr,
                                source_landmarks_px=target_face.landmarks_px,
                                profile=profile,
                                feather_px=feather_px,
                                warp_method=warp_method,
                                apply_color_match=True,
                                color_match_method=color_match_method,
                                blend_mode=blend_mode,
                            )
                            composed_bgr = result.composite
                        yield encode_mjpeg_frame(composed_bgr, quality=quality)
                    except Exception as e:
                        logger.exception("face_math: compose failed: %s", e)
                        yield passthrough_frame(frame)

                    now = time.time()
                    if now - last_log >= 5.0:
                        fps = frame_count / max(now - t0, 1e-6)
                        det_rate = detected_count / max(frame_count, 1)
                        logger.info(
                            "face_math stream %s/%s: %d frames, %.1f fps, %.0f%% face-detect",
                            target_id, profile, frame_count, fps, det_rate * 100,
                        )
                        last_log = now
    finally:
        await client.aclose()
