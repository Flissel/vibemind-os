from pathlib import Path

import numpy as np

from djset.dsp.analyze import (
    _drop_unsupported_edge_beats,
    _is_silent,
    detect_bpm_and_grid,
    snap_to_range,
)
from tests.fixtures.make_audio import (
    click_track,
    click_track_with_lead_in,
    click_track_with_quiet_first_beat,
    click_track_with_weak_offbeats,
)


def _synthetic_click(sr: int, gain: float) -> np.ndarray:
    """Kurzer Klick als rohes Array, fuer Unit-Tests der privaten Rand-Erkennung.

    Identischer Klang wie in tests/fixtures/make_audio.py, aber ohne Umweg
    ueber eine Audiodatei - _is_silent und _drop_unsupported_edge_beats
    arbeiten direkt auf dem Signal-Array.
    """
    click_len = int(0.01 * sr)
    envelope = np.exp(-np.linspace(0.0, 12.0, click_len)).astype(np.float32)
    tone = np.sin(2 * np.pi * 1200.0 * np.arange(click_len) / sr).astype(np.float32)
    return (envelope * tone * gain).astype(np.float32)


def test_snap_doubles_halftime_reading():
    value, corrected = snap_to_range(87.0, 170.0, 178.0)
    assert value == 174.0
    assert corrected is True


def test_snap_halves_doubletime_reading():
    value, corrected = snap_to_range(250.0, 120.0, 130.0)
    assert value == 125.0
    assert corrected is True


def test_snap_leaves_value_inside_range_untouched():
    value, corrected = snap_to_range(126.0, 120.0, 130.0)
    assert value == 126.0
    assert corrected is False


def test_snap_reports_no_correction_when_hopeless():
    value, corrected = snap_to_range(100.0, 170.0, 178.0)
    assert value == 100.0
    assert corrected is False


def test_detects_bpm_of_click_track(tmp_path: Path):
    audio = click_track(tmp_path / "click.wav", bpm=128.0, bars=16)
    bpm, _grid = detect_bpm_and_grid(audio)
    assert abs(bpm.value - 128.0) < 1.0


def test_grid_bar_length_matches_bpm(tmp_path: Path):
    audio = click_track(tmp_path / "click.wav", bpm=128.0, bars=16)
    _bpm, grid = detect_bpm_and_grid(audio)
    assert abs(grid.bar_length_s - 4 * 60.0 / 128.0) < 0.02
    assert grid.first_downbeat_s < 0.1


def test_hint_pulls_detection_into_the_right_octave(tmp_path: Path):
    audio = click_track(tmp_path / "dnb.wav", bpm=174.0, bars=16)
    bpm, _grid = detect_bpm_and_grid(audio, hint_bpm=174.0)
    assert abs(bpm.value - 174.0) < 1.0


def test_first_downbeat_lands_on_first_beat_after_silent_lead_in(tmp_path: Path):
    """trim=False darf keinen Phantom-Beat im stillen Vorlauf melden.

    Regressionstest fuer Fix-Runde 1: trim=False (siehe detect_bpm_and_grid)
    erlaubt dem Beat-Tracker, am Rand einen Beat zu setzen, der keine
    Entsprechung im Signal hat. Bei einer leisen Intro/einem Fade-in muss
    first_downbeat_s trotzdem auf dem ersten echten Beat landen, nicht auf
    einem Punkt im stillen Vorlauf.
    """
    lead_in_s = 2.0
    audio = click_track_with_lead_in(
        tmp_path / "lead_in.wav", bpm=128.0, bars=16, lead_in_s=lead_in_s
    )
    _bpm, grid = detect_bpm_and_grid(audio)
    assert abs(grid.first_downbeat_s - lead_in_s) < 0.05


def test_detects_bpm_from_flac(tmp_path: Path):
    """_load_audio muss FLAC lesen koennen (Spec: Ingestion aus watch/)."""
    audio = click_track(tmp_path / "click.flac", bpm=128.0, bars=16)
    bpm, _grid = detect_bpm_and_grid(audio)
    assert abs(bpm.value - 128.0) < 1.0


def test_detects_bpm_from_mp3(tmp_path: Path):
    """_load_audio muss MP3 lesen koennen (Spec: Ingestion aus watch/).

    Das installierte libsndfile (1.2.2, via soundfile 0.14.0) kann MP3 sowohl
    schreiben als auch lesen, daher wird die MP3-Datei hier zur Testlaufzeit
    erzeugt - keine Binaerdatei im Repo noetig.
    """
    audio = click_track(tmp_path / "click.mp3", bpm=128.0, bars=16)
    bpm, _grid = detect_bpm_and_grid(audio)
    assert abs(bpm.value - 128.0) < 1.0


def test_first_downbeat_survives_quiet_but_real_first_beat(tmp_path: Path):
    """Ein leiser, aber echter Randbeat darf nicht als Stille verworfen werden.

    Regressionstest fuer Fix-Runde 2: die Randbeat-Pruefung darf nicht nach
    relativer Lautstaerke (Anteil am Spitzenpegel des Tracks) entscheiden,
    sondern nur nach absoluter Stille. Eine gefilterte, leise Intro vor
    einem Drop (hier: -30 dBFS fuer den ersten Beat) ist normaler
    Trackaufbau in den Zielgenres und muss als first_downbeat_s erhalten
    bleiben, nicht auf den naechsten (lauten) Beat springen.
    """
    audio = click_track_with_quiet_first_beat(
        tmp_path / "quiet_first.wav", bpm=128.0, bars=16, first_beat_db=-30.0
    )
    _bpm, grid = detect_bpm_and_grid(audio)
    assert grid.first_downbeat_s < 0.1


def test_is_silent_distinguishes_silence_from_quiet_signal():
    """Direkter Unit-Test der Stille-Erkennung: absolute Schwelle, nicht relativ.

    Testet _is_silent direkt statt nur ueber die volle Pipeline: BeatGrid
    exponiert keinen Wert fuer den letzten Beat, das Randverhalten am
    Trackende laesst sich also nicht ueber detect_bpm_and_grid beobachten
    (Contracts werden in dieser Fix-Runde bewusst nicht angefasst, siehe
    Report). Ein direkter Test der privaten Funktion ist hier der einzige
    Weg, das Trackende-Verhalten zu pruefen.
    """
    sr = 44100
    total = int(2.0 * sr)
    signal = np.zeros(total, dtype=np.float32)
    click_len = int(0.01 * sr)

    loud = _synthetic_click(sr, gain=1.0)
    quiet = _synthetic_click(sr, gain=10.0 ** (-30.0 / 20.0))

    pos_loud = int(0.5 * sr)
    pos_quiet_near_end = int(1.5 * sr)
    signal[pos_loud:pos_loud + click_len] += loud
    signal[pos_quiet_near_end:pos_quiet_near_end + click_len] += quiet

    # Echte Stille an beiden Raendern des Arrays.
    assert _is_silent(signal, sr, 0.05) is True
    assert _is_silent(signal, sr, 1.95) is True
    # Lauter Beat in der Mitte.
    assert _is_silent(signal, sr, 0.505) is False
    # Leiser, aber echter Beat nahe dem Ende - darf NICHT als Stille gelten.
    assert _is_silent(signal, sr, pos_quiet_near_end / sr + 0.005) is False


def test_drop_unsupported_edge_beats_strips_leading_and_trailing_silence():
    """Symmetrischer Unit-Test: Phantom-Beats fallen an BEIDEN Raendern weg.

    Der bisherige Lead-in-Test deckt nur den Anfang ab (ueber
    first_downbeat_s beobachtbar); das Trackende ist ueber BeatGrid nicht
    exponierbar. Regressionstest dafuer, dass die Randbeat-Korrektur -
    dieselbe Ursache an beiden Enden (trim=False kann an jedem Rand einen
    Beat ohne Signal-Entsprechung setzen) - tatsaechlich symmetrisch wirkt
    und nicht nur zufaellig am Anfang, wo sie ueber die bestehenden Tests
    laeuft.
    """
    sr = 44100
    total = int(2.0 * sr)
    signal = np.zeros(total, dtype=np.float32)
    click_len = int(0.01 * sr)
    loud = _synthetic_click(sr, gain=1.0)

    pos_a = int(0.5 * sr)
    pos_b = int(1.5 * sr)
    signal[pos_a:pos_a + click_len] += loud
    signal[pos_b:pos_b + click_len] += loud

    # Phantom-Beats in Stille an beiden Raendern, dazwischen die zwei
    # echten Beats.
    beat_times = np.array([0.05, 0.5, 1.5, 1.95])
    cleaned = _drop_unsupported_edge_beats(beat_times, signal, sr)

    assert list(cleaned) == [0.5, 1.5]


def test_drop_unsupported_edge_beats_keeps_quiet_but_real_edges():
    """Symmetrisches Gegenstueck: leise, aber echte Randbeats bleiben stehen.

    Wenn an beiden Raendern ein leiser, aber echter Beat liegt (kein
    Phantom), darf _drop_unsupported_edge_beats nichts verwerfen - weder
    vorne (bereits ueber die volle Pipeline getestet, siehe
    test_first_downbeat_survives_quiet_but_real_first_beat) noch hinten
    (nur hier direkt pruefbar, siehe Docstring oben).
    """
    sr = 44100
    total = int(2.0 * sr)
    signal = np.zeros(total, dtype=np.float32)
    click_len = int(0.01 * sr)
    quiet = _synthetic_click(sr, gain=10.0 ** (-30.0 / 20.0))
    loud = _synthetic_click(sr, gain=1.0)

    pos_first = int(0.1 * sr)
    pos_mid = int(1.0 * sr)
    pos_last = int(1.9 * sr)
    signal[pos_first:pos_first + click_len] += quiet
    signal[pos_mid:pos_mid + click_len] += loud
    signal[pos_last:pos_last + click_len] += quiet

    beat_times = np.array([0.1, 1.0, 1.9])
    cleaned = _drop_unsupported_edge_beats(beat_times, signal, sr)

    assert list(cleaned) == [0.1, 1.0, 1.9]


def test_grid_confidence_is_high_for_a_clean_click_track(tmp_path: Path):
    """BeatGrid.confidence ist die Regelmaessigkeit der Beatabstaende."""
    audio = click_track(tmp_path / "click.wav", bpm=128.0, bars=16)
    _bpm, grid = detect_bpm_and_grid(audio)
    assert grid.confidence > 0.8


def test_halftime_reading_still_scores_high_confidence(tmp_path: Path):
    """Haelt fest, was Bpm.confidence NICHT bedeutet.

    Material mit schwachen Offbeats wird von librosa in der halben
    Tempo-Oktave gelesen (87 statt 174) - mit perfekt gleichmaessigen
    Abstaenden und damit hoher Konfidenz. Das ist genau der Fall, den der
    Spec in Abschnitt 7 als "BPM halb oder doppelt erkannt" nennt.

    Der Test ist Absicht, kein Bug-Beweis: er pinnt die im Vertrag
    dokumentierte Bedeutung des Feldes (Rasterqualitaet, nicht
    Oktavensicherheit), damit Plan 2 die Oktavenfrage nicht an dieser Zahl
    entscheidet, sondern am BPM-Korridor des Profils (snap_to_range).
    """
    audio = click_track_with_weak_offbeats(
        tmp_path / "halftime.wav", bpm=174.0, bars=16, offbeat_db=-26.0
    )
    bpm, grid = detect_bpm_and_grid(audio)

    assert abs(bpm.value - 87.0) < 2.0, "Fixture liest nicht halftime"
    assert bpm.confidence > 0.8
    assert grid.confidence == bpm.confidence

    # Und so kommt der Wert wieder in die richtige Oktave: ueber den
    # Korridor aus dem Profil, nicht ueber die Konfidenz.
    corrected, was_corrected = snap_to_range(bpm.value, 170.0, 178.0)
    assert was_corrected is True
    assert abs(corrected - 174.0) < 4.0
