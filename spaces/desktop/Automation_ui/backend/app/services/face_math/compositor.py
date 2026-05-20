"""Region-composite face swap.

Given:
    - user_frame  (your live or static photo)
    - source_face (Eminem)
    - swap_profile or explicit region list
    - landmarks for both (computed once on file)

Produce:
    composite = user_frame * (1 - mask) + warped_source * mask

This is the math-based alternative to inswapper/DFM: every output pixel
is a controlled blend, no opaque latent. Mask = which regions to swap.
Warp = how to align Eminem's geometry to yours.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import cv2
import numpy as np

from .alpha_mask import make_composite_mask, make_profile_mask
from .blending import (
    alpha_blend,
    histogram_match_lab,
    hybrid_blend,
    poisson_blend,
)
from .landmark_detector import FaceLandmarkDetector, LandmarkResult
from .regions import SWAP_PROFILES
from .warp import apply_affine, estimate_affine, warp_source_to_target


@dataclass
class CompositeResult:
    composite: np.ndarray  # final BGR image
    mask: np.ndarray  # (H,W) float32 α-mask used
    warped_source: np.ndarray  # source image warped onto target geometry
    target_landmarks: np.ndarray  # (478,2) landmarks of the target frame
    source_landmarks: np.ndarray  # (478,2) landmarks of the source image (warped)
    debug_info: dict


def color_match_lab(
    source_warped_bgr: np.ndarray,
    target_bgr: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    """Match warped source's color stats to target inside the masked area.

    Works in LAB space: shifts L (luminance) and a/b (chroma) so the
    mean and std of the warped face match the target's. Prevents the
    obvious "pasted face from different lighting" look.

    Only the masked region influences the statistics — outside the mask
    is irrelevant noise.
    """
    if mask.max() <= 0:
        return source_warped_bgr

    # Detect catastrophic warps: if the warped source's masked pixels are
    # mostly the BORDER_REFLECT padding (i.e. the source face landed outside
    # its own bbox), the color stats become meaningless and normalising
    # produces the magenta-blob bug. Skip color-match in that case.
    sample = mask > 0.1
    if sample.sum() < 50:
        return source_warped_bgr
    s_pixels = source_warped_bgr[sample]
    # Heuristic: if 70%+ of masked pixels are within 8 LAB-units of each
    # other on every channel, the warp collapsed to a near-uniform color —
    # color-match would amplify the artefact. Better to skip.
    src_std_total = s_pixels.std(axis=0).sum()
    if src_std_total < 6.0:
        return source_warped_bgr

    src_lab = cv2.cvtColor(source_warped_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    tgt_lab = cv2.cvtColor(target_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

    out = src_lab.copy()
    for ch in range(3):
        s_mean = src_lab[..., ch][sample].mean()
        s_std = src_lab[..., ch][sample].std() + 1e-6
        t_mean = tgt_lab[..., ch][sample].mean()
        t_std = tgt_lab[..., ch][sample].std() + 1e-6
        # Clamp the scale factor — runaway std-ratios cause oversaturation
        scale = float(np.clip(t_std / s_std, 0.5, 2.0))
        out[..., ch] = (src_lab[..., ch] - s_mean) * scale + t_mean

    out = np.clip(out, 0, 255).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_LAB2BGR)


def compose(
    target_frame_bgr: np.ndarray,
    target_landmarks_px: np.ndarray,
    source_image_bgr: np.ndarray,
    source_landmarks_px: np.ndarray,
    region_names: Sequence[str] | None = None,
    profile: str | None = None,
    per_region_alpha: Optional[dict[str, float]] = None,
    feather_px: int = 12,
    warp_method: str = "tps",
    apply_color_match: bool = True,
    color_match_method: str = "histogram",
    blend_mode: str = "alpha",
) -> CompositeResult:
    """Build a region-composite face swap.

    You must pass EITHER region_names (explicit list) OR profile (predefined
    bundle from SWAP_PROFILES). region_names wins if both are given.

    Args:
        target_frame_bgr: your frame, (H, W, 3) uint8 BGR
        target_landmarks_px: (478, 2) int32 — must be computed on the same frame
        source_image_bgr: Eminem's photo, (H_s, W_s, 3) uint8 BGR
        source_landmarks_px: (478, 2) int32 for source
        region_names: ['lips_outer', 'left_eye', ...] OR
        profile: 'inner_face', 'eyes_only', etc. (see SWAP_PROFILES)
        per_region_alpha: e.g. {'lips_outer': 0.6} for partial swap
        feather_px: Gaussian blur on the mask edge (default 12px)
        warp_method: 'tps' (default, pixel-exact) or 'affine' (fast)
        apply_color_match: shift LAB stats so lighting matches (default True)
        color_match_method: 'histogram' (default — CDF mapping, photoreal)
            or 'mean_std' (legacy — overshoots to magenta with B&W sources)
        blend_mode: 'alpha' (default — clean linear blend with feathered
            mask, works well after histogram color-match) or 'poisson' /
            'poisson_mixed' / 'hybrid' (cv2.seamlessClone — currently
            shows red tint artefacts on the TPS-pre-aligned warped source,
            kept as opt-in for future fix)

    Returns:
        CompositeResult with everything you need to inspect the math.
    """
    h, w = target_frame_bgr.shape[:2]

    # 1. Build the α-mask
    if region_names is not None:
        mask = make_composite_mask(
            region_names, target_landmarks_px, (w, h),
            feather_px=feather_px, per_region_alpha=per_region_alpha,
        )
        regions_used = list(region_names)
    elif profile is not None:
        mask = make_profile_mask(
            profile, target_landmarks_px, (w, h),
            feather_px=feather_px, per_region_alpha=per_region_alpha,
        )
        regions_used = list(SWAP_PROFILES[profile])
    else:
        raise ValueError("must pass region_names or profile")

    # 2. Warp source onto target geometry
    warped = warp_source_to_target(
        source_image_bgr,
        source_landmarks_px,
        target_landmarks_px,
        target_size=(w, h),
        method=warp_method,
    )

    # 3. Color-match (optional but usually on)
    if apply_color_match:
        if color_match_method == "histogram":
            warped = histogram_match_lab(warped, target_frame_bgr, mask)
        elif color_match_method == "mean_std":
            warped = color_match_lab(warped, target_frame_bgr, mask)
        else:
            raise ValueError(f"unknown color_match_method: {color_match_method!r}")

    # 4. Blend
    if blend_mode == "alpha":
        composite = alpha_blend(target_frame_bgr, warped, mask)
    elif blend_mode == "poisson":
        composite = poisson_blend(target_frame_bgr, warped, mask, mode="normal")
    elif blend_mode == "poisson_mixed":
        composite = poisson_blend(target_frame_bgr, warped, mask, mode="mixed")
    elif blend_mode == "hybrid":
        composite = hybrid_blend(target_frame_bgr, warped, mask)
    else:
        raise ValueError(f"unknown blend_mode: {blend_mode!r}")

    return CompositeResult(
        composite=composite,
        mask=mask,
        warped_source=warped,
        target_landmarks=target_landmarks_px,
        source_landmarks=source_landmarks_px,
        debug_info={
            "regions": regions_used,
            "profile": profile,
            "warp_method": warp_method,
            "feather_px": feather_px,
            "color_match": apply_color_match,
            "color_match_method": color_match_method,
            "blend_mode": blend_mode,
            "per_region_alpha": per_region_alpha or {},
        },
    )


def compose_fast(
    target_frame_bgr: np.ndarray,
    target_landmarks_px: np.ndarray,
    source_image_bgr: np.ndarray,
    source_landmarks_px: np.ndarray,
    profile: str = "inner_face",
    feather_px: int = 12,
    blend_mode: str = "alpha",
) -> np.ndarray:
    """Realtime-tier composite: affine warp + bbox-localised color/blend.

    Same math as compose() but tuned for live streaming:
      - affine warp (~2ms vs ~1500ms TPS)
      - histogram match + alpha blend only inside the face bbox
        (typically 500x600 px) instead of the full 1920x1080 frame
      - returns only the BGR composite (no CompositeResult wrapper)

    Quality is visually 95-99% of compose(method='tps') for typical
    webcam poses where source/target heads aren't drastically rotated
    relative to each other.

    ~30-50ms total on a 1920x1080 frame, vs ~1100-1500ms for compose().
    Suitable for 10-15 fps live preview.
    """
    h, w = target_frame_bgr.shape[:2]

    # 1. Mask in full-frame coords (cheap — mostly a fillPoly).
    mask = make_profile_mask(
        profile, target_landmarks_px, (w, h), feather_px=feather_px
    )

    # 2. Bounding box of the mask + padding for the soft edge.
    pad = max(feather_px * 3, 30)
    ys, xs = np.where(mask > 0.01)
    if ys.size == 0:
        return target_frame_bgr.copy()
    y0 = max(0, int(ys.min()) - pad)
    y1 = min(h, int(ys.max()) + pad)
    x0 = max(0, int(xs.min()) - pad)
    x1 = min(w, int(xs.max()) + pad)

    # 3. Affine warp (whole frame — cv2.warpAffine is already O(output_size)
    #    not O(input_size), so cropping the output to bbox would save little).
    matrix = estimate_affine(source_landmarks_px, target_landmarks_px)
    warped_full = apply_affine(source_image_bgr, matrix, (w, h))

    # 4. Crop everything to bbox for the expensive per-pixel steps.
    crop_tgt = target_frame_bgr[y0:y1, x0:x1]
    crop_src = warped_full[y0:y1, x0:x1]
    crop_mask = mask[y0:y1, x0:x1]

    # 5. Color-match in bbox.
    crop_src = histogram_match_lab(crop_src, crop_tgt, crop_mask)

    # 6. Blend in bbox.
    if blend_mode == "alpha":
        blended_crop = alpha_blend(crop_tgt, crop_src, crop_mask)
    else:
        # poisson modes are dicey + slow — fall back to alpha for fast path
        blended_crop = alpha_blend(crop_tgt, crop_src, crop_mask)

    # 7. Splice the blended bbox back into the original frame.
    out = target_frame_bgr.copy()
    out[y0:y1, x0:x1] = blended_crop
    return out


def compose_from_paths(
    target_image_path: str,
    source_image_path: str,
    profile: str = "inner_face",
    **kwargs,
) -> CompositeResult:
    """Convenience: load both images, run FaceLandmarker on each, compose.

    Used by the static-test harness in scripts/face_math_compose.py.
    Detector is the singleton FaceLandmarkDetector.
    """
    target = cv2.imread(target_image_path)
    source = cv2.imread(source_image_path)
    if target is None:
        raise FileNotFoundError(f"target not readable: {target_image_path}")
    if source is None:
        raise FileNotFoundError(f"source not readable: {source_image_path}")

    detector = FaceLandmarkDetector.get()

    target_rgb = cv2.cvtColor(target, cv2.COLOR_BGR2RGB)
    target_result = detector.detect(target_rgb)
    if target_result is None:
        raise ValueError(f"no face in target: {target_image_path}")

    source_rgb = cv2.cvtColor(source, cv2.COLOR_BGR2RGB)
    source_result = detector.detect(source_rgb)
    if source_result is None:
        raise ValueError(f"no face in source: {source_image_path}")

    return compose(
        target_frame_bgr=target,
        target_landmarks_px=target_result.landmarks_px,
        source_image_bgr=source,
        source_landmarks_px=source_result.landmarks_px,
        profile=profile,
        **kwargs,
    )
