"""MediaPipe FaceMesh region polygon definitions.

These are the canonical landmark-index subsets defining each anatomical
face region. They come from the FaceMesh topology — the same indices
mediapipe's old solutions API exposed as FACEMESH_LIPS, FACEMESH_LEFT_EYE,
etc., re-organised here as ordered polygons (not edge-pairs) so we can
feed them straight to cv2.fillPoly / cv2.polylines.

Single source of truth: this file. Both the reference-recorder (preview
overlay) and the region-compositor (α-masks) import from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class Region:
    """One named face region."""

    name: str
    indices: tuple[int, ...]
    color: tuple[int, int, int]  # BGR — used for preview overlays
    is_closed: bool = True  # most regions are closed polygons; nose bridge is a line


# ---------------------------------------------------------------------------
# Region definitions
# ---------------------------------------------------------------------------
# Coordinate convention: "left" and "right" are from the SUBJECT's POV.
# The subject's left eye is therefore on the viewer's right side.

LIPS_OUTER = Region(
    name="lips_outer",
    indices=(
        61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291,
        409, 270, 269, 267, 0, 37, 39, 40, 185,
    ),
    color=(50, 50, 255),  # red
)

LIPS_INNER = Region(
    name="lips_inner",
    indices=(
        78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308,
        415, 310, 311, 312, 13, 82, 81, 80, 191,
    ),
    color=(50, 150, 255),  # orange
)

LEFT_EYE = Region(
    name="left_eye",
    indices=(
        # eye outline — subject's LEFT eye (viewer's right)
        263, 249, 390, 373, 374, 380, 381, 382, 362,
        466, 388, 387, 386, 385, 384, 398,
    ),
    color=(255, 200, 50),  # cyan
)

RIGHT_EYE = Region(
    name="right_eye",
    indices=(
        33, 7, 163, 144, 145, 153, 154, 155, 133,
        246, 161, 160, 159, 158, 157, 173,
    ),
    color=(255, 200, 50),
)

LEFT_BROW = Region(
    name="left_brow",
    indices=(276, 283, 282, 295, 285, 336, 296, 334, 293, 300),
    color=(50, 255, 50),  # green
    is_closed=False,
)

RIGHT_BROW = Region(
    name="right_brow",
    indices=(46, 53, 52, 65, 55, 107, 66, 105, 63, 70),
    color=(50, 255, 50),
    is_closed=False,
)

NOSE_BRIDGE = Region(
    name="nose_bridge",
    indices=(168, 6, 197, 195, 5, 4, 1),
    color=(200, 100, 255),  # pink
    is_closed=False,
)

FACE_OVAL = Region(
    name="face_oval",
    indices=(
        10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361,
        288, 397, 365, 379, 378, 400, 377, 152, 148, 176, 149,
        150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103,
        67, 109,
    ),
    color=(255, 255, 255),  # white
)

# Chin — verified-good polygon along the jawline below the lower lip.
CHIN = Region(
    name="chin",
    indices=(
        # midline below lip, then jawline curve to one ear
        18, 200, 199, 175, 152, 148, 176, 149, 150, 136, 172, 58,
    ),
    color=(150, 100, 200),
)

# NOTE: forehead / cheeks / eye_socket are NOT defined as standalone
# regions. Earlier attempts produced invalid polygons (self-intersecting
# or off-target) because their indices in the FaceMesh canonical model
# are not contiguous closed contours.
#
# To swap "everything except hair", use the `full_face_minus_hair`
# profile (= face_oval region — masks the entire inner face including
# forehead, cheeks, eye sockets). To swap only eyes including brow area,
# combine `left_eye + right_eye + left_brow + right_brow` via region_names.
# These are the proven-correct building blocks.


# ---------------------------------------------------------------------------
# Public registry
# ---------------------------------------------------------------------------

ALL_REGIONS: tuple[Region, ...] = (
    FACE_OVAL,
    CHIN,
    LEFT_EYE,
    RIGHT_EYE,
    LEFT_BROW,
    RIGHT_BROW,
    NOSE_BRIDGE,
    LIPS_OUTER,
    LIPS_INNER,
)

REGION_BY_NAME: dict[str, Region] = {r.name: r for r in ALL_REGIONS}


# Composite "swap profiles" — predefined combinations of base regions
# that answer common questions like "swap only the inner face" or
# "swap everything except the hair." Built only from verified regions.
SWAP_PROFILES: dict[str, tuple[str, ...]] = {
    "inner_face": (
        # mouth + eyes + brows + nose — keeps your forehead/cheeks/jaw
        "lips_outer", "lips_inner",
        "left_eye", "right_eye",
        "left_brow", "right_brow",
        "nose_bridge",
    ),
    "eyes_only": ("left_eye", "right_eye"),
    "eyes_and_brows": (
        "left_eye", "right_eye", "left_brow", "right_brow",
    ),
    "mouth_only": ("lips_outer", "lips_inner"),
    "mouth_and_chin": (
        "lips_outer", "lips_inner", "chin",
    ),
    "full_face_minus_hair": (
        # face_oval covers the entire inner face down to the jawline
        "face_oval",
    ),
    "identity_anchor": (
        # eyes + nose + brows — the parts ArcFace weights most
        "left_eye", "right_eye", "left_brow", "right_brow",
        "nose_bridge",
    ),
}


def get_polygon_pixels(
    region: Region, landmarks_px: np.ndarray
) -> np.ndarray:
    """Get the (N, 2) pixel polygon for one region given the 478-landmark array.

    landmarks_px must be shape (478, 2) int32.
    """
    return landmarks_px[list(region.indices)]


def get_polygon_normalised(
    region: Region, landmarks_norm: np.ndarray
) -> np.ndarray:
    """Same but in normalised (0..1) coords. Useful for resolution-independent math."""
    return landmarks_norm[list(region.indices), :2]
