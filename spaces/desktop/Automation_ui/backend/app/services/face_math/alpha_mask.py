"""α-mask generation from FaceMesh landmarks.

A mask is a single-channel float32 array in [0, 1] same size as the
frame. 1 = "fully take target pixel here", 0 = "keep source pixel".
The feathering Gaussian-blurs the polygon edge so the composite has no
visible seam.

This is the geometric heart of Phase B: every swap profile boils down to
"draw N polygons into a mask, feather, blend." No ML at all.
"""

from __future__ import annotations

from typing import Iterable, Optional

import cv2
import numpy as np

from .regions import (
    ALL_REGIONS,
    REGION_BY_NAME,
    Region,
    SWAP_PROFILES,
    get_polygon_pixels,
)


def make_region_mask(
    region: Region,
    landmarks_px: np.ndarray,
    image_size: tuple[int, int],
    feather_px: int = 12,
) -> np.ndarray:
    """Build a single-region α-mask.

    Args:
        region: which polygon to draw
        landmarks_px: (478, 2) int32 pixel landmarks for this frame
        image_size: (W, H) of the target frame
        feather_px: Gaussian blur radius in px for the soft edge.
            Set 0 to disable feathering (hard mask, for debugging).

    Returns:
        (H, W) float32 in [0, 1].
    """
    w, h = image_size
    mask = np.zeros((h, w), dtype=np.uint8)
    poly = get_polygon_pixels(region, landmarks_px)
    if region.is_closed:
        cv2.fillPoly(mask, [poly.astype(np.int32)], 255)
    else:
        # open polylines (brows, nose_bridge) — draw thick stroke,
        # then dilate
        cv2.polylines(
            mask, [poly.astype(np.int32)], isClosed=False, color=255, thickness=8
        )
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))

    if feather_px > 0:
        # Convert to float, then gaussian, normalise
        mask_f = mask.astype(np.float32) / 255.0
        ksize = feather_px * 2 + 1
        mask_f = cv2.GaussianBlur(mask_f, (ksize, ksize), feather_px / 3.0)
        return np.clip(mask_f, 0.0, 1.0)
    return mask.astype(np.float32) / 255.0


def make_composite_mask(
    region_names: Iterable[str],
    landmarks_px: np.ndarray,
    image_size: tuple[int, int],
    feather_px: int = 12,
    per_region_alpha: Optional[dict[str, float]] = None,
) -> np.ndarray:
    """Combine multiple regions into one α-mask.

    Args:
        region_names: which regions to union (from REGION_BY_NAME)
        landmarks_px: (478, 2) int32
        image_size: (W, H)
        feather_px: Gaussian blur radius for the *final* combined mask
        per_region_alpha: optional dict {region_name: scalar α in [0,1]}.
            Lets you say "give me 30% lips + 100% eyes". Defaults to 1.0
            for every named region. Useful for the live-tuning UI later.

    Returns:
        (H, W) float32 in [0, 1].
    """
    w, h = image_size
    out = np.zeros((h, w), dtype=np.float32)
    per_region_alpha = per_region_alpha or {}

    for name in region_names:
        region = REGION_BY_NAME.get(name)
        if region is None:
            raise KeyError(f"unknown region: {name!r}")
        # build per-region hard mask first (no internal feather — final
        # feather happens once on the union, gives smoother seams)
        sub = make_region_mask(
            region, landmarks_px, image_size, feather_px=0
        )
        alpha = float(per_region_alpha.get(name, 1.0))
        sub *= alpha
        # union via max (not sum — overlapping regions shouldn't exceed 1)
        np.maximum(out, sub, out=out)

    if feather_px > 0:
        ksize = feather_px * 2 + 1
        out = cv2.GaussianBlur(out, (ksize, ksize), feather_px / 3.0)
    return np.clip(out, 0.0, 1.0)


def make_profile_mask(
    profile_name: str,
    landmarks_px: np.ndarray,
    image_size: tuple[int, int],
    feather_px: int = 12,
    per_region_alpha: Optional[dict[str, float]] = None,
) -> np.ndarray:
    """Build mask from a named SWAP_PROFILES entry."""
    if profile_name not in SWAP_PROFILES:
        raise KeyError(
            f"unknown profile: {profile_name!r} (known: {list(SWAP_PROFILES)})"
        )
    return make_composite_mask(
        SWAP_PROFILES[profile_name],
        landmarks_px,
        image_size,
        feather_px=feather_px,
        per_region_alpha=per_region_alpha,
    )


def make_all_region_masks(
    landmarks_px: np.ndarray,
    image_size: tuple[int, int],
    feather_px: int = 12,
) -> dict[str, np.ndarray]:
    """Convenience: build one mask per region. Useful for debug visualisations."""
    return {
        r.name: make_region_mask(r, landmarks_px, image_size, feather_px=feather_px)
        for r in ALL_REGIONS
    }


def visualise_mask(
    image_bgr: np.ndarray,
    mask: np.ndarray,
    color: tuple[int, int, int] = (0, 255, 0),
    alpha_strength: float = 0.5,
) -> np.ndarray:
    """Tint the masked area for visual debugging.

    Returns a copy of image_bgr with the mask drawn as a coloured overlay.
    """
    overlay = image_bgr.copy()
    color_layer = np.zeros_like(image_bgr)
    color_layer[:] = color
    a = (mask * alpha_strength)[..., None]
    overlay = (image_bgr.astype(np.float32) * (1 - a) +
               color_layer.astype(np.float32) * a)
    return overlay.astype(np.uint8)
