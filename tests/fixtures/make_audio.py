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


def click_track_with_lead_in(
    path: Path, bpm: float, bars: int, lead_in_s: float, sr: int = SR
) -> Path:
    """Wie click_track, aber mit stillem Vorlauf vor dem ersten echten Beat.

    Simuliert eine leise Intro/ein Fade-in vor dem eigentlichen Trackstart.
    Damit laesst sich pruefen, ob first_downbeat_s auf dem ersten echten Beat
    landet und nicht auf einem Artefakt im stillen Vorlauf.
    """
    beat_seconds = 60.0 / bpm
    beats = bars * 4
    lead_in_samples = int(round(lead_in_s * sr))
    total = lead_in_samples + int(np.ceil(beats * beat_seconds * sr)) + sr
    signal = np.zeros(total, dtype=np.float32)

    click_len = int(0.01 * sr)
    envelope = np.exp(-np.linspace(0.0, 12.0, click_len)).astype(np.float32)
    tone = np.sin(2 * np.pi * 1200.0 * np.arange(click_len) / sr).astype(np.float32)
    click = envelope * tone

    for beat in range(beats):
        start = lead_in_samples + int(beat * beat_seconds * sr)
        gain = 1.0 if beat % 4 == 0 else 0.5
        signal[start:start + click_len] += click * gain

    sf.write(path, signal, sr)
    return path


def click_track_with_quiet_first_beat(
    path: Path, bpm: float, bars: int, first_beat_db: float = -30.0, sr: int = SR
) -> Path:
    """Wie click_track, aber der erste Beat ist stark abgeschwaecht.

    Simuliert eine leise, aber echte Intro (z. B. eine gefilterte Passage
    vor einem Drop) statt eines stillen Vorlaufs: first_beat_db daempft nur
    den allerersten Klick, alle folgenden Beats bleiben unveraendert laut.
    Damit laesst sich pruefen, dass ein leiser, aber echter Randbeat nicht
    faelschlich als Stille verworfen wird.
    """
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
        if beat == 0:
            gain = 10.0 ** (first_beat_db / 20.0)
        else:
            gain = 1.0 if beat % 4 == 0 else 0.5
        signal[start:start + click_len] += click * gain

    sf.write(path, signal, sr)
    return path


def chord_tone(
    path: Path, semitones: list[int], seconds: float = 4.0, sr: int = SR
) -> Path:
    """Gehaltener Akkord aus Sinustoenen. Halbtoene relativ zu C4 (261.63 Hz)."""
    t = np.arange(int(seconds * sr)) / sr
    signal = np.zeros_like(t, dtype=np.float32)
    for semitone in semitones:
        freq = 261.63 * (2.0 ** (semitone / 12.0))
        # Grundton plus zwei Obertoene, damit das Chroma-Profil realistisch faellt
        for harmonic, gain in ((1, 1.0), (2, 0.4), (3, 0.2)):
            signal += (gain * np.sin(2 * np.pi * freq * harmonic * t)).astype(np.float32)
    signal /= np.max(np.abs(signal))
    sf.write(path, signal * 0.8, sr)
    return path
