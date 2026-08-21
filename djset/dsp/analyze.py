"""Messungen am Audio. Dieses Modul faellt keine Urteile.

Wo eine Messung mehrdeutig ist (Tempo-Oktave, Dur/Moll), gibt es eine
Konfidenz zurueck und ueberlaesst die Entscheidung dem aufrufenden Agenten.
"""

from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from djset.contracts import BeatGrid, Bpm, Key

BEATS_PER_BAR = 4


def _load_audio(path: Path) -> tuple[np.ndarray, int]:
    """Laedt eine Audiodatei ueber soundfile statt ueber librosa.load.

    librosa.load prueft vor jedem Ladevorgang unbedingt
    audioread.available_backends(), was unter Python 3.11 die als
    deprecated markierten Stdlib-Module 'aifc', 'audioop' und 'sunau'
    importiert und DeprecationWarnings ausloest - unabhaengig vom
    tatsaechlichen Dateiformat. Das laesst sich nicht per Aufrufsignatur an
    librosa.load abstellen, deshalb lesen wir direkt ueber soundfile (WAV,
    FLAC und MP3 werden vom installierten libsndfile gelesen, siehe
    tests/dsp/test_bpm.py) und mischen bei Bedarf auf Mono ab.

    Spaetere Erweiterungen dieses Moduls (Tasks 4/5) sollten diese Funktion
    fuer jeden Audio-Ladevorgang wiederverwenden statt erneut
    librosa.load(...) aufzurufen, sonst kommen die Deprecation-Warnings
    zurueck.
    """
    with sf.SoundFile(str(path)) as sf_desc:
        sr = sf_desc.samplerate
        signal = sf_desc.read(dtype="float32", always_2d=False).T
    if signal.ndim > 1:
        signal = librosa.to_mono(signal)
    return signal, int(sr)


# -50 dBFS: grosszuegiger Sicherheitsabstand zwischen digitaler Stille
# (exaktes Nullsignal bzw. Rauschen im Dither-Bereich, ueblicherweise
# < -80 dBFS) und einer leisen, aber echten Intro vor einem Drop
# (ueblicherweise nicht leiser als etwa -30 dBFS). Bewusst absolut in
# dBFS, nicht relativ zum Spitzenpegel des Tracks: eine leise Intro darf
# nicht verworfen werden, nur weil der Rest des Tracks lauter ist.
_SILENCE_THRESHOLD_DBFS = -50.0
_SILENCE_THRESHOLD_LINEAR = 10.0 ** (_SILENCE_THRESHOLD_DBFS / 20.0)


def _is_silent(signal: np.ndarray, sr: int, time_s: float, window_s: float = 0.05) -> bool:
    """Prueft, ob im Zeitfenster um time_s digitale Stille herrscht.

    Wird benutzt, um Phantom-Beats an den Raendern zu erkennen (siehe
    detect_bpm_and_grid, trim=False). Die Onset-Staerke selbst kann das an
    Rand-Frames nicht leisten: der allererste Analyse-Frame von
    librosa.onset.onset_strength ist strukturell immer 0.0, weil fuer die
    Flussberechnung kein Vorgaengerframe existiert - unabhaengig davon, ob
    dort tatsaechlich ein Klang liegt. Deshalb schauen wir stattdessen auf
    das rohe Signal, und zwar in absoluten dBFS (siehe
    _SILENCE_THRESHOLD_DBFS), nicht relativ zum Spitzenpegel des Tracks.

    Das ist eine Messung (liegt dort ueberhaupt Signal?), keine Wertung
    (ist der Beat "zu leise, um zu zaehlen"?) - Letzteres waere eine
    Entscheidung ueber Trackinhalt und gehoert dem Agenten, nicht diesem
    Modul.
    """
    center = int(round(time_s * sr))
    half = max(1, int(round(window_s * sr / 2)))
    lo = max(0, center - half)
    hi = min(signal.shape[-1], center + half)
    if hi <= lo:
        return True
    window_peak = float(np.max(np.abs(signal[lo:hi])))
    return window_peak < _SILENCE_THRESHOLD_LINEAR


def _drop_unsupported_edge_beats(
    beat_times: np.ndarray, signal: np.ndarray, sr: int
) -> np.ndarray:
    """Entfernt Phantom-Beats an Anfang und Ende, die in Stille liegen.

    trim=False bewahrt echte Randbeats (siehe detect_bpm_and_grid), erlaubt
    dem Beat-Tracker aber auch, am Rand einen zusaetzlichen Beat genau eine
    Periode vor dem ersten bzw. nach dem letzten tatsaechlich gemessenen
    Onset zu platzieren - ohne jede Entsprechung im Signal, etwa in einem
    stillen Vorlauf vor dem eigentlichen Trackstart. Diese Funktion
    verwirft nur Randbeats, die in digitaler Stille liegen. Eine leise,
    aber echte Intro (z. B. eine gefilterte Passage vor einem Drop) bleibt
    erhalten - das Unterscheidungsmerkmal ist Stille, nicht Lautstaerke.
    """
    while beat_times.size > 1 and _is_silent(signal, sr, float(beat_times[0])):
        beat_times = beat_times[1:]
    while beat_times.size > 1 and _is_silent(signal, sr, float(beat_times[-1])):
        beat_times = beat_times[:-1]
    return beat_times


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
    # t~=0 unterschlaegt und first_downbeat_s verfaelscht. Das eroeffnet im
    # Gegenzug die Moeglichkeit eines Phantom-Beats ohne Signal-Entsprechung
    # (z. B. in einem stillen Vorlauf) - dem begegnet
    # _drop_unsupported_edge_beats() weiter unten gezielt.
    raw_tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset, sr=sr, units="frames", trim=False, **kwargs
    )
    raw_tempo_value = float(np.atleast_1d(raw_tempo)[0])

    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    if beat_times.size == 0:
        raise ValueError(f"keine Beats erkennbar in {path}")
    beat_times = _drop_unsupported_edge_beats(beat_times, signal, sr)

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


# Krumhansl-Schmuckler-Profile: wie stark jede Tonstufe in Dur bzw. Moll wiegt.
_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

# Camelot-Zahl je Grundton, Index 0 = C. Dur ist B, Moll ist A.
_MAJOR_CAMELOT = [8, 3, 10, 5, 12, 7, 2, 9, 4, 11, 6, 1]
_MINOR_CAMELOT = [5, 12, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10]


def detect_key(path: Path) -> Key:
    """Tonart in Camelot-Notation ueber Chroma-Korrelation."""
    signal, sr = _load_audio(path)
    chroma = librosa.feature.chroma_cqt(y=signal, sr=sr)
    weights = np.mean(chroma, axis=1)

    scores: list[tuple[float, str]] = []
    for tonic in range(12):
        rotated = np.roll(weights, -tonic)
        for profile, table, letter in (
            (_MAJOR_PROFILE, _MAJOR_CAMELOT, "B"),
            (_MINOR_PROFILE, _MINOR_CAMELOT, "A"),
        ):
            correlation = float(np.corrcoef(rotated, profile)[0, 1])
            scores.append((correlation, f"{table[tonic]}{letter}"))

    scores.sort(reverse=True)
    best_score, best_camelot = scores[0]
    runner_up = scores[1][0]

    # Konfidenz aus dem Abstand zum Zweitplatzierten: ein knapper Sieg
    # bedeutet eine mehrdeutige Tonart.
    margin = max(0.0, best_score - runner_up)
    confidence = float(max(0.0, min(1.0, margin * 5.0)))
    return Key(camelot=best_camelot, confidence=confidence)
