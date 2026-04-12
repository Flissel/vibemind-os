"""Real-time deepfake detection from MediaPipe 478-landmarks + pixel analysis.

Maintains sliding windows of landmark and pixel data, computes multiple
authenticity signals, and emits alerts when the composite score drops
below threshold.

Signals (scored 0.0 = deepfake .. 1.0 = authentic):

  Tier 1 — every frame, landmark-only (<1 ms):
    1. Landmark jitter (velocity variance)
    2. Blink naturalness (interval regularity, rate, duration)
    3. Eye-head coordination (cross-correlation lag)
    4. Symmetry stability (left-right ratio variance)

  Tier 1b — every 30 frames (~0.5 ms):
    5. Temporal FFT (5-15 Hz artifact band energy)

  Tier 2 — every N frames, pixel-level (~6 ms):
    6. HF energy ratio (GAN blur / grid artifacts)
    7. Boundary artifacts (jawline blending seams)
    8. Color consistency (LAB face-vs-neck shift)
"""

import logging
import math
from collections import deque
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key landmark indices (subset of MediaPipe 478 for efficiency)
# ---------------------------------------------------------------------------
# Eyes
_LEFT_EYE_CORNERS = (33, 133)
_RIGHT_EYE_CORNERS = (362, 263)
_LEFT_IRIS = 468
_RIGHT_IRIS = 473
_LEFT_UPPER_LID = 159
_LEFT_LOWER_LID = 145
_RIGHT_UPPER_LID = 386
_RIGHT_LOWER_LID = 374

# Head structure
_NOSE_TIP = 1
_FOREHEAD = 10
_CHIN = 152
_LEFT_EAR = 234
_RIGHT_EAR = 454

# Jawline (every ~3rd landmark for efficiency)
_JAWLINE = [
    10, 338, 284, 389, 323, 288, 397, 377, 152,
    148, 150, 172, 132, 234, 162, 54, 109,
]

# Left-side and right-side landmark pairs for symmetry
_SYMMETRY_PAIRS = [
    (33, 362),    # outer eye corners
    (133, 263),   # inner eye corners
    (159, 386),   # upper lids
    (145, 374),   # lower lids
    (70, 300),    # outer eyebrows
    (107, 336),   # inner eyebrows
    (234, 454),   # ear tragions
    (61, 291),    # mouth corners
]

# All key landmarks for jitter tracking
_KEY_LANDMARKS = sorted(set(
    [_NOSE_TIP, _FOREHEAD, _CHIN, _LEFT_EAR, _RIGHT_EAR,
     _LEFT_IRIS, _RIGHT_IRIS,
     _LEFT_UPPER_LID, _LEFT_LOWER_LID,
     _RIGHT_UPPER_LID, _RIGHT_LOWER_LID]
    + [p for pair in _SYMMETRY_PAIRS for p in pair]
    + _JAWLINE
))

# Mouth landmarks for face region extraction
_MOUTH_OUTER = [
    61, 185, 40, 39, 37, 0, 267, 269, 270, 409,
    291, 375, 321, 405, 314, 17, 84, 181, 91, 146,
]

# EAR blink threshold
_EAR_BLINK_THRESHOLD = 0.21


def _sigmoid(x: float, k: float = 10.0, x0: float = 0.0) -> float:
    """Sigmoid squash: returns 0..1."""
    return 1.0 / (1.0 + math.exp(k * (x - x0)))


def _extract_key_landmarks(landmarks: Any) -> np.ndarray:
    """Extract (N, 3) array of key landmark positions."""
    pts = np.array(
        [(landmarks[i].x, landmarks[i].y, landmarks[i].z) for i in _KEY_LANDMARKS],
        dtype=np.float32,
    )
    return pts


def _face_bbox(landmarks: Any, frame_shape: Tuple[int, int, int],
               pad_frac: float = 0.1) -> Tuple[int, int, int, int]:
    """Bounding box (x1, y1, x2, y2) of face from landmarks, with padding."""
    h, w = frame_shape[:2]
    xs = [landmarks[i].x * w for i in _KEY_LANDMARKS]
    ys = [landmarks[i].y * h for i in _KEY_LANDMARKS]
    x1, x2 = int(min(xs)), int(max(xs))
    y1, y2 = int(min(ys)), int(max(ys))
    pw, ph = int((x2 - x1) * pad_frac), int((y2 - y1) * pad_frac)
    return (max(0, x1 - pw), max(0, y1 - ph),
            min(w, x2 + pw), min(h, y2 + ph))


def _jawline_mask(landmarks: Any, frame_shape: Tuple[int, int, int],
                  band_width: int = 5) -> np.ndarray:
    """Thin mask along jawline convex hull boundary."""
    h, w = frame_shape[:2]
    pts = np.array(
        [(int(landmarks[i].x * w), int(landmarks[i].y * h)) for i in _JAWLINE],
        dtype=np.int32,
    )
    hull = cv2.convexHull(pts)
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.drawContours(mask, [hull], -1, 255, band_width)
    return mask


class DeepfakeDetector:
    """Real-time deepfake detection using landmark + pixel analysis.

    Parameters
    ----------
    window_frames : int
        Sliding window size in frames (default 90 = 3s at 30fps).
    pixel_check_interval : int
        Run pixel-level checks every N frames (default 10).
    alert_threshold : float
        Composite authenticity below this triggers alert (default 0.4).
    alert_cooldown_ms : int
        Min ms between consecutive alerts (default 5000).
    """

    # Signal weights (must sum to 1.0)
    _WEIGHTS = {
        "jitter": 0.25,
        "blink": 0.15,
        "eye_head": 0.15,
        "symmetry": 0.10,
        "fft": 0.15,
        "hf_energy": 0.10,
        "boundary": 0.05,
        "color": 0.05,
    }

    def __init__(
        self,
        window_frames: int = 90,
        pixel_check_interval: int = 10,
        alert_threshold: float = 0.4,
        alert_cooldown_ms: int = 5000,
    ) -> None:
        self._window = window_frames
        self._pixel_interval = pixel_check_interval
        self._threshold = alert_threshold
        self._cooldown_ms = alert_cooldown_ms

        # Sliding windows
        self._lm_history: deque = deque(maxlen=window_frames)
        self._ear_history: deque = deque(maxlen=window_frames)
        self._head_history: deque = deque(maxlen=window_frames)
        self._iris_history: deque = deque(maxlen=window_frames)
        self._ts_history: deque = deque(maxlen=window_frames)

        # Blink tracking
        self._in_blink = False
        self._blink_start_ms = 0
        self._blink_intervals: deque = deque(maxlen=60)
        self._blink_durations: deque = deque(maxlen=60)
        self._last_blink_end_ms = 0

        # Frame counter + alert state
        self._frame_count = 0
        self._last_alert_ms = 0

        # Cached scores (persist between frames for amortized signals)
        self._scores: Dict[str, float] = {k: 1.0 for k in self._WEIGHTS}
        self._composite: float = 1.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self,
        frame: np.ndarray,
        landmarks: Any,
        ear_values: Tuple[float, float],
        head_pose: Tuple[float, float],
        iris_pos: Tuple[float, float],
        timestamp_ms: int,
    ) -> Optional[Dict]:
        """Process one frame. Returns alert dict or None."""
        # Append to sliding windows
        key_lm = _extract_key_landmarks(landmarks)
        self._lm_history.append(key_lm)
        self._ear_history.append(ear_values)
        self._head_history.append(head_pose)
        self._iris_history.append(iris_pos)
        self._ts_history.append(timestamp_ms)
        self._frame_count += 1

        n = len(self._lm_history)

        # Tier 1 — every frame (need at least 10 frames)
        if n >= 10:
            self._scores["jitter"] = self._score_landmark_jitter()
            self._scores["blink"] = self._score_blink_naturalness(
                ear_values, timestamp_ms
            )
            self._scores["eye_head"] = self._score_eye_head_coordination()
            self._scores["symmetry"] = self._score_symmetry_stability()

        # Tier 1b — FFT every 30 frames (need at least 60 frames)
        if n >= 60 and self._frame_count % 30 == 0:
            self._scores["fft"] = self._score_landmark_fft()

        # Tier 2 — pixel analysis every N frames (need at least 10 frames)
        if n >= 10 and self._frame_count % self._pixel_interval == 0:
            self._scores["hf_energy"] = self._score_hf_energy(frame, landmarks)
            self._scores["boundary"] = self._score_boundary_artifacts(
                frame, landmarks
            )
            self._scores["color"] = self._score_color_consistency(
                frame, landmarks
            )

        # Composite score
        self._composite = sum(
            self._WEIGHTS[k] * self._scores[k] for k in self._WEIGHTS
        )

        # Alert logic — need at least 60 frames (2s) before alerting
        if (
            n >= 60
            and self._composite < self._threshold
            and (timestamp_ms - self._last_alert_ms) >= self._cooldown_ms
        ):
            self._last_alert_ms = timestamp_ms
            confidence = "high" if n >= self._window else "medium"
            return {
                "type": "eyeterm_deepfake_alert",
                "authenticity_score": round(self._composite, 3),
                "signals": {k: round(v, 3) for k, v in self._scores.items()},
                "confidence": confidence,
                "timestamp_ms": timestamp_ms,
            }
        return None

    @property
    def composite_score(self) -> float:
        return self._composite

    @property
    def signal_scores(self) -> Dict[str, float]:
        return dict(self._scores)

    @property
    def ready(self) -> bool:
        """True when enough frames have been accumulated for scoring."""
        return len(self._lm_history) >= 10

    # ------------------------------------------------------------------
    # Tier 1 — Landmark-only signals
    # ------------------------------------------------------------------

    def _score_landmark_jitter(self) -> float:
        """Velocity variance of key landmarks. High jitter = deepfake."""
        arr = np.array(self._lm_history)  # (T, N, 3)
        # Frame-to-frame velocity (ignore z, use x/y only)
        vel = np.diff(arr[:, :, :2], axis=0)  # (T-1, N, 2)
        # Per-landmark velocity magnitude
        speed = np.linalg.norm(vel, axis=2)  # (T-1, N)
        # Variance of speed per landmark, then mean across landmarks
        var_per_lm = np.var(speed, axis=0)  # (N,)
        mean_var = float(np.mean(var_per_lm))

        # Jaw landmarks are weighted higher (more affected by deepfakes)
        jaw_indices = [_KEY_LANDMARKS.index(j) for j in _JAWLINE
                       if j in _KEY_LANDMARKS]
        if jaw_indices:
            jaw_var = float(np.mean(var_per_lm[jaw_indices]))
            mean_var = 0.6 * mean_var + 0.4 * jaw_var

        # Sigmoid: low variance -> 1.0 (authentic), high -> 0.0 (deepfake)
        # Threshold ~0.0001 for normalized landmarks (0..1 range)
        return _sigmoid(mean_var, k=30000.0, x0=0.00015)

    def _score_blink_naturalness(
        self, ear_values: Tuple[float, float], timestamp_ms: int
    ) -> float:
        """Blink pattern naturalness. Metronomic or absent = deepfake."""
        left_ear, right_ear = ear_values
        avg_ear = (left_ear + right_ear) / 2.0
        both_closed = avg_ear < _EAR_BLINK_THRESHOLD

        # Track blink state transitions
        if both_closed and not self._in_blink:
            self._in_blink = True
            self._blink_start_ms = timestamp_ms
        elif not both_closed and self._in_blink:
            self._in_blink = False
            duration_ms = timestamp_ms - self._blink_start_ms
            if 50 < duration_ms < 800:  # plausible blink duration
                self._blink_durations.append(duration_ms)
                if self._last_blink_end_ms > 0:
                    interval = timestamp_ms - self._last_blink_end_ms
                    if interval > 0:
                        self._blink_intervals.append(interval)
                self._last_blink_end_ms = timestamp_ms

        # Need at least 4 blinks to assess
        if len(self._blink_intervals) < 4:
            return 1.0  # not enough data, assume authentic

        intervals = np.array(self._blink_intervals, dtype=np.float64)
        durations = np.array(self._blink_durations, dtype=np.float64)

        score = 1.0

        # 1. Interval regularity — CV should be > 0.25 (humans are irregular)
        cv = float(np.std(intervals) / (np.mean(intervals) + 1e-9))
        if cv < 0.15:  # metronomic
            score *= 0.3
        elif cv < 0.25:
            score *= 0.6

        # 2. Blink rate — normal is 15-20/min
        window_s = (self._ts_history[-1] - self._ts_history[0]) / 1000.0
        if window_s > 0:
            rate = len(self._blink_durations) / window_s * 60.0
            if rate < 5.0:  # almost no blinks
                score *= 0.4
            elif rate > 40.0:  # excessive
                score *= 0.5

        # 3. Duration — normal 100-400ms
        mean_dur = float(np.mean(durations))
        if mean_dur < 80 or mean_dur > 500:
            score *= 0.6

        return max(0.0, min(1.0, score))

    def _score_eye_head_coordination(self) -> float:
        """Cross-correlation of iris vs head movement. Decoupled = deepfake."""
        n = len(self._iris_history)
        if n < 15:
            return 1.0

        iris = np.array(self._iris_history)  # (T, 2)
        head = np.array(self._head_history)  # (T, 2)

        # Deltas (velocity)
        iris_dx = np.diff(iris[:, 0])
        head_dx = np.diff(head[:, 0])

        # Normalize
        iris_std = np.std(iris_dx)
        head_std = np.std(head_dx)
        if iris_std < 1e-6 or head_std < 1e-6:
            return 1.0  # no movement, can't assess

        iris_n = (iris_dx - np.mean(iris_dx)) / iris_std
        head_n = (head_dx - np.mean(head_dx)) / head_std

        # Cross-correlation (limited lag range)
        max_lag = min(8, n // 4)
        best_corr = 0.0
        best_lag = 0
        for lag in range(-max_lag, max_lag + 1):
            if lag >= 0:
                corr = float(np.mean(iris_n[lag:] * head_n[:len(head_n) - lag]))
            else:
                corr = float(np.mean(iris_n[:len(iris_n) + lag] * head_n[-lag:]))
            if abs(corr) > abs(best_corr):
                best_corr = corr
                best_lag = lag

        # Real faces: positive correlation, eyes lead head (lag 1-5 frames)
        # Score based on correlation strength and lag plausibility
        if best_corr < 0.15:
            return 0.3  # no coordination
        if best_corr < 0.3:
            return 0.6

        # Lag penalty: real faces have lag 0-5 (eyes lead or simultaneous)
        if 0 <= best_lag <= 5:
            return 1.0
        elif -2 <= best_lag <= 8:
            return 0.8
        else:
            return 0.5

    def _score_symmetry_stability(self) -> float:
        """Variance of left-right symmetry ratios. Fluctuating = deepfake."""
        n = len(self._lm_history)
        if n < 10:
            return 1.0

        arr = np.array(self._lm_history)  # (T, N_key, 3)
        nose_idx = _KEY_LANDMARKS.index(_NOSE_TIP)

        ratios = []
        for left_lm, right_lm in _SYMMETRY_PAIRS:
            if left_lm not in _KEY_LANDMARKS or right_lm not in _KEY_LANDMARKS:
                continue
            li = _KEY_LANDMARKS.index(left_lm)
            ri = _KEY_LANDMARKS.index(right_lm)
            # Distance from nose to left vs right (xy only)
            d_left = np.linalg.norm(arr[:, li, :2] - arr[:, nose_idx, :2], axis=1)
            d_right = np.linalg.norm(arr[:, ri, :2] - arr[:, nose_idx, :2], axis=1)
            ratio = d_left / (d_right + 1e-9)
            ratios.append(np.var(ratio))

        if not ratios:
            return 1.0

        mean_var = float(np.mean(ratios))
        # Sigmoid: low variance -> authentic, high -> deepfake
        return _sigmoid(mean_var, k=5000.0, x0=0.001)

    # ------------------------------------------------------------------
    # Tier 1b — Temporal FFT
    # ------------------------------------------------------------------

    def _score_landmark_fft(self) -> float:
        """FFT of landmark positions. Energy in 5-15 Hz band = artifacts."""
        arr = np.array(self._lm_history)  # (T, N, 3)
        T = arr.shape[0]
        if T < 30:
            return 1.0

        fps = 30.0  # assumed
        freqs = np.fft.rfftfreq(T, d=1.0 / fps)

        # Frequency bands
        natural_mask = (freqs >= 0.5) & (freqs <= 3.0)
        wave_mask = (freqs >= 5.0) & (freqs <= 15.0)

        if not np.any(natural_mask) or not np.any(wave_mask):
            return 1.0

        wave_ratios = []
        # Analyze x and y of a subset of landmarks
        subset = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]  # first 10 key landmarks
        subset = subset[:min(len(subset), arr.shape[1])]

        for li in subset:
            for axis in (0, 1):  # x, y
                signal = arr[:, li, axis]
                signal = signal - np.mean(signal)  # detrend
                fft_mag = np.abs(np.fft.rfft(signal))

                natural_energy = float(np.mean(fft_mag[natural_mask]) + 1e-9)
                wave_energy = float(np.mean(fft_mag[wave_mask]))
                wave_ratios.append(wave_energy / natural_energy)

        mean_ratio = float(np.mean(wave_ratios))
        # Sigmoid: low wave ratio -> authentic, high -> deepfake artifact
        return _sigmoid(mean_ratio, k=8.0, x0=0.5)

    # ------------------------------------------------------------------
    # Tier 2 — Pixel-level signals
    # ------------------------------------------------------------------

    def _score_hf_energy(self, frame: np.ndarray, landmarks: Any) -> float:
        """High-frequency energy ratio in face crop. Low = GAN blur."""
        x1, y1, x2, y2 = _face_bbox(landmarks, frame.shape)
        if x2 - x1 < 20 or y2 - y1 < 20:
            return 1.0

        crop = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
        blurred = cv2.GaussianBlur(gray, (7, 7), 0)
        hf = gray - blurred

        total_energy = float(np.mean(gray ** 2)) + 1e-9
        hf_energy = float(np.mean(hf ** 2))
        ratio = hf_energy / total_energy

        # Real faces typically have ratio 0.002-0.01
        # Very low = GAN smoothing, very high = GAN grid artifacts
        if ratio < 0.001:
            return 0.3  # suspiciously smooth
        elif ratio < 0.002:
            return 0.6
        elif ratio > 0.02:
            return 0.5  # unusually noisy / artifact-heavy
        return 1.0

    def _score_boundary_artifacts(
        self, frame: np.ndarray, landmarks: Any
    ) -> float:
        """Gradient discontinuity at jawline. Blending seams = deepfake."""
        mask = _jawline_mask(landmarks, frame.shape, band_width=5)
        if np.sum(mask > 0) < 50:
            return 1.0

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        grad_mag = np.sqrt(sobel_x ** 2 + sobel_y ** 2)

        # Gradient at boundary vs overall face gradient
        boundary_grad = float(np.mean(grad_mag[mask > 0]))

        x1, y1, x2, y2 = _face_bbox(landmarks, frame.shape, pad_frac=0.0)
        face_grad = float(np.mean(grad_mag[y1:y2, x1:x2])) + 1e-9
        ratio = boundary_grad / face_grad

        # Real faces: boundary gradient ~ face gradient (ratio ~1.0)
        # Deepfakes: boundary has higher gradient (blending seam), ratio > 1.5
        if ratio > 2.5:
            return 0.2
        elif ratio > 1.8:
            return 0.5
        elif ratio > 1.3:
            return 0.7
        return 1.0

    def _score_color_consistency(
        self, frame: np.ndarray, landmarks: Any
    ) -> float:
        """LAB color shift between face and neck region."""
        h, w = frame.shape[:2]
        chin_y = int(landmarks[_CHIN].y * h)
        nose_x = int(landmarks[_NOSE_TIP].x * w)

        # Face region: above chin
        x1, y1, x2, y2 = _face_bbox(landmarks, frame.shape, pad_frac=0.0)
        face_crop = frame[y1:chin_y, x1:x2]

        # Neck region: below chin (same width, ~30% of face height)
        neck_h = max(10, int((y2 - y1) * 0.3))
        neck_y2 = min(h, chin_y + neck_h)
        neck_crop = frame[chin_y:neck_y2, x1:x2]

        if face_crop.size < 100 or neck_crop.size < 100:
            return 1.0

        face_lab = cv2.cvtColor(face_crop, cv2.COLOR_BGR2LAB).astype(np.float32)
        neck_lab = cv2.cvtColor(neck_crop, cv2.COLOR_BGR2LAB).astype(np.float32)

        face_mean = face_lab.reshape(-1, 3).mean(axis=0)
        neck_mean = neck_lab.reshape(-1, 3).mean(axis=0)

        # a and b channel shifts (chrominance)
        delta_a = abs(face_mean[1] - neck_mean[1])
        delta_b = abs(face_mean[2] - neck_mean[2])
        max_shift = max(delta_a, delta_b)

        # Real faces: delta < 5. Deepfakes: delta > 10
        if max_shift > 15:
            return 0.2
        elif max_shift > 10:
            return 0.5
        elif max_shift > 7:
            return 0.7
        return 1.0
