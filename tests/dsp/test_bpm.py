from pathlib import Path

from djset.dsp.analyze import detect_bpm_and_grid, snap_to_range
from tests.fixtures.make_audio import click_track, click_track_with_lead_in


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
