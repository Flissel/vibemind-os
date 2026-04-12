"""ParticleAligner — measures SOLL/IST landmark error and warps swap output.

SOLL points come from eyeTerm's MediaPipe 478-landmark mesh (high temporal stability).
IST points come from insightface Face.kps (where inswapper placed the new face).
A thin-plate spline warp drags the swapped frame so IST → SOLL, eliminating the
"face-swim" drift that naive ML swappers produce.

The error state is exported in the same JSON schema used by poc_red_blue
baby_brain_sb3 _export_particles() → the existing humanoid-particles.html viewer
can render face error data unchanged.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# MediaPipe 478 indices for the 5-key subset that matches insightface kps order:
# [left_eye, right_eye, nose, left_mouth, right_mouth]
MP_5KEY_INDICES = [
    468,  # left iris center  (≈ left eye center used by insightface)
    473,  # right iris center
    1,    # nose tip
    61,   # left mouth corner
    291,  # right mouth corner
]

POINT_LABELS = ["left_eye", "right_eye", "nose", "left_mouth", "right_mouth"]


@dataclass
class ParticleState:
    """Per-frame SOLL/IST error snapshot — schema mirrors poc_red_blue particles.json."""
    timestamp: float
    step: int
    phase: str = "align"
    target_phase: str = "align"
    bodies: List[dict] = field(default_factory=list)     # IST points
    targets: List[dict] = field(default_factory=list)    # SOLL points
    errors: List[float] = field(default_factory=list)    # per-point euclidean px
    total_error: float = 0.0
    connections: List[List[int]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class ParticleAligner:
    """Measure SOLL/IST drift and warp swap output back onto SOLL geometry."""

    def __init__(
        self,
        smoothing_alpha: float = 0.6,
        debug_export_path: Optional[Path] = None,
        export_every_n: int = 3,
    ):
        self._alpha = float(smoothing_alpha)
        self._export_path = Path(debug_export_path) if debug_export_path else None
        self._export_every_n = max(1, int(export_every_n))
        self._step = 0
        self._prev_soll: Optional[np.ndarray] = None  # for temporal smoothing

    def align(
        self,
        swapped_frame: np.ndarray,
        mp_landmarks_soll,          # list[NormalizedLandmark] from GazeEstimator
        insight_face_ist,           # insightface.app.common.Face (has .kps)
    ) -> Tuple[np.ndarray, ParticleState]:
        self._step += 1
        h, w = swapped_frame.shape[:2]

        soll = self._extract_soll(mp_landmarks_soll, w, h)      # (5, 2) float32 px
        ist = np.asarray(insight_face_ist.kps, dtype=np.float32)  # (5, 2)

        # Temporal smoothing on SOLL — prevents TPS jitter when MP landmarks wobble
        if self._prev_soll is not None and self._prev_soll.shape == soll.shape:
            soll = self._alpha * self._prev_soll + (1.0 - self._alpha) * soll
        self._prev_soll = soll

        aligned = self._warp(swapped_frame, ist, soll)

        state = self._build_state(soll, ist)
        self._maybe_export(state)
        return aligned, state

    # ------------------------------------------------------------------
    def _extract_soll(self, mp_landmarks, w: int, h: int) -> np.ndarray:
        pts = np.zeros((5, 2), dtype=np.float32)
        for i, idx in enumerate(MP_5KEY_INDICES):
            lm = mp_landmarks[idx]
            pts[i, 0] = lm.x * w
            pts[i, 1] = lm.y * h
        return pts

    def _warp(self, frame: np.ndarray, ist: np.ndarray, soll: np.ndarray) -> np.ndarray:
        """Warp frame so IST points land on SOLL points.

        Prefers OpenCV TPS (contrib). Falls back to affine when TPS module
        is absent (e.g. opencv-python-headless without contrib). Affine on
        5 points is solved via least-squares — less precise than TPS but
        still corrects translation/rotation/scale drift which is the main
        source of face-swim.
        """
        delta = np.linalg.norm(soll - ist, axis=1).mean()
        if delta < 1.0:
            return frame

        tps_factory = getattr(cv2, "createThinPlateSplineShapeTransformer", None)
        if tps_factory is not None:
            try:
                tps = tps_factory()
                src = ist.reshape(1, -1, 2)
                dst = soll.reshape(1, -1, 2)
                matches = [cv2.DMatch(i, i, 0) for i in range(len(ist))]
                tps.estimateTransformation(dst, src, matches)
                return tps.warpImage(frame)
            except cv2.error as e:
                logger.debug("TPS warp failed (%s) — falling back to affine", e)

        # Affine fallback: least-squares fit through all 5 points
        try:
            M, _ = cv2.estimateAffinePartial2D(ist, soll, method=cv2.LMEDS)
            if M is None:
                return frame
            h, w = frame.shape[:2]
            return cv2.warpAffine(
                frame, M, (w, h),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
        except cv2.error as e:
            logger.debug("Affine warp failed (%s) — returning unwarped", e)
            return frame

    def _build_state(self, soll: np.ndarray, ist: np.ndarray) -> ParticleState:
        errors = np.linalg.norm(soll - ist, axis=1).tolist()
        bodies = [
            {"label": POINT_LABELS[i], "x": float(ist[i, 0]), "y": float(ist[i, 1]), "z": 0.0}
            for i in range(len(ist))
        ]
        targets = [
            {"label": POINT_LABELS[i], "x": float(soll[i, 0]), "y": float(soll[i, 1]), "z": 0.0}
            for i in range(len(soll))
        ]
        return ParticleState(
            timestamp=time.time(),
            step=self._step,
            bodies=bodies,
            targets=targets,
            errors=errors,
            total_error=float(sum(errors)),
            connections=[[0, 2], [1, 2], [2, 3], [2, 4], [3, 4]],
        )

    def _maybe_export(self, state: ParticleState) -> None:
        if self._export_path is None:
            return
        if self._step % self._export_every_n != 0:
            return
        try:
            self._export_path.parent.mkdir(parents=True, exist_ok=True)
            self._export_path.write_text(state.to_json(), encoding="utf-8")
        except OSError as e:
            logger.debug("Particle export failed: %s", e)
