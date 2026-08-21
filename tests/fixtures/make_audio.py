"""Synthetisches Referenzmaterial mit exakt bekannten Eigenschaften.

Damit sind DSP-Tests deterministisch und brauchen keine echten Audiodateien.
"""

from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100


def click_track(path: Path, bpm: float, bars: int, sr: int = SR) -> Path:
    """4/4-Clicktrack. Jeder Beat ein kurzer Impuls, Eins jeweils lauter."""
    beat_seconds = 60.0 / bpm
    beats = bars * 4
    total = int(np.ceil(beats * beat_seconds * sr)) + sr
    signal = np.zeros(total, dtype=np.float32)

    click_len = int(0.01 * sr)
    envelope = np.exp(-np.linspace(0.0, 12.0, click_len)).astype(np.float32)
    tone = np.sin(2 * np.pi * 1200.0 * np.arange(click_len) / sr).astype(np.float32)
    click = envelope * tone

    for beat in range(beats):
        start = int(beat * beat_seconds * sr)
        gain = 1.0 if beat % 4 == 0 else 0.5
        signal[start:start + click_len] += click * gain

    sf.write(path, signal, sr)
    return path
