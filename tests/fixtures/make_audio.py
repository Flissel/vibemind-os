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


def click_track_with_weak_offbeats(
    path: Path, bpm: float, bars: int, offbeat_db: float = -26.0, sr: int = SR
) -> Path:
    """Clicktrack, bei dem jeder zweite Beat stark abgeschwaecht ist.

    So klingt Halftime-Material: der Puls liegt hoerbar auf der Haelfte des
    tatsaechlichen Tempos. librosa liest hier dann auch bpm/2 - bei perfekt
    gleichmaessigen Abstaenden und damit hoher Konfidenz. Genau das pinnt
    tests/dsp/test_bpm.py::test_halftime_reading_still_scores_high_confidence.
    """
    beat_seconds = 60.0 / bpm
    beats = bars * 4
    total = int(np.ceil(beats * beat_seconds * sr)) + sr
    signal = np.zeros(total, dtype=np.float32)

    click_len = int(0.01 * sr)
    envelope = np.exp(-np.linspace(0.0, 12.0, click_len)).astype(np.float32)
    tone = np.sin(2 * np.pi * 1200.0 * np.arange(click_len) / sr).astype(np.float32)
    click = envelope * tone

    quiet_gain = 10.0 ** (offbeat_db / 20.0)
    for beat in range(beats):
        start = int(beat * beat_seconds * sr)
        gain = 1.0 if beat % 2 == 0 else quiet_gain
        signal[start:start + click_len] += click * gain

    sf.write(path, signal, sr)
    return path


def near_silent_click_track(
    path: Path, bpm: float, bars: int, peak_db: float = -60.0, sr: int = SR
) -> Path:
    """Clicktrack knapp ueber der Nulllinie - fuer pyloudnorm digitale Stille.

    Unterhalb des absoluten Gates von -70 LUFS liefert pyloudnorm -inf. Genau
    dieser Wert ueberlebt den JSON-Roundtrip nicht, siehe
    tests/dsp/test_measurements.py::test_near_silent_track_survives_the_run_roundtrip.
    Anders als eine komplett stille Datei hat dieses Material noch Onsets,
    laeuft also durch die volle measure_track-Kette.
    """
    beat_seconds = 60.0 / bpm
    beats = bars * 4
    total = int(np.ceil(beats * beat_seconds * sr)) + sr
    signal = np.zeros(total, dtype=np.float32)

    click_len = int(0.01 * sr)
    envelope = np.exp(-np.linspace(0.0, 12.0, click_len)).astype(np.float32)
    tone = np.sin(2 * np.pi * 1200.0 * np.arange(click_len) / sr).astype(np.float32)
    click = envelope * tone * (10.0 ** (peak_db / 20.0))

    for beat in range(beats):
        start = int(beat * beat_seconds * sr)
        signal[start:start + click_len] += click * (1.0 if beat % 4 == 0 else 0.5)

    sf.write(path, signal, sr)
    return path
