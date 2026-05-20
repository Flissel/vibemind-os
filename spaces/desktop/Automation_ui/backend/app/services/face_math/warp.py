"""Geometric warping: map Eminem's face onto YOUR landmarks.

Two warp strategies, picked per use-case:

1. AFFINE — fast (one 2×3 matrix), preserves straight lines. Good when
   source and target heads have similar pose / scale. Used as the default
   in the realtime pipeline.

2. THIN-PLATE SPLINE (TPS) — slower, locally non-rigid. Bends the target
   face so its lips, eyes etc. land EXACTLY on yours. Essential for
   per-region composites where pixel-accurate alignment matters more
   than speed.

Both warps work on landmark indices (not pixels) so the same code path
works for static photos and live frames.
"""

from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

# Reduced control-point set for realtime TPS (instead of all 478).
# These 64 indices are evenly distributed over the face — gives a smooth
# warp without the slowdown of full-density TPS. Hand-picked from
# face_oval (18pts) + brows (8pts) + eyes (8pts) + nose (8pts) + lips
# (8pts) + cheeks (6pts) + chin (4pts).
TPS_CONTROL_POINTS_REDUCED = (
    # face oval
    10, 297, 284, 389, 454, 361, 397, 379, 400, 152,
    176, 150, 172, 132, 234, 162, 54, 67,
    # eyebrows
    105, 66, 107, 334, 296, 293, 70, 300,
    # eye corners + lid centers
    33, 133, 159, 145, 263, 362, 386, 374,
    # nose
    168, 6, 197, 195, 5, 4, 1, 19,
    # lips
    61, 291, 0, 17, 13, 14, 78, 308,
    # cheekbones
    116, 345, 50, 280, 425, 205,
    # chin
    18, 200, 175, 199,
)


# ---------------------------------------------------------------------------
# Affine warp (5-point similarity)
# ---------------------------------------------------------------------------

# Stable anchor points for similarity transform — eye corners + mouth corners
# + nose tip. These resist expression changes (open mouth, blink) better
# than random landmarks. Indices into the 478 FaceMesh array.
_AFFINE_ANCHORS = (
    33,    # right eye outer corner
    263,   # left eye outer corner
    1,     # nose tip
    61,    # right mouth corner
    291,   # left mouth corner
)


def estimate_affine(
    src_landmarks_px: np.ndarray,
    dst_landmarks_px: np.ndarray,
    anchor_indices: Sequence[int] = _AFFINE_ANCHORS,
) -> np.ndarray:
    """Best-fit 2×3 affine matrix mapping src → dst, using anchor points.

    Args:
        src_landmarks_px: (478, 2) source face landmarks
        dst_landmarks_px: (478, 2) destination face landmarks
        anchor_indices: which landmarks to use for the fit (default: 5 stable points)

    Returns:
        (2, 3) float32 affine matrix — apply via cv2.warpAffine.
    """
    src = src_landmarks_px[list(anchor_indices)].astype(np.float32)
    dst = dst_landmarks_px[list(anchor_indices)].astype(np.float32)
    # estimateAffinePartial2D = similarity (rotation + uniform scale + translation)
    # — exactly what we want, no shearing artifacts
    matrix, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC)
    if matrix is None:
        # fallback to least-squares affine
        matrix, _ = cv2.estimateAffine2D(src, dst)
    return matrix.astype(np.float32)


def apply_affine(
    image: np.ndarray, matrix: np.ndarray, dst_size: tuple[int, int]
) -> np.ndarray:
    """Warp image with a 2×3 affine, output at dst_size = (W, H).

    Uses BORDER_CONSTANT (=0/black) outside the source — the compositor
    masks the output anyway, so reflected pixels would only create
    artefacts when the soft α-edge picks them up. Black is harmless: the
    α-blend = source_warped * mask, and 0 * anything = 0.
    """
    return cv2.warpAffine(
        image, matrix, dst_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )


def affine_warp_landmarks(
    landmarks_px: np.ndarray, matrix: np.ndarray
) -> np.ndarray:
    """Apply the same affine to a (N, 2) landmark array. Useful for
    checking how well the warp aligns source landmarks to target."""
    ones = np.ones((landmarks_px.shape[0], 1), dtype=np.float32)
    pts = np.concatenate([landmarks_px.astype(np.float32), ones], axis=1)
    warped = pts @ matrix.T  # (N, 2)
    return warped


# ---------------------------------------------------------------------------
# Thin-Plate Spline warp
# ---------------------------------------------------------------------------

class TPSWarp:
    """Wraps cv2.createThinPlateSplineShapeTransformer.

    Use this when you need pixel-accurate region alignment (e.g. landing
    Eminem's lips EXACTLY on yours), not just rough head-pose match.

    Build once with N control-point pairs, apply many times to images +
    landmarks. The transformer learns a smooth deformation that maps
    every src control point exactly onto its dst pair.
    """

    def __init__(
        self,
        src_landmarks_px: np.ndarray,
        dst_landmarks_px: np.ndarray,
        control_indices: Sequence[int] | None = None,
        regularisation: float = 0.0,
    ) -> None:
        """
        Args:
            src_landmarks_px: (478, 2) source landmarks
            dst_landmarks_px: (478, 2) destination landmarks
            control_indices: subset of landmark indices to use as control
                points. None = use all 478 (slow but accurate). For realtime
                pick 30-60 well-distributed indices.
            regularisation: 0 = exact interpolation; >0 = smoother but loses
                pixel-perfect anchor matching. Default 0 for static composites.
        """
        if control_indices is None:
            control_indices = list(range(src_landmarks_px.shape[0]))
        else:
            control_indices = list(control_indices)

        # Step 1: similarity pre-alignment matrix. Brings the source's
        # 5-anchor cluster to the destination's, in destination pixel
        # space. Without this, warp_image() gets a TPS deformation
        # whose output sits outside the source's canvas — black hole.
        self._pre_align_matrix = estimate_affine(
            src_landmarks_px, dst_landmarks_px
        )
        # Apply the pre-alignment to the SOURCE control points so the
        # subsequent TPS estimates the residual deformation only.
        ones = np.ones((src_landmarks_px.shape[0], 1), dtype=np.float32)
        pts = np.concatenate([src_landmarks_px.astype(np.float32), ones], axis=1)
        src_prealigned = (pts @ self._pre_align_matrix.T).astype(np.float32)

        src = src_prealigned[control_indices].reshape(1, -1, 2)
        dst = dst_landmarks_px[control_indices].astype(np.float32).reshape(1, -1, 2)
        matches = [cv2.DMatch(i, i, 0) for i in range(len(control_indices))]

        self._tps = cv2.createThinPlateSplineShapeTransformer(regularisation)
        # cv2's TPS estimates dst→src warp (inverse) by convention; we pass
        # source as "template" and dst as "target"
        self._tps.estimateTransformation(dst, src, matches)
        self._dst_size = (
            int(dst_landmarks_px[:, 0].max()) + 1,
            int(dst_landmarks_px[:, 1].max()) + 1,
        )

    def warp_image(
        self, source_image: np.ndarray, dst_size: tuple[int, int] | None = None
    ) -> np.ndarray:
        """Warp source_image so it lands on the destination geometry.

        dst_size = (W, H). If None, infer from destination landmarks bbox.

        Implementation detail: cv2's TPS.warpImage() returns the warped
        image at the SOURCE size, not the destination. If source and
        destination resolutions differ (Marshall photo 1024² vs your
        1080p frame), the warped source pixels would shift OUTSIDE the
        source canvas and arrive black. Fix: first paint the source into
        a destination-sized canvas using an affine pre-alignment, THEN
        run TPS on that canvas. Now warpImage sees source and target in
        the same coordinate space.

        BORDER_CONSTANT (=black) outside the source bounds: masked
        compositing zeroes those pixels anyway.
        """
        size = dst_size or self._dst_size
        sw, sh = source_image.shape[1], source_image.shape[0]
        dw, dh = size

        if (sw, sh) != (dw, dh):
            # Pre-align source into a destination-sized canvas via affine.
            # Use the same 5-anchor similarity used by AFFINE warp — the
            # subsequent TPS will pick up the remaining non-rigid deformation.
            pre_matrix = self._pre_align_matrix
            source_canvas = cv2.warpAffine(
                source_image, pre_matrix, (dw, dh),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(0, 0, 0),
            )
        else:
            source_canvas = source_image

        warped = self._tps.warpImage(
            source_canvas,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        if warped.shape[:2] != (dh, dw):
            warped = cv2.resize(warped, (dw, dh), interpolation=cv2.INTER_LINEAR)
        return warped


# ---------------------------------------------------------------------------
# Convenience high-level: take source RGB + landmarks and target landmarks,
# return source image warped onto target frame geometry.
# ---------------------------------------------------------------------------

def warp_source_to_target(
    source_image_bgr: np.ndarray,
    source_landmarks_px: np.ndarray,
    target_landmarks_px: np.ndarray,
    target_size: tuple[int, int],
    method: str = "tps",
    tps_control_indices: Sequence[int] | None = None,
) -> np.ndarray:
    """One-call API used by the compositor.

    Args:
        source_image_bgr: e.g. Eminem photo (H_src, W_src, 3)
        source_landmarks_px: (478, 2) for the source image
        target_landmarks_px: (478, 2) for the target (your) frame
        target_size: (W, H) of the output canvas
        method: "affine" (fast, rigid) or "tps" (pixel-exact, ~5x slower)
        tps_control_indices: which landmarks to use as TPS control points.
            Default = reduced 64-point set (fast + still pixel-accurate
            for the face). Set to range(478) for full density.

    Returns:
        Warped source image at target_size, ready for masked compositing.

    Note on TPS distortion: TPS without locality bleeds into the
    background. Caller is expected to mask the output — pixels outside
    the mask should never be displayed. Bbox-localizing the warp is the
    compositor's responsibility, not this function's.
    """
    if method == "affine":
        matrix = estimate_affine(source_landmarks_px, target_landmarks_px)
        return apply_affine(source_image_bgr, matrix, target_size)
    elif method == "tps":
        control = (
            tps_control_indices
            if tps_control_indices is not None
            else TPS_CONTROL_POINTS_REDUCED
        )
        tps = TPSWarp(
            source_landmarks_px,
            target_landmarks_px,
            control_indices=control,
        )
        return tps.warp_image(source_image_bgr, target_size)
    raise ValueError(f"unknown warp method: {method!r}")
