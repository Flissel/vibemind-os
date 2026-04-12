"""VideoRecorder — writes composited UI frames as MP4.

Stores to ~/.rowboat/Videos/ so the Rowboat space and Video space (both
backed by media_server.py on port 9877) pick up the recordings automatically.

Thread-model: start()/stop() called from the main loop (not HTTP thread),
because cv2.VideoWriter is not thread-safe. HTTP commands arrive via the
camera_server command queue and are processed in the main loop; see
EyeTermApp._process_commands().
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("eyeterm.recorder")

# Shared storage with Rowboat + Video space (media_server MEDIA_ROOT)
DEFAULT_OUTPUT_DIR = Path.home() / ".rowboat" / "Videos"


class VideoRecorder:
    """Writes BGR frames to a timestamped MP4 using cv2.VideoWriter (mp4v)."""

    def __init__(self, output_dir: Path = DEFAULT_OUTPUT_DIR, fps: int = 30):
        self._output_dir = Path(output_dir)
        self._fps = int(fps)
        self._writer: Optional[cv2.VideoWriter] = None
        self._filename: Optional[Path] = None
        self._started_at: Optional[float] = None
        self._frame_count: int = 0
        self._size: Optional[tuple] = None

    @property
    def active(self) -> bool:
        return self._writer is not None

    @property
    def state(self) -> dict:
        if not self.active:
            return {"active": False}
        return {
            "active": True,
            "filename": self._filename.name if self._filename else None,
            "path": str(self._filename) if self._filename else None,
            "started_at": self._started_at,
            "duration_s": round(time.time() - (self._started_at or 0), 1),
            "frame_count": self._frame_count,
            "fps": self._fps,
        }

    def start(self, sample_frame: np.ndarray, name_hint: Optional[str] = None) -> Path:
        """Begin a new recording. Returns the file path."""
        if self.active:
            raise RuntimeError("Recording already in progress")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"eyeterm_{ts}"
        if name_hint:
            # Sanitize: alnum/underscore only
            safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in name_hint)[:40]
            stem = f"{stem}_{safe}"
        self._filename = self._output_dir / f"{stem}.mp4"

        h, w = sample_frame.shape[:2]
        self._size = (w, h)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(
            str(self._filename), fourcc, float(self._fps), (w, h)
        )
        if not self._writer.isOpened():
            self._writer = None
            self._filename = None
            raise RuntimeError(
                "cv2.VideoWriter failed to open — check that the mp4v codec "
                "is available (opencv-python ships it)."
            )
        self._started_at = time.time()
        self._frame_count = 0
        logger.info("Recording started: %s (%dx%d @ %d fps)", self._filename, w, h, self._fps)
        return self._filename

    def write(self, frame: np.ndarray) -> None:
        """Write a single frame. Silently drops if not active or wrong size."""
        if self._writer is None or self._size is None:
            return
        h, w = frame.shape[:2]
        if (w, h) != self._size:
            # Resize to recorder dimensions — keeps MP4 consistent if the
            # pipeline temporarily outputs a different shape
            frame = cv2.resize(frame, self._size)
        self._writer.write(frame)
        self._frame_count += 1

    def stop(self) -> dict:
        """Finalize the recording and return summary dict."""
        if not self.active:
            return {"active": False}
        summary = self.state
        try:
            self._writer.release()
        except Exception as e:
            logger.warning("VideoWriter.release error: %s", e)
        logger.info(
            "Recording stopped: %s (%d frames, %.1fs)",
            self._filename,
            self._frame_count,
            summary.get("duration_s", 0),
        )
        self._writer = None
        self._filename = None
        self._started_at = None
        self._frame_count = 0
        self._size = None
        summary["active"] = False
        summary["stopped"] = True
        return summary
