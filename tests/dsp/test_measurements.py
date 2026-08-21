from datetime import date
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import BeatGrid, TrackMeasurements
from djset.dsp.analyze import (
    energy_curve, find_mix_regions, measure_loudness, measure_track,
)
from djset.run import Run
from tests.fixtures.make_audio import SR, click_track, near_silent_click_track


def _fade_in_track(path: Path, seconds: float = 20.0) -> Path:
    """Rauschen, das leise beginnt, laut wird und leise endet."""
    t = np.arange(int(seconds * SR)) / SR
    rng = np.random.default_rng(seed=7)
    noise = rng.standard_normal(t.size).astype(np.float32)
    envelope = np.sin(np.pi * t / seconds).astype(np.float32)
    signal = noise * envelope
    # Auf sichere Aussteuerung normieren: Gauss-Rauschen hat unbeschraenkte
    # Spitzen, ohne Normierung waere der True Peak zufaellig positiv.
    signal = signal / (float(np.max(np.abs(signal))) * 1.2)
    sf.write(path, signal.astype(np.float32), SR)
    return path


def test_energy_curve_is_normalised(tmp_path: Path):
    audio = _fade_in_track(tmp_path / "fade.wav")
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0, confidence=1.0)
    curve = energy_curve(audio, grid)
    assert len(curve) >= 2
    assert min(curve) >= 0.0
    assert max(curve) <= 1.0


def test_energy_curve_rises_then_falls(tmp_path: Path):
    audio = _fade_in_track(tmp_path / "fade.wav")
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0, confidence=1.0)
    curve = energy_curve(audio, grid)
    peak = curve.index(max(curve))
    assert 0 < peak < len(curve) - 1


def test_mix_regions_exclude_quiet_head_and_tail():
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0, confidence=1.0)
    curve = [0.05, 0.1, 0.9, 1.0, 0.9, 0.1, 0.05]
    regions = find_mix_regions(curve, grid, duration_s=56.0)
    assert regions.intro_end_s > 0.0
    assert regions.outro_start_s < 56.0
    assert regions.intro_end_s < regions.outro_start_s


def test_loudness_returns_negative_lufs(tmp_path: Path):
    audio = _fade_in_track(tmp_path / "fade.wav")
    loudness = measure_loudness(audio)
    assert loudness.lufs_integrated < 0.0
    assert loudness.true_peak <= 0.5


def test_measure_track_fills_every_field(tmp_path: Path):
    audio = click_track(tmp_path / "click.wav", bpm=128.0, bars=32)
    measurements = measure_track(audio, track_id="t1")
    assert measurements.id == "t1"
    assert measurements.duration_s > 0
    assert abs(measurements.bpm.value - 128.0) < 1.0
    assert len(measurements.energy) >= 1


def test_near_silent_track_survives_the_run_roundtrip(tmp_path: Path):
    """Regression: ein (fast) stiller Track darf `build --resume` nicht sprengen.

    pyloudnorm liefert fuer Material unter dem absoluten Gate von -70 LUFS
    -inf. model_dump_json() schrieb daraus `null`, model_validate_json()
    lehnte das ab - genau der Pfad, den `build --resume` beim Wiedereinlesen
    von tracks/<id>.json geht. Der Spec verlangt, dass eine einzelne kaputte
    Datei den Lauf weiterlaufen laesst; ein Absturz beim Zurueckladen ist das
    Gegenteil davon.
    """
    audio = near_silent_click_track(tmp_path / "quiet.wav", bpm=128.0, bars=8)
    measurements = measure_track(audio, track_id="quiet")

    # Der Bodenwert steht anstelle von -inf und ist damit JSON-faehig.
    assert measurements.loudness.lufs_integrated == -120.0

    run = Run.create(tmp_path, "silence", today=date(2026, 8, 5))
    run.write_model("tracks/quiet.json", measurements)
    restored = run.read_model("tracks/quiet.json", TrackMeasurements)

    assert restored == measurements
