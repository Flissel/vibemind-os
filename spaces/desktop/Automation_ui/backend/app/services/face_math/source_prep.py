"""Source-image normalisation.

Stylised / AI-generated source portraits (e.g. marshall_v5) carry a
global colour cast — a sepia or warm tint — plus an exaggerated
contrast curve. When such a source is region-composited onto a normal
webcam frame, the colour-match maths cannot fully undo the cast and
the swapped region ends up pink/yellow.

A real photograph (marshallv2, face101) composites cleanly because it
has a roughly neutral colour balance to begin with.

This module brings a stylised source closer to "neutral photograph"
BEFORE it enters the warp/composite pipeline:

  - Gray-world white balance: assumes the average of a face image
    should be roughly neutral grey; rescales the channels to remove a
    global tint.
  - Mild contrast damping in LAB-L: pulls an over-cranked tone curve
    back toward the mid-range so deep shadows / blown highlights don't
    survive the swap as hard edges.

The normalisation is conservative — a source that is already neutral
(a real photo) passes through almost unchanged.
"""

from __future__ import annotations

import cv2
import numpy as np


def _gray_world_white_balance(bgr: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Remove a global colour cast via the gray-world assumption.

    Each channel is scaled so all three end up with the same mean.
    `strength` in [0,1] blends between the original (0) and the fully
    balanced image (1) — keeps the correction from over-shooting on a
    source that legitimately has a non-neutral subject.
    """
    f = bgr.astype(np.float32)
    means = f.reshape(-1, 3).mean(axis=0)  # B, G, R
    gray = float(means.mean())
    if gray < 1e-3:
        return bgr
    # per-channel gain that would equalise the means
    gains = gray / np.clip(means, 1e-3, None)
    # clamp so a single weird channel can't blow up
    gains = np.clip(gains, 0.6, 1.6)
    # blend toward neutral by `strength`
    gains = 1.0 + (gains - 1.0) * float(np.clip(strength, 0.0, 1.0))
    out = f * gains[None, None, :]
    return np.clip(out, 0, 255).astype(np.uint8)


def _damp_contrast(bgr: np.ndarray, strength: float = 0.4) -> np.ndarray:
    """Pull an over-cranked tone curve back toward the mid-range.

    Works on the LAB L channel: rescales its spread around the mean by
    (1 - strength*k). strength=0 leaves it alone, strength=1 would
    flatten it heavily. Default 0.4 is a gentle damp.
    """
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0]
    mean = float(L.mean())
    # how much wider than a "normal" spread is this image?
    std = float(L.std())
    # normal-ish portrait L-std is ~45; only damp if clearly above
    excess = max(0.0, (std - 45.0) / 45.0)  # 0 if already normal
    factor = 1.0 - float(np.clip(strength, 0.0, 1.0)) * min(excess, 1.0)
    lab[..., 0] = np.clip((L - mean) * factor + mean, 0, 255)
    return cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)


def normalise_source(
    source_bgr: np.ndarray,
    white_balance: float = 0.8,
    contrast_damp: float = 0.4,
) -> np.ndarray:
    """Bring a source portrait toward a neutral-photograph baseline.

    Conservative by design: a real photo (already neutral, normal
    contrast) comes back nearly unchanged; a stylised AI render gets
    its cast and over-contrast pulled back so the composite stays
    photoreal.

    Args:
        source_bgr: the source face image, uint8 BGR
        white_balance: gray-world correction strength [0,1]
        contrast_damp: L-channel contrast damping strength [0,1]
    """
    out = _gray_world_white_balance(source_bgr, strength=white_balance)
    out = _damp_contrast(out, strength=contrast_damp)
    return out


def cast_metrics(bgr: np.ndarray) -> dict:
    """Quantify how 'stylised' an image is — used by tests / logging.

    Returns the channel-mean spread (colour cast) and the LAB-L std
    (contrast). A neutral photo has low cast and L-std near 45.
    """
    means = bgr.reshape(-1, 3).mean(axis=0)
    cast = float(means.max() - means.min())
    L = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[..., 0].astype(np.float32)
    return {
        "channel_cast": round(cast, 1),
        "L_mean": round(float(L.mean()), 1),
        "L_std": round(float(L.std()), 1),
    }
