from pathlib import Path

from djset.dsp.analyze import detect_bpm_and_grid, snap_to_range
from tests.fixtures.make_audio import click_track


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
