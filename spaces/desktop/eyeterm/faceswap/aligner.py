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
#
# IMPORTANT: we deliberately use eye-OUTER-corners (33/263), NOT iris centres
# (468/473). Iris centres move with the eyeball during saccades/blinks — they
# would force the TPS warp to jitter the whole face every time the user looks
# around. Outer eye corners are bone-anchored and stable.
# insightface's kps order: [left_eye_center, right_eye_center, nose, L_mouth, R_mouth]
# MediaPipe has no stable single "eye center" landmark (iris 468/473 moves with
# eyeball), so we synthesise one as the midpoint of outer+inner bone-anchored
# eye corners — frame-stable through saccades and blinks.

MP_NOSE_TIP = 1
MP_MOUTH_LEFT = 61
MP_MOUTH_RIGHT = 291
MP_LEFT_EYE_OUTER = 33
MP_LEFT_EYE_INNER = 133
MP_RIGHT_EYE_INNER = 362
MP_RIGHT_EYE_OUTER = 263

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
        smoothing_alpha: float = 0.8,
        debug_export_path: Optional[Path] = None,
        export_every_n: int = 3,
    ):
        self._alpha = float(smoothing_alpha)
        self._export_path = Path(debug_export_path) if debug_export_path else None
        self._export_every_n = max(1, int(export_every_n))
        self._step = 0
        self._prev_soll: Optional[np.ndarray] = None  # EMA-smoothed SOLL
        self._prev_ist: Optional[np.ndarray] = None   # EMA-smoothed IST

    def align(
        self,
        swapped_frame: np.ndarray,
        mp_landmarks_soll,          # list[NormalizedLandmark] from GazeEstimator
        insight_face_ist,           # insightface.app.common.Face (has .kps + bbox)
    ) -> Tuple[np.ndarray, ParticleState]:
        self._step += 1
        h, w = swapped_frame.shape[:2]

        soll = self._extract_soll(mp_landmarks_soll, w, h)      # (5, 2) float32 px
        ist = np.asarray(insight_face_ist.kps, dtype=np.float32)  # (5, 2)

        # Temporal smoothing on BOTH SOLL + IST — prevents TPS jitter from either
        # side's frame-to-frame wobble. alpha near 1.0 = heavy smoothing (calm),
        # near 0.0 = responsive (twitchy). 0.8 is a good default for face swap.
        if self._prev_soll is not None and self._prev_soll.shape == soll.shape:
            soll = self._alpha * self._prev_soll + (1.0 - self._alpha) * soll
        self._prev_soll = soll
        if self._prev_ist is not None and self._prev_ist.shape == ist.shape:
            ist = self._alpha * self._prev_ist + (1.0 - self._alpha) * ist
        self._prev_ist = ist

        # IMPORTANT: only warp the face region, not the whole frame. A full-frame
        # TPS warp causes visible background wobble (every landmark jitter
        # propagates to every background pixel). We extract an expanded bounding
        # box around the face, warp that subpatch, then blend it back in with a
        # soft-edge mask.
        aligned = self._warp_face_region(swapped_frame, ist, soll, insight_face_ist)

        state = self._build_state(soll, ist)
        self._maybe_export(state)
        return aligned, state

    def _warp_face_region(
        self,
        frame: np.ndarray,
        ist: np.ndarray,
        soll: np.ndarray,
        face,
    ) -> np.ndarray:
        """Warp only the face bbox region, composite back with soft-edge blend.

        - Expand bbox 30% for safety margin (warp can push pixels out of the
          original bbox rectangle).
        - Warp the subpatch using the landmark deltas (relative to the patch).
        - Blend back using a feathered ellipse mask so the patch boundary is
          invisible.
        """
        h, w = frame.shape[:2]
        delta = float(np.linalg.norm(soll - ist, axis=1).mean())
        if delta < 1.0:
            return frame

        # Expand bbox by 30% around its centre
        x1, y1, x2, y2 = face.bbox.astype(float)
        cx, cy = (x1 + x2) * 0.5, (y1 + y2) * 0.5
        bw = (x2 - x1) * 1.6
        bh = (y2 - y1) * 1.6
        px1 = max(0, int(cx - bw * 0.5))
        py1 = max(0, int(cy - bh * 0.5))
        px2 = min(w, int(cx + bw * 0.5))
        py2 = min(h, int(cy + bh * 0.5))
        if px2 - px1 < 20 or py2 - py1 < 20:
            return frame

        # Landmarks translated into patch coordinates
        patch_ist = ist - np.array([px1, py1], dtype=np.float32)
        patch_soll = soll - np.array([px1, py1], dtype=np.float32)

        patch = frame[py1:py2, px1:px2].copy()
        warped_patch = self._warp(patch, patch_ist, patch_soll)
        if warped_patch is patch:
            return frame  # warp was skipped (delta < 1 px)

        # Soft-edge ellipse mask so the patch boundary fades smoothly
        ph, pw = patch.shape[:2]
        mask = np.zeros((ph, pw), dtype=np.float32)
        cv2.ellipse(
            mask,
            (pw // 2, ph // 2),
            (int(pw * 0.42), int(ph * 0.46)),
            0, 0, 360, 1.0, -1,
        )
        feather = max(11, (min(pw, ph) // 12) | 1)  # odd kernel
        mask = cv2.GaussianBlur(mask, (feather, feather), 0)
        mask_3 = mask[..., None]

        blended = (warped_patch.astype(np.float32) * mask_3 +
                   patch.astype(np.float32) * (1.0 - mask_3)).astype(np.uint8)

        out = frame.copy()
        out[py1:py2, px1:px2] = blended
        return out

    # ------------------------------------------------------------------
    def _extract_soll(self, mp_landmarks, w: int, h: int) -> np.ndarray:
        """Build 5 SOLL points in insightface kps order.

        Eye centres are corner-midpoints (stable through saccades/blinks),
        not iris landmarks.
        """
        def _pt(idx):
            lm = mp_landmarks[idx]
            return lm.x * w, lm.y * h

        lxo, lyo = _pt(MP_LEFT_EYE_OUTER)
        lxi, lyi = _pt(MP_LEFT_EYE_INNER)
        rxi, ryi = _pt(MP_RIGHT_EYE_INNER)
        rxo, ryo = _pt(MP_RIGHT_EYE_OUTER)
        nx, ny = _pt(MP_NOSE_TIP)
        mlx, mly = _pt(MP_MOUTH_LEFT)
        mrx, mry = _pt(MP_MOUTH_RIGHT)

        pts = np.array([
            [(lxo + lxi) * 0.5, (lyo + lyi) * 0.5],   # left eye centre
            [(rxi + rxo) * 0.5, (ryi + ryo) * 0.5],   # right eye centre
            [nx, ny],                                  # nose tip
            [mlx, mly],                                # left mouth corner
            [mrx, mry],                                # right mouth corner
        ], dtype=np.float32)
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
