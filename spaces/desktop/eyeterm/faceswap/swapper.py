"""FaceSwapper — insightface buffalo_l detector + inswapper_128 ONNX swap."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _register_cuda_dll_dirs() -> None:
    """Make CUDA DLLs from pip nvidia-* packages findable on Windows.

    onnxruntime-gpu (built against CUDA 12) needs cublas64_12, cublasLt64_12,
    cudart64_12, cudnn64_9 etc. When installed via pip (nvidia-cublas-cu12,
    nvidia-cudnn-cu12, etc.), those DLLs sit in site-packages/nvidia/*/bin.
    We both `os.add_dll_directory` (for DLLs that ORT loads directly) AND
    prepend to PATH (for transitive loads — cublas → cublasLt, etc. — which
    follow the process PATH instead of the Python dll-dir list).
    """
    if sys.platform != "win32":
        return
    try:
        import nvidia  # type: ignore
    except ImportError:
        return
    # nvidia is a namespace package: __file__ may be None but __path__ is set
    nvidia_file = getattr(nvidia, "__file__", None)
    if nvidia_file:
        nvidia_root = Path(nvidia_file).parent
    else:
        paths = getattr(nvidia, "__path__", None)
        if not paths:
            return
        nvidia_root = Path(list(paths)[0])
    dirs = [str(d) for d in nvidia_root.glob("*/bin") if d.is_dir()]
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except (OSError, FileNotFoundError):
            pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")


_register_cuda_dll_dirs()


class FaceSwapper:
    """Live face-swap using insightface + inswapper_128.

    Target face is extracted once at init. Each `swap()` call:
    1. detects largest face in input frame
    2. swaps target identity onto it
    3. returns (swapped_frame, detected_face) — the Face object carries
       kps (5-key) and landmark_2d_106 which the aligner uses as IST points.

    If no face is detected, returns (original_frame, None) — caller can
    skip alignment and push the raw frame through.
    """

    def __init__(self, target_face_path: Path, providers: Optional[List[str]] = None):
        self._target_path = Path(target_face_path)
        self._providers = providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]

        # Lazy import — only when swapper is actually instantiated
        import insightface
        from insightface.app import FaceAnalysis

        self._app = FaceAnalysis(name="buffalo_l", providers=self._providers)
        self._app.prepare(ctx_id=0, det_size=(640, 640))

        model_path = self._resolve_inswapper_path()
        self._swapper = insightface.model_zoo.get_model(
            str(model_path), providers=self._providers
        )

        self._target_face = self._load_target(self._target_path)
        logger.info(
            "FaceSwapper ready — target=%s providers=%s",
            self._target_path.name,
            self._providers,
        )

    @staticmethod
    def _resolve_inswapper_path() -> Path:
        candidates = [
            Path.home() / ".insightface" / "models" / "inswapper_128.onnx",
            Path.home() / ".insightface" / "models" / "inswapper_128_fp16.onnx",
        ]
        for c in candidates:
            if c.exists():
                return c
        raise FileNotFoundError(
            "inswapper_128.onnx not found. Download it into "
            f"{candidates[0].parent} (non-commercial license — user responsibility)."
        )

    def _load_target(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(f"Target face image not found: {path}")
        img = cv2.imread(str(path))
        if img is None:
            raise ValueError(f"Could not read target image: {path}")
        faces = self._app.get(img)
        if not faces:
            raise ValueError(f"No face detected in target image: {path}")
        # Use largest face as target
        return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

    def set_target(self, path: Path) -> None:
        """Hot-swap the target face."""
        self._target_face = self._load_target(Path(path))
        self._target_path = Path(path)
        logger.info("FaceSwapper target changed to %s", path)

    def swap(self, frame_bgr: np.ndarray) -> Tuple[np.ndarray, Optional[object]]:
        """Swap largest detected face. Returns (frame, insight_face_or_None)."""
        faces = self._app.get(frame_bgr)
        if not faces:
            return frame_bgr, None
        src = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        out = self._swapper.get(frame_bgr, src, self._target_face, paste_back=True)
        return out, src
