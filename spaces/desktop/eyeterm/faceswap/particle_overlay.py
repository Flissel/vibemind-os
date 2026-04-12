"""ParticleOverlay — cv2 port of humanoid-particles.html ParticleCloud renderer.

Renders SOLL (green) and IST (red) as small particle clusters with dashed
connection lines whose color interpolates from green (aligned) to red
(misaligned). Debug-only — not visible in virtual-cam output.
"""

from __future__ import annotations

import math
import random
from typing import Optional

import cv2
import numpy as np

from .aligner import ParticleState


class ParticleOverlay:
    """Draws SOLL/IST error particles onto a BGR frame."""

    def __init__(self, cluster_size: int = 8, error_scale_px: float = 12.0):
        self._cluster_size = int(cluster_size)
        self._error_scale = float(error_scale_px)
        self._phase = 0.0  # animates wobble over time

    def draw(
        self,
        frame: np.ndarray,
        state: Optional[ParticleState],
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> None:
        if state is None or not state.bodies:
            return
        self._phase += 0.15

        for i, (soll, ist, err) in enumerate(
            zip(state.targets, state.bodies, state.errors)
        ):
            sx, sy = int(soll["x"] * scale_x), int(soll["y"] * scale_y)
            ix, iy = int(ist["x"] * scale_x), int(ist["y"] * scale_y)

            t = min(1.0, float(err) / self._error_scale)
            # BGR: green (aligned) → red (misaligned)
            color = (0, int(255 * (1 - t)), int(255 * t))

            self._draw_cluster(frame, sx, sy, (0, 255, 0), radius=3)
            self._draw_cluster(frame, ix, iy, (0, 0, 255), radius=3)
            self._dashed_line(frame, (ix, iy), (sx, sy), color)

    def _draw_cluster(self, frame, cx, cy, color, radius=3):
        for i in range(self._cluster_size):
            angle = (i / self._cluster_size) * 2 * math.pi + self._phase
            r = radius + (i % 3)
            px = int(cx + math.cos(angle) * r)
            py = int(cy + math.sin(angle) * r)
            if 0 <= px < frame.shape[1] and 0 <= py < frame.shape[0]:
                cv2.circle(frame, (px, py), 1, color, -1)
        cv2.circle(frame, (cx, cy), 2, color, 1)

    def _dashed_line(self, frame, p1, p2, color, dash=6, gap=4):
        x1, y1 = p1
        x2, y2 = p2
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy)
        if dist < 1:
            return
        n = int(dist / (dash + gap))
        for i in range(n + 1):
            t0 = i * (dash + gap) / dist
            t1 = min(1.0, (i * (dash + gap) + dash) / dist)
            sx = int(x1 + dx * t0)
            sy = int(y1 + dy * t0)
            ex = int(x1 + dx * t1)
            ey = int(y1 + dy * t1)
            cv2.line(frame, (sx, sy), (ex, ey), color, 1, cv2.LINE_AA)
