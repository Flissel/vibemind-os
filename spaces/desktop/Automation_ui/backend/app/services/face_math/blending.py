"""Phase C — high-quality blending.

Two improvements over the naive alpha-blend in compositor.compose:

1. POISSON seamless cloning (cv2.seamlessClone) — solves
       ∇²ƒ = ∇·v  inside the masked region,
       ƒ = target  on the boundary.
   Result: the composite has the *gradients* of the source (so it
   "looks like" Marshall) but the *boundary pixels* match your
   surrounding skin/hair perfectly. No visible seam.

2. HISTOGRAM MATCH color correction — instead of shifting LAB mean+std
   (which can overshoot to magenta when the source is B&W), we map
   each channel's CDF onto the target's CDF inside the mask. More
   conservative, preserves source texture details.

Both are drop-in replacements for the linear alpha blend; the
compositor picks via a flag.
"""

from __future__ import annotations

import cv2
import numpy as np


def _valid_source_pixels(source_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Boolean array: pixels that are BOTH inside the mask AND have real
    source content (not BORDER_CONSTANT zero).

    The TPS-pre-aligned warped source has black BORDER_CONSTANT regions
    where the source image didn't cover the destination canvas. If we
    feed those into histogram-match or Poisson, they bias the statistics
    toward black → pink/red tint in the composite. Excluding them is
    the single fix that makes both face_oval (large mask) and Poisson
    blending photoreal.

    "Zero" check is permissive (max channel ≤ 6) to also catch
    near-black noise from JPEG re-encoding around the border.
    """
    in_mask = mask > 0.1
    max_chan = source_bgr.max(axis=2)
    has_content = max_chan > 6
    return in_mask & has_content


def histogram_match_lab(
    source_bgr: np.ndarray,
    target_bgr: np.ndarray,
    mask: np.ndarray,
    luminance_only: bool = False,
) -> np.ndarray:
    """Match source's LAB channel CDFs to target's, inside the mask.

    Per-channel monotonic mapping: source value with CDF-percentile p
    becomes the target value at percentile p. Preserves rank-ordering
    (texture detail) — only re-maps the value range.

    By default operates on the L (luminance) channel only — re-lights
    Marshall so his skin matches your ambient lighting, but keeps his
    own a/b chroma. Matching all three channels independently used to
    pull Marshall's neutral-tone skin toward webcam-pink and produced
    the long-standing "magenta-blob" tint over face_oval profiles.

    Set luminance_only=False to restore the legacy three-channel behaviour
    when the source is genuinely B&W and you want it tinted toward the
    target's chroma.

    Samples ONLY pixels with real warped-source content — black
    BORDER_CONSTANT regions are excluded from the CDF.
    """
    if mask.max() <= 0:
        return source_bgr
    sample = _valid_source_pixels(source_bgr, mask)
    if sample.sum() < 50:
        return source_bgr

    src_lab = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2LAB)
    tgt_lab = cv2.cvtColor(target_bgr, cv2.COLOR_BGR2LAB)
    out = src_lab.copy()
    channels = [0] if luminance_only else [0, 1, 2]

    for ch in channels:
        src_vals = src_lab[..., ch][sample]
        tgt_vals = tgt_lab[..., ch][sample]
        if src_vals.size == 0 or tgt_vals.size == 0:
            continue
        # Build sorted-unique → percentile mapping
        src_sorted = np.sort(src_vals)
        tgt_sorted = np.sort(tgt_vals)
        # For each possible byte value 0..255, find what % of src pixels
        # fall below it, then look up that percentile in the target.
        src_cdf = np.searchsorted(src_sorted, np.arange(256))
        src_cdf_norm = src_cdf / max(src_sorted.size, 1)
        # Find target value at each percentile
        tgt_at_pct = tgt_sorted[
            np.clip(
                (src_cdf_norm * (tgt_sorted.size - 1)).astype(np.int64),
                0, tgt_sorted.size - 1,
            )
        ]
        lut = tgt_at_pct.astype(np.uint8)
        # Apply LUT to the entire channel (cheap)
        out[..., ch] = lut[src_lab[..., ch]]

    return cv2.cvtColor(out, cv2.COLOR_LAB2BGR)


def alpha_blend(
    target_bgr: np.ndarray,
    source_bgr: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    """Linear alpha blend — same as before, baseline for comparison."""
    m3 = mask[..., None]
    out = target_bgr.astype(np.float32) * (1 - m3) + source_bgr.astype(np.float32) * m3
    return out.clip(0, 255).astype(np.uint8)


def poisson_blend(
    target_bgr: np.ndarray,
    source_bgr: np.ndarray,
    mask: np.ndarray,
    mode: str = "normal",
) -> np.ndarray:
    """Poisson seamless cloning via cv2.seamlessClone.

    Args:
        target_bgr: background frame, (H, W, 3) uint8
        source_bgr: foreground (warped Marshall), same shape
        mask: (H, W) float32 in [0,1]; converted to uint8 with thresh 0.5
        mode: 'normal' = NORMAL_CLONE (preserves source colour),
              'mixed'  = MIXED_CLONE (lets target gradients dominate
              where stronger — better when source has sparse details
              like Marshall's stubble),
              'monochrome' = MONOCHROME_TRANSFER (illumination-aware)

    Returns:
        Blended BGR image.

    Caveat: cv2.seamlessClone expects a binary mask. We intersect the
    soft α-mask (>0.5) with the warped source's actual content area —
    BORDER_CONSTANT black pixels would otherwise feed zero-gradients
    into Poisson and produce a red/pink tint over the whole region.
    """
    if mask.max() <= 0:
        return target_bgr

    # Binary mask = inside α AND has real warped-source content
    valid = _valid_source_pixels(source_bgr, mask)
    binary = valid.astype(np.uint8) * 255

    ys, xs = np.where(binary > 0)
    if ys.size < 100:
        # Too little real content to integrate gradients meaningfully
        return alpha_blend(target_bgr, source_bgr, mask)
    center = (int(xs.mean()), int(ys.mean()))

    flag = {
        "normal": cv2.NORMAL_CLONE,
        "mixed": cv2.MIXED_CLONE,
        "monochrome": cv2.MONOCHROME_TRANSFER,
    }.get(mode, cv2.NORMAL_CLONE)

    try:
        return cv2.seamlessClone(source_bgr, target_bgr, binary, center, flag)
    except cv2.error:
        # seamlessClone occasionally fails on degenerate masks
        # (single pixel, mask touching image edge). Fall back to alpha.
        return alpha_blend(target_bgr, source_bgr, mask)


def hybrid_blend(
    target_bgr: np.ndarray,
    source_bgr: np.ndarray,
    mask: np.ndarray,
    poisson_mode: str = "normal",
    feather_at_edge: int = 8,
) -> np.ndarray:
    """Best of both: Poisson inside the mask, soft α at the edge.

    Poisson alone gives a seamless boundary but the centre can shift
    colour wildly if the surrounding target colour differs from the
    source mean. Hybrid: Poisson-blend the core, then α-blend a narrow
    feathered ring along the mask edge — gives smooth transitions
    without the colour wander.
    """
    if mask.max() <= 0:
        return target_bgr

    # Erode the mask-and-content intersection to find the "core"
    # Poisson area. Excluding BORDER_CONSTANT zero pixels — same
    # reason as poisson_blend.
    valid = _valid_source_pixels(source_bgr, mask)
    binary = valid.astype(np.uint8) * 255
    kernel = np.ones((feather_at_edge * 2 + 1,) * 2, np.uint8)
    core = cv2.erode(binary, kernel)
    if core.sum() < 100 * 255:
        # Mask too thin / source content too sparse for Poisson
        return alpha_blend(target_bgr, source_bgr, mask)

    ys, xs = np.where(core > 0)
    center = (int(xs.mean()), int(ys.mean()))
    flag = {
        "normal": cv2.NORMAL_CLONE,
        "mixed": cv2.MIXED_CLONE,
        "monochrome": cv2.MONOCHROME_TRANSFER,
    }.get(poisson_mode, cv2.NORMAL_CLONE)
    try:
        poisson = cv2.seamlessClone(
            source_bgr, target_bgr, core, center, flag
        )
    except cv2.error:
        return alpha_blend(target_bgr, source_bgr, mask)

    # Now α-blend the original soft mask: outside core, use original
    # mask values; inside core, mask is already 1.0 effectively. The
    # net effect: smooth feathered transition from target → poisson
    # in the ring zone.
    return alpha_blend(poisson, source_bgr, mask)
