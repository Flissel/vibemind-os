"""Messungen am Audio. Dieses Modul faellt keine Urteile.

Wo eine Messung mehrdeutig ist (Tempo-Oktave, Dur/Moll), gibt es eine
Konfidenz zurueck und ueberlaesst die Entscheidung dem aufrufenden Agenten.
"""

from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from djset.contracts import BeatGrid, Bpm

BEATS_PER_BAR = 4


def _load_audio(path: Path) -> tuple[np.ndarray, int]:
    """Laedt eine Audiodatei ueber soundfile statt ueber librosa.load.

    librosa.load prueft vor jedem Ladevorgang unbedingt
    audioread.available_backends(), was unter Python 3.11 die als
    deprecated markierten Stdlib-Module 'aifc', 'audioop' und 'sunau'
    importiert und DeprecationWarnings ausloest - unabhaengig vom
    tatsaechlichen Dateiformat. Das laesst sich nicht per Aufrufsignatur an
    librosa.load abstellen, deshalb lesen wir direkt ueber soundfile (WAV
    reicht fuer unsere Zwecke) und mischen bei Bedarf auf Mono ab.
    """
    with sf.SoundFile(str(path)) as sf_desc:
        sr = sf_desc.samplerate
        signal = sf_desc.read(dtype="float32", always_2d=False).T
    if signal.ndim > 1:
        signal = librosa.to_mono(signal)
    return signal, int(sr)


def snap_to_range(bpm: float, low: float, high: float) -> tuple[float, bool]:
    """Haelt Halftime- und Doubletime-Lesungen an.

    Liefert den korrigierten Wert und ob korrigiert wurde. Passt keine
    Verdopplung oder Halbierung, bleibt der Originalwert stehen - die
    Entscheidung darueber trifft der Agent, nicht dieses Modul.
    """
    if low <= bpm <= high:
        return bpm, False
    for factor in (2.0, 0.5, 4.0, 0.25):
        candidate = bpm * factor
        if low <= candidate <= high:
            return candidate, True
    return bpm, False


def detect_bpm_and_grid(
    path: Path, hint_bpm: float | None = None
) -> tuple[Bpm, BeatGrid]:
    """Tempo und Taktraster. Der Hinweis lenkt librosa in die richtige Oktave."""
    signal, sr = _load_audio(path)
    onset = librosa.onset.onset_strength(y=signal, sr=sr)

    kwargs: dict[str, float] = {}
    if hint_bpm is not None:
        kwargs["start_bpm"] = hint_bpm
    # trim=False: der Default verwirft den ersten erkannten Beat als "zu
    # schwach am Rand", was bei einem Clicktrack den echten Downbeat bei
    # t~=0 unterschlaegt und first_downbeat_s verfaelscht.
    raw_tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset, sr=sr, units="frames", trim=False, **kwargs
    )
    raw_tempo_value = float(np.atleast_1d(raw_tempo)[0])

    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    if beat_times.size == 0:
        raise ValueError(f"keine Beats erkennbar in {path}")

    # Konfidenz: wie gleichmaessig die Beatabstaende sind. Ein sauberer
    # Viervierteltakt liefert nahezu konstante Abstaende.
    intervals = np.diff(beat_times)
    if intervals.size == 0:
        confidence = 0.0
        tempo_value = raw_tempo_value
    else:
        # Die initiale Tempo-Schaetzung ist auf ganzzahlige Vielfache von
        # hop_length quantisiert (bei sr=44100, hop=512 ca. 11.6 ms), was
        # bei exaktem Material zu einem Fehler von mehr als 1 BPM fuehren
        # kann. Der Mittelwert der tatsaechlichen Beatabstaende mittelt
        # diese Quantisierung ueber das ganze Stueck heraus und ist praeziser.
        tempo_value = 60.0 / float(np.mean(intervals))
        spread = float(np.std(intervals) / np.mean(intervals))
        confidence = float(max(0.0, min(1.0, 1.0 - spread * 4.0)))

    bar_length = BEATS_PER_BAR * 60.0 / tempo_value
    grid = BeatGrid(first_downbeat_s=float(beat_times[0]), bar_length_s=bar_length)
    return Bpm(value=tempo_value, confidence=confidence), grid
