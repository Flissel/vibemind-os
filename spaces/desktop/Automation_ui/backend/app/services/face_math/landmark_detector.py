"""MediaPipe FaceLandmarker wrapper.

Singleton-ish: holds one Landmarker instance per process, lazily
initialised so importing this module costs nothing (the
face_landmarker.task model is ~3.6MB and only fetched on first use).

Returns 478 normalised 3D landmarks + 52 blendshapes + 4x4 facial
transformation matrix, per frame. Used by both the reference-recorder
(batch) and the live swap-stream (per-frame, hot path).
"""

from __future__ import annotations

import logging
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

logger = logging.getLogger(__name__)

# Google's signed face_landmarker model (float16, ~3.6MB). Persisted
# next to other face-math artifacts on E: so it survives venv rebuilds.
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)
_MODEL_PATH = Path("E:/Vibemind_Tools/face_math_test/face_landmarker.task")


@dataclass
class LandmarkResult:
    """One detected face from one frame."""

    landmarks_norm: np.ndarray  # (478, 3) float32 — normalised x,y,z
    landmarks_px: np.ndarray  # (478, 2) int32 — pixel coords
    blendshapes: dict[str, float]  # 52 named scores
    transform: Optional[np.ndarray]  # (4, 4) float32 facial transformation matrix
    image_size: tuple[int, int]  # (w, h)


def _ensure_model() -> Path:
    _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _MODEL_PATH.exists() and _MODEL_PATH.stat().st_size > 1_000_000:
        return _MODEL_PATH
    logger.info("face_math: downloading FaceLandmarker model -> %s", _MODEL_PATH)
    urllib.request.urlretrieve(_MODEL_URL, str(_MODEL_PATH))
    logger.info(
        "face_math: model ready (%.1f MB)",
        _MODEL_PATH.stat().st_size / 1024 / 1024,
    )
    return _MODEL_PATH


class FaceLandmarkDetector:
    """Lazy singleton wrapping mp_vision.FaceLandmarker."""

    _instance: Optional["FaceLandmarkDetector"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        model_path = _ensure_model()
        base = mp_python.BaseOptions(model_asset_path=str(model_path))
        options = mp_vision.FaceLandmarkerOptions(
            base_options=base,
            running_mode=mp_vision.RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = mp_vision.FaceLandmarker.create_from_options(options)
        self._call_lock = threading.Lock()
        logger.info("face_math: FaceLandmarker initialised")

    @classmethod
    def get(cls) -> "FaceLandmarkDetector":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def detect(self, rgb_image: np.ndarray) -> Optional[LandmarkResult]:
        """Run FaceLandmarker on a single RGB frame.

        rgb_image must be uint8 HWC. Returns None if no face found.
        Threadsafe via internal lock — MediaPipe's IMAGE-mode landmarker
        is not re-entrant.
        """
        if rgb_image.dtype != np.uint8:
            raise ValueError(f"need uint8 RGB, got {rgb_image.dtype}")
        h, w = rgb_image.shape[:2]
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)
        with self._call_lock:
            result = self._landmarker.detect(mp_image)
        if not result.face_landmarks:
            return None

        lms = result.face_landmarks[0]
        norm = np.array(
            [(lm.x, lm.y, lm.z) for lm in lms], dtype=np.float32
        )
        px = np.column_stack(
            [(norm[:, 0] * w).astype(np.int32), (norm[:, 1] * h).astype(np.int32)]
        )

        blend = {}
        if result.face_blendshapes:
            for c in result.face_blendshapes[0]:
                blend[c.category_name] = float(c.score)

        transform = None
        if result.facial_transformation_matrixes:
            transform = np.array(
                result.facial_transformation_matrixes[0], dtype=np.float32
            )

        return LandmarkResult(
            landmarks_norm=norm,
            landmarks_px=px,
            blendshapes=blend,
            transform=transform,
            image_size=(w, h),
        )
