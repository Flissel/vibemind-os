"""Phase A — capture a user's reference take.

Pulls N seconds of MJPEG frames from eyeTerm (:8099/stream), runs the
FaceLandmarker on each, and persists:

  references/{take_id}/
    frames/000.jpg ... 599.jpg      (raw frames @ original res, 30fps)
    landmarks.npz                   (N, 478, 3) + blendshapes + transforms
    motion_stats.json               (mean/std/range per landmark + blendshape)
    preview.jpg                     (representative frame with overlay)

A take_id is a short UUID. Concurrent takes are rejected with 409 (only
one capture at a time per process — MediaPipe IMAGE-mode is single-call).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import httpx
import numpy as np

from .landmark_detector import FaceLandmarkDetector, LandmarkResult

logger = logging.getLogger(__name__)

# Persistent root for references. Lives on E: alongside the other host-
# native ML artifacts (see tasks/host-native-ml-tools.md) — not in the
# repo, not in the venv.
REFS_ROOT = Path("E:/Vibemind_Tools/face_math/references")

# Default upstream — the backend's own /api/eyeterm/stream proxy.
# Going through the proxy (not :8099 directly) is mandatory because
# eyeTerm's MJPEG server only accepts ONE concurrent connection — the
# Electron Video Studio is already holding it. The backend proxy fans
# the stream out to multiple subscribers.
DEFAULT_STREAM_URL = "http://127.0.0.1:8007/api/eyeterm/stream"


@dataclass
class TakeStatus:
    take_id: str
    state: str  # "running" | "done" | "failed"
    seconds_target: float
    frames_captured: int = 0
    frames_with_face: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    output_dir: Optional[str] = None


class ReferenceRecorder:
    """Process-wide singleton: one active take at a time."""

    _instance: Optional["ReferenceRecorder"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._active: Optional[TakeStatus] = None
        self._active_lock = threading.Lock()
        self._takes: dict[str, TakeStatus] = {}

    @classmethod
    def get(cls) -> "ReferenceRecorder":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def status(self, take_id: str) -> Optional[TakeStatus]:
        return self._takes.get(take_id)

    def list_takes(self) -> list[TakeStatus]:
        return list(self._takes.values())

    def start(
        self,
        seconds: float = 20.0,
        stream_url: str = DEFAULT_STREAM_URL,
    ) -> TakeStatus:
        """Kick off a capture. Returns immediately; runs in a worker thread."""
        if seconds <= 0 or seconds > 120:
            raise ValueError("seconds must be in (0, 120]")
        with self._active_lock:
            if self._active and self._active.state == "running":
                raise RuntimeError(
                    f"reference take already running: {self._active.take_id}"
                )
            take_id = uuid.uuid4().hex[:12]
            status = TakeStatus(
                take_id=take_id, state="running", seconds_target=seconds
            )
            self._active = status
            self._takes[take_id] = status

        thread = threading.Thread(
            target=self._run, args=(status, stream_url), name=f"refcap-{take_id}"
        )
        thread.daemon = True
        thread.start()
        return status

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _run(self, status: TakeStatus, stream_url: str) -> None:
        out_dir = REFS_ROOT / status.take_id
        frames_dir = out_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        status.output_dir = str(out_dir)

        detector = FaceLandmarkDetector.get()

        norms: list[np.ndarray] = []
        blends: list[dict[str, float]] = []
        transforms: list[np.ndarray] = []
        timestamps: list[float] = []
        first_face_frame: Optional[np.ndarray] = None
        first_face_landmarks: Optional[LandmarkResult] = None

        deadline = time.time() + status.seconds_target

        try:
            with httpx.Client(timeout=httpx.Timeout(5.0, read=None)) as client:
                with client.stream("GET", stream_url) as response:
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"stream returned {response.status_code} — is eyeTerm running on :8099?"
                        )
                    for frame_bgr in self._parse_mjpeg(response.iter_raw()):
                        if time.time() >= deadline:
                            break
                        status.frames_captured += 1

                        # Save raw frame
                        frame_idx = status.frames_captured - 1
                        cv2.imwrite(
                            str(frames_dir / f"{frame_idx:04d}.jpg"),
                            frame_bgr,
                            [cv2.IMWRITE_JPEG_QUALITY, 88],
                        )

                        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                        result = detector.detect(rgb)
                        if result is None:
                            # still record the frame index so the timeline
                            # is gap-aware downstream
                            norms.append(np.full((478, 3), np.nan, dtype=np.float32))
                            blends.append({})
                            transforms.append(
                                np.full((4, 4), np.nan, dtype=np.float32)
                            )
                        else:
                            status.frames_with_face += 1
                            norms.append(result.landmarks_norm)
                            blends.append(result.blendshapes)
                            transforms.append(
                                result.transform
                                if result.transform is not None
                                else np.full((4, 4), np.nan, dtype=np.float32)
                            )
                            if first_face_frame is None:
                                first_face_frame = frame_bgr.copy()
                                first_face_landmarks = result
                        timestamps.append(time.time() - status.started_at)

            # Persist arrays
            self._persist(
                out_dir,
                norms,
                blends,
                transforms,
                timestamps,
                first_face_frame,
                first_face_landmarks,
            )

            status.state = "done"
            status.finished_at = time.time()
            logger.info(
                "face_math: take %s done — %d frames, %d with face, %.1fs",
                status.take_id,
                status.frames_captured,
                status.frames_with_face,
                (status.finished_at - status.started_at),
            )

        except Exception as e:
            logger.exception("face_math: take %s failed", status.take_id)
            status.state = "failed"
            status.error = str(e)
            status.finished_at = time.time()

    @staticmethod
    def _parse_mjpeg(chunks):
        """Yield decoded BGR frames from a multipart MJPEG byte-stream."""
        buf = bytearray()
        for chunk in chunks:
            buf.extend(chunk)
            while True:
                start = buf.find(b"\xff\xd8")  # JPEG SOI
                end = buf.find(b"\xff\xd9", start + 2)  # JPEG EOI
                if start == -1 or end == -1:
                    break
                jpeg = bytes(buf[start : end + 2])
                del buf[: end + 2]
                arr = np.frombuffer(jpeg, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    yield frame

    @staticmethod
    def _persist(
        out_dir: Path,
        norms: list[np.ndarray],
        blends: list[dict[str, float]],
        transforms: list[np.ndarray],
        timestamps: list[float],
        preview_frame: Optional[np.ndarray],
        preview_landmarks: Optional[LandmarkResult],
    ) -> None:
        # All blendshape names that appeared (stable ordering)
        all_names = sorted({name for b in blends for name in b})
        blend_matrix = np.full((len(blends), len(all_names)), np.nan, dtype=np.float32)
        for i, b in enumerate(blends):
            for j, name in enumerate(all_names):
                if name in b:
                    blend_matrix[i, j] = b[name]

        landmarks_arr = np.stack(norms, axis=0) if norms else np.zeros((0, 478, 3))
        transforms_arr = (
            np.stack(transforms, axis=0) if transforms else np.zeros((0, 4, 4))
        )

        np.savez(
            out_dir / "landmarks.npz",
            landmarks_norm=landmarks_arr,
            blendshape_names=np.array(all_names, dtype=object),
            blendshape_values=blend_matrix,
            transforms=transforms_arr,
            timestamps=np.array(timestamps, dtype=np.float32),
        )

        # Motion stats — only over frames where a face was detected
        valid = ~np.isnan(landmarks_arr[:, 0, 0])
        valid_lm = landmarks_arr[valid]
        valid_blend = blend_matrix[valid]
        stats = {
            "frames_total": int(len(timestamps)),
            "frames_with_face": int(valid.sum()),
            "duration_s": float(timestamps[-1]) if timestamps else 0.0,
            "landmark_mean": valid_lm.mean(axis=0).tolist() if valid_lm.size else [],
            "landmark_std": valid_lm.std(axis=0).tolist() if valid_lm.size else [],
            "blendshape_stats": {},
        }
        for j, name in enumerate(all_names):
            col = valid_blend[:, j]
            col = col[~np.isnan(col)]
            if col.size:
                stats["blendshape_stats"][name] = {
                    "mean": float(col.mean()),
                    "std": float(col.std()),
                    "min": float(col.min()),
                    "max": float(col.max()),
                }
        (out_dir / "motion_stats.json").write_text(json.dumps(stats, indent=2))

        # Preview JPEG with overlay
        if preview_frame is not None and preview_landmarks is not None:
            preview = _draw_overlay(preview_frame, preview_landmarks.landmarks_px)
            cv2.imwrite(
                str(out_dir / "preview.jpg"),
                preview,
                [cv2.IMWRITE_JPEG_QUALITY, 92],
            )


# ---------------------------------------------------------------------------
# Overlay helper — duplicated from scripts/face_math_smoke.py so the
# backend isn't reaching outside the project tree.
# ---------------------------------------------------------------------------

_REGIONS = {
    "lips_outer": (
        [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291,
         409, 270, 269, 267, 0, 37, 39, 40, 185, 61],
        (50, 50, 255),
    ),
    "lips_inner": (
        [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308,
         415, 310, 311, 312, 13, 82, 81, 80, 191, 78],
        (50, 150, 255),
    ),
    "left_eye": (
        [263, 249, 390, 373, 374, 380, 381, 382, 362,
         263, 466, 388, 387, 386, 385, 384, 398, 362],
        (255, 200, 50),
    ),
    "right_eye": (
        [33, 7, 163, 144, 145, 153, 154, 155, 133,
         33, 246, 161, 160, 159, 158, 157, 173, 133],
        (255, 200, 50),
    ),
    "left_brow": (
        [276, 283, 282, 295, 285, 336, 296, 334, 293, 300],
        (50, 255, 50),
    ),
    "right_brow": (
        [46, 53, 52, 65, 55, 107, 66, 105, 63, 70],
        (50, 255, 50),
    ),
    "nose_bridge": (
        [168, 6, 197, 195, 5, 4, 1],
        (200, 100, 255),
    ),
    "face_oval": (
        [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361,
         288, 397, 365, 379, 378, 400, 377, 152, 148, 176, 149,
         150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103,
         67, 109, 10],
        (255, 255, 255),
    ),
}


def _draw_overlay(img_bgr: np.ndarray, pixels: np.ndarray) -> np.ndarray:
    out = img_bgr.copy()
    for x, y in pixels:
        cv2.circle(out, (int(x), int(y)), 1, (180, 180, 180), -1)
    for _, (idxs, color) in _REGIONS.items():
        pts = pixels[idxs].reshape(-1, 1, 2)
        cv2.polylines(out, [pts], isClosed=False, color=color, thickness=2)
    return out
