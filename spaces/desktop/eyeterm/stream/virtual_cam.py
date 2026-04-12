"""VirtualCamSink — push frames into a Windows virtual camera via pyvirtualcam.

Meeting apps (Zoom, Teams, Meet, browser getUserMedia) pick up the virtual
camera as a regular DirectShow/MF device.

Tries backends in order: unitycapture → obs → auto (pyvirtualcam default).
- Unity Capture: small standalone DirectShow driver (~2MB, signed) — install
  once via its Install.bat, no OBS Studio needed.
- OBS: works if OBS Studio is installed (ships the obs-virtualcam driver).
"""

from __future__ import annotations

import logging
from typing import List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_BACKEND_ORDER = ["unitycapture", "obs"]


class VirtualCamSink:
    def __init__(
        self,
        width: int,
        height: int,
        fps: int = 30,
        backends: Optional[List[str]] = None,
    ):
        self._width = int(width)
        self._height = int(height)
        self._fps = int(fps)
        self._cam = None
        self._backend_used: Optional[str] = None

        tried: List[str] = []
        try:
            import pyvirtualcam
        except ImportError as e:
            logger.warning("pyvirtualcam not installed (%s) — virtual cam disabled", e)
            return

        order = backends or DEFAULT_BACKEND_ORDER
        last_err: Optional[Exception] = None
        for backend in order:
            try:
                self._cam = pyvirtualcam.Camera(
                    width=self._width,
                    height=self._height,
                    fps=self._fps,
                    backend=backend,
                )
                self._backend_used = backend
                logger.info(
                    "VirtualCamSink open: %dx%d @ %dfps via %s (device=%s)",
                    self._width,
                    self._height,
                    self._fps,
                    backend,
                    self._cam.device,
                )
                break
            except Exception as e:
                tried.append(f"{backend}:{type(e).__name__}")
                last_err = e
                continue
        if self._cam is None:
            logger.warning(
                "VirtualCamSink init failed on all backends (%s). "
                "Install Unity Capture (github.com/schellingb/UnityCapture → Install.bat) "
                "or OBS Studio. Running as no-op. last_error=%s",
                ", ".join(tried),
                last_err,
            )

    @property
    def is_open(self) -> bool:
        return self._cam is not None

    def send(self, frame_bgr: np.ndarray) -> None:
        if self._cam is None:
            return
        if frame_bgr.shape[1] != self._width or frame_bgr.shape[0] != self._height:
            frame_bgr = cv2.resize(frame_bgr, (self._width, self._height))
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        try:
            self._cam.send(rgb)
            self._cam.sleep_until_next_frame()
        except Exception as e:
            logger.debug("VirtualCam send error: %s", e)

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.close()
            except Exception:
                pass
            self._cam = None
