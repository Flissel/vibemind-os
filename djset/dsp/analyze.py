"""Messungen am Audio. Dieses Modul faellt keine Urteile.

Wo eine Messung mehrdeutig ist, gibt es eine Konfidenz dazu und ueberlaesst
die Entscheidung dem aufrufenden Agenten. Die Konfidenzen bedeuten dabei
Verschiedenes, und zwar genau das, was hier ehrlich messbar ist:

- `Key.confidence` ist der Abstand zum zweitbesten Tonart-Kandidaten, sagt
  also tatsaechlich etwas ueber die Mehrdeutigkeit Dur/Moll aus
- `BeatGrid.confidence` ist die Regelmaessigkeit der Beatabstaende
- `Bpm.confidence` ist derselbe Wert und sagt damit NICHTS ueber die
  Tempo-Oktave: eine saubere Halftime-Lesung ist gleichmaessig und bekommt
  eine hohe Konfidenz. Die Oktavenfrage entscheidet der Agent am
  BPM-Korridor des Profils, siehe snap_to_range und die Docstrings von Bpm
  und BeatGrid in djset.contracts.
"""

import math
from pathlib import Path

import librosa
import numpy as np
import pyloudnorm
import soundfile as sf

from djset.contracts import BeatGrid, Bpm, Key, Loudness, MixRegions, TrackMeasurements

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


def _detect_beats(
    path: Path, hint_bpm: float | None = None
) -> tuple[np.ndarray, float]:
    """Gemeinsamer Kern von beat_times() und detect_bpm_and_grid().

    Liefert die erkannten Beat-Zeitpunkte sowie librosas rohe Tempo-Schaetzung
    (als Rueckfall, falls zu wenige Beats fuer eine eigene Berechnung
    uebrigbleiben - siehe detect_bpm_and_grid).
    """
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

    times = librosa.frames_to_time(beat_frames, sr=sr)
    if times.size == 0:
        raise ValueError(f"keine Beats erkennbar in {path}")
    times = _drop_unsupported_edge_beats(times, signal, sr)
    return times, raw_tempo_value


def beat_times(path: Path, hint_bpm: float | None = None) -> np.ndarray:
    """Die erkannten Beat-Zeitpunkte einer Datei, in Sekunden."""
    times, _raw_tempo_value = _detect_beats(path, hint_bpm)
    return times


def detect_bpm_and_grid(
    path: Path, hint_bpm: float | None = None
) -> tuple[Bpm, BeatGrid]:
    """Tempo und Taktraster. Der Hinweis lenkt librosa in die richtige Oktave."""
    beat_times_, raw_tempo_value = _detect_beats(path, hint_bpm)

    # Regelmaessigkeit: wie gleichmaessig die Beatabstaende sind. Ein sauberer
    # Viervierteltakt liefert nahezu konstante Abstaende. Das ist eine Aussage
    # ueber die Qualitaet des Rasters, NICHT ueber die Tempo-Oktave - eine
    # saubere Halftime-Lesung schneidet hier bestens ab. Siehe die
    # Docstrings von Bpm und BeatGrid in djset.contracts.
    intervals = np.diff(beat_times_)
    if intervals.size == 0:
        regularity = 0.0
        tempo_value = raw_tempo_value
    else:
        # Die initiale Tempo-Schaetzung von librosa ist auf ganzzahlige
        # Vielfache von hop_length quantisiert (bei sr=44100, hop=512 ca.
        # 11.6 ms), was bei exaktem Material zu einem Fehler von mehr als
        # 1 BPM fuehrt. Der Mittelwert der Beatabstaende ist praeziser, weil
        # er die gesamte Spanne des Stuecks nutzt.
        #
        # Was er NICHT tut: ueber die einzelnen Beats mitteln. np.mean(diff)
        # ist algebraisch (letzter - erster) / (n - 1) - die Summe der
        # Differenzen teleskopiert, alles zwischen dem ersten und dem letzten
        # Beat kuerzt sich weg. Der Gewinn kommt allein aus der langen
        # Messstrecke, nicht aus einer Mittelung ueber viele Messungen.
        #
        # np.median waere deshalb kein Ersatz: der Median einzelner
        # Beatabstaende traegt die Hop-Quantisierung wieder herein (~3.2 BPM
        # bei 128 BPM) und bricht den Clicktrack-Test.
        #
        # Offene Schwaeche der langen Messstrecke: fallen mitten im Stueck
        # Beats aus (Breakdown ohne Drums), schrumpft n - 1, waehrend die
        # Spanne gleich bleibt - das Tempo faellt dann zu niedrig aus. Die
        # saubere Loesung waere eine robuste Ausgleichsgerade durch die
        # Beat-Zeiten ueber ihren Index; die will an echtem Material geprueft
        # werden und bleibt deshalb bewusst offen statt geraten.
        tempo_value = 60.0 / float(np.mean(intervals))
        spread = float(np.std(intervals) / np.mean(intervals))
        regularity = float(max(0.0, min(1.0, 1.0 - spread * 4.0)))

    bar_length = BEATS_PER_BAR * 60.0 / tempo_value
    grid = BeatGrid(
        first_downbeat_s=float(beat_times_[0]),
        bar_length_s=bar_length,
        confidence=regularity,
    )
    # Bpm.confidence bekommt denselben Wert. Das DSP-Modul kann aus dem, was
    # librosa liefert, keine ehrliche Aussage ueber die Tempo-Oktave ableiten
    # (die Oktave waehlt der Beat-Tracker ueber seinen start_bpm-Prior, nicht
    # ueber ein Mass, das wir hier auslesen koennten). Ein erfundener
    # Zweitwert waere schlimmer als ein benannter gleicher - der Vertrag sagt
    # deshalb ausdruecklich, was dieses Feld bedeutet und was nicht.
    return Bpm(value=tempo_value, confidence=regularity), grid


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
    # errstate: bei einer stillen oder konstanten Datei ist das Chroma-Profil
    # ueberall gleich, np.corrcoef teilt dann durch eine Standardabweichung
    # von 0 und wirft je Durchlauf ein "invalid value encountered in divide" -
    # rund 50 Warnungen fuer eine einzige Datei. Die Korrelationen sind dann
    # NaN, und der Abstand zum Zweitbesten faellt weiter unten auf 0.0
    # zusammen: genau die Konfidenz 0.0 ist das Signal, an dem der Agent eine
    # unbrauchbare Datei erkennt.
    # Die Warnungen sagen daneben nichts Eigenes und wuerden unter
    # filterwarnings=error (siehe pyproject.toml) eine ganze Analyse
    # abbrechen. (librosa warnt bei solchem Material zusaetzlich einmal mit
    # "Trying to estimate tuning from empty frequency set" - das kommt aus
    # chroma_cqt und bleibt bewusst stehen.)
    with np.errstate(invalid="ignore"):
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


BARS_PER_ENERGY_STEP = 4

# Wie weit unter der Spitzenenergie ein Abschnitt noch als "im Groove" gilt:
# 6 dB, also halbe Leistung. Bewusst als dB-ABSTAND und nicht als Anteil -
# die Energiekurve ist linear in dB (siehe energy_curve), auf ihr ist "halbe
# Leistung" eine Subtraktion. Ein Faktor 0.5 auf dieser Skala waere etwas
# voellig anderes: bei -10 dBFS Spitzenenergie entspraeche er 25 dB unter
# Peak, und die Fensterbreite haengt zusaetzlich am Absolutpegel des Tracks.
_QUIET_MARGIN_DB = 6.0

# Feste Referenzspanne der Energiekurve: -60 dBFS wird zu 0.0, 0 dBFS
# (Vollaussteuerung) zu 1.0, alles ausserhalb wird geklemmt. -60 dBFS liegt
# weit unter allem, was in einem gemasterten Track noch als Musik durchgeht,
# und weit ueber der digitalen Null.
_ENERGY_FLOOR_DBFS = -60.0


def _rms_to_energy(rms: float) -> float:
    """Bildet einen RMS-Wert gegen die feste dBFS-Referenz auf 0..1 ab."""
    if rms <= 0.0:
        return 0.0
    dbfs = 20.0 * math.log10(rms)
    scaled = (dbfs - _ENERGY_FLOOR_DBFS) / -_ENERGY_FLOOR_DBFS
    return float(min(1.0, max(0.0, scaled)))


def energy_curve(path: Path, grid: BeatGrid) -> list[float]:
    """RMS je vier Takte, gegen eine feste Referenz auf 0..1 abgebildet.

    Bewusst NICHT auf den Spitzenwert des jeweiligen Tracks normiert. Bei
    Normierung je Track hat jede Kurve ihr Maximum bei exakt 1.0, und ein
    sparsamer Ambient-Opener ist von einem Peaktime-Roller nicht mehr zu
    unterscheiden. Genau diesen Vergleich braucht aber die naechste Stufe:

    - Stufe 5 legt die Tracks auf profile.energy_arc, dessen Stuetzpunkte
      absolute Zielenergien 0..1 sind
    - die Aehnlichkeitsregel des Spec ("Energiedifferenz unter 0,1")
      vergleicht zwei verschiedene Tracks miteinander
    - Stufe 7 prueft, ob das fertige Set "die Energiekurve haelt"

    Alle drei sind auf je Track normierten Kurven unmoeglich. Die Referenz
    ist deshalb absolut: RMS in dBFS, linear von _ENERGY_FLOOR_DBFS (0.0)
    bis 0 dBFS (1.0), ausserhalb geklemmt. Zwei Tracks sind damit
    vergleichbar, solange sie aehnlich gemastert sind - was fuer Material
    aus einer Quelle (Suno) zutrifft. Ein Angleichen der Lautheit vor der
    Messung waere die naechste Ausbaustufe; die dafuer noetige Zahl misst
    dieses Modul mit lufs_integrated bereits.
    """
    signal, sr = _load_audio(path)
    step_seconds = grid.bar_length_s * BARS_PER_ENERGY_STEP
    step_samples = max(1, int(step_seconds * sr))

    values: list[float] = []
    for start in range(0, signal.size, step_samples):
        chunk = signal[start:start + step_samples]
        if chunk.size == 0:
            continue
        rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
        values.append(_rms_to_energy(rms))

    if not values:
        return [0.0]
    return values


def find_mix_regions(
    energy: list[float], grid: BeatGrid, duration_s: float
) -> MixRegions:
    """Wo das Intro endet und das Outro beginnt.

    Massstab sind 6 dB unter der Spitzenenergie DIESES Tracks: darunter
    laeuft er noch nicht bzw. nicht mehr im vollen Groove und taugt zum
    Ueberblenden. Der Bezug auf den eigenen Spitzenwert ist hier richtig -
    anders als bei der Kurve selbst (siehe energy_curve) geht es nicht um den
    Vergleich zweier Tracks, sondern um Auf- und Abbau innerhalb eines
    einzigen.

    Der Abstand wird SUBTRAHIERT, nicht multipliziert: die Kurve ist linear in
    dB, ein Faktor waere darauf keine Pegeldifferenz mehr, sondern eine, die
    mit dem Absolutpegel des Tracks waechst. Ein Intro 10 dB unter dem Koerper
    - bei Suno-Material der Normalfall - verschwand so vollstaendig, der ganze
    Track galt als "im Groove". Der Regressionstest dazu bezieht seine Kurve
    aus energy_curve statt aus einer Literalliste, siehe
    tests/dsp/test_measurements.py::test_mix_regions_follow_a_graded_track.

    So gebaut ist die Schwelle wieder skaleninvariant: ein leiser, aber
    hoerbarer Track behaelt sein Intro. Erst wenn die Spitzenenergie selbst
    unter den Abstand faellt (unterhalb von etwa -54 dBFS), rutscht die
    Schwelle unter null und der ganze Track gilt als mischbar - bei einem
    Track dieser Lautstaerke die richtige Antwort.

    Erwartet wird eine nichtleere Kurve, so wie energy_curve sie liefert.
    Einen Zweig fuer "nichts ist laut genug" braucht es nicht: die Schwelle
    liegt nie ueber dem Spitzenwert, der Abschnitt mit dem Spitzenwert zaehlt
    also immer mit, und die Liste der lauten Abschnitte ist nie leer. Der
    gleichfoermige Fall (konstante Kurve, auch die Nullkurve) faellt damit in
    den Normalweg - alles gilt als laut, das Intro endet bei 0.0, das Outro
    beginnt am Trackende. Genau das ist die richtige Antwort, wenn es keinen
    erkennbaren Auf- und Abbau gibt.
    """
    step_seconds = grid.bar_length_s * BARS_PER_ENERGY_STEP
    threshold = max(energy) - _QUIET_MARGIN_DB / -_ENERGY_FLOOR_DBFS

    loud = [index for index, value in enumerate(energy) if value >= threshold]
    intro_end = min(loud) * step_seconds
    outro_start = min((max(loud) + 1) * step_seconds, duration_s)
    if intro_end >= outro_start:
        intro_end = 0.0
    return MixRegions(intro_end_s=intro_end, outro_start_s=outro_start)


# Bodenwert fuer Pegelangaben in dB. Digitale Stille waere rechnerisch -inf;
# -inf ist aber kein gueltiger JSON-Zahlenwert und faellt beim Roundtrip
# durch das Loudness-Schema (siehe djset.contracts.Loudness).
_LEVEL_FLOOR_DB = -120.0


def measure_loudness(path: Path) -> Loudness:
    """Integrierte Lautheit nach EBU R128 und True Peak."""
    signal, sr = sf.read(str(path))
    meter = pyloudnorm.Meter(sr)
    # pyloudnorm liefert fuer digitale Stille -inf. -inf ueberlebt den
    # JSON-Roundtrip nicht (dump schreibt null, validate lehnt null ab) und
    # wuerde damit `build --resume` an einem einzigen stillen Track
    # zerreissen. Bodenwert wie beim True Peak: -120 dB.
    lufs = max(_LEVEL_FLOOR_DB, float(meter.integrated_loudness(signal)))

    mono = signal if signal.ndim == 1 else np.mean(signal, axis=1)
    # True Peak naeherungsweise ueber vierfaches Oversampling
    upsampled = librosa.resample(
        mono.astype(np.float32), orig_sr=sr, target_sr=sr * 4
    )
    peak = float(np.max(np.abs(upsampled)))
    true_peak = 20.0 * np.log10(peak) if peak > 0 else _LEVEL_FLOOR_DB
    return Loudness(lufs_integrated=lufs, true_peak=float(true_peak))


def measure_track(
    path: Path, track_id: str, hint_bpm: float | None = None
) -> TrackMeasurements:
    """Alle Messungen eines Tracks in einem Aufruf."""
    duration = float(librosa.get_duration(path=str(path)))
    bpm, grid = detect_bpm_and_grid(path, hint_bpm=hint_bpm)
    key = detect_key(path)
    energy = energy_curve(path, grid)
    regions = find_mix_regions(energy, grid, duration)
    loudness = measure_loudness(path)

    return TrackMeasurements(
        id=track_id,
        path=str(path),
        duration_s=duration,
        bpm=bpm,
        beatgrid=grid,
        key=key,
        energy=energy,
        mix_regions=regions,
        loudness=loudness,
    )
