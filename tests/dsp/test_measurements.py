from datetime import date
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.contracts import BeatGrid, TrackMeasurements
from djset.dsp.analyze import (
    energy_curve, find_mix_regions, measure_loudness, measure_track,
)
from djset.run import Run
from tests.fixtures.make_audio import (
    SR, click_track, graded_noise_track, near_silent_click_track,
)


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


def test_energy_curve_stays_within_bounds(tmp_path: Path):
    """0..1 gilt weiterhin - aber das Maximum ist nicht mehr zwangslaeufig 1.0.

    Frueher hiess dieser Test "is_normalised": jede Kurve wurde auf ihren
    eigenen Spitzenwert normiert und erreichte damit immer exakt 1.0. Die
    Grenzen selbst sind geblieben, ihr Zustandekommen nicht (siehe
    energy_curve und test_energy_curve_uses_a_fixed_reference_not_the_track_peak).
    """
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
    """Prueft die Arithmetik an einer handgeschriebenen Kurve.

    Achtung beim Lesen: diese Form (Spitzenwert exakt 1.0) erzeugt
    energy_curve seit der festen dB-Referenz nicht mehr. Der Test bewacht
    also die Rechnung, nicht das Verhalten an echtem Material - dafuer ist
    test_mix_regions_follow_a_graded_track da.
    """
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


def _steady_noise(path: Path, rms_db: float, seconds: float = 20.0) -> Path:
    """Rauschen mit exakt eingestelltem RMS-Pegel in dBFS.

    Damit laesst sich pruefen, dass die Energiekurve gegen eine feste
    Referenz misst und nicht gegen den Spitzenwert des jeweiligen Tracks.
    """
    rng = np.random.default_rng(seed=3)
    signal = rng.standard_normal(int(seconds * SR)).astype(np.float32)
    current = float(np.sqrt(np.mean(signal.astype(np.float64) ** 2)))
    signal = signal / current * (10.0 ** (rms_db / 20.0))
    sf.write(path, signal.astype(np.float32), SR)
    return path


def test_energy_curve_uses_a_fixed_reference_not_the_track_peak(tmp_path: Path):
    """Zwei verschieden laute Tracks muessen verschiedene Energien ergeben.

    Regression fuer die Normierung je Track: die machte aus jedem Track eine
    Kurve mit Maximum exakt 1.0, womit ein leiser Ambient-Opener und ein
    Peaktime-Roller ununterscheidbar wurden. Stufe 5 legt die Tracks aber auf
    profile.energy_arc mit absoluten Zielenergien, und die
    Aehnlichkeitsregel des Spec vergleicht die Energie zweier Tracks
    miteinander - beides braucht eine trackuebergreifende Skala.
    """
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0, confidence=1.0)
    loud = energy_curve(_steady_noise(tmp_path / "loud.wav", -12.0), grid)
    quiet = energy_curve(_steady_noise(tmp_path / "quiet.wav", -36.0), grid)

    assert max(loud) > max(quiet) + 0.3

    # -60 dBFS -> 0.0, 0 dBFS -> 1.0, dazwischen linear.
    assert abs(max(loud) - 0.8) < 0.02
    assert abs(max(quiet) - 0.4) < 0.02


def test_energy_curve_clamps_below_the_reference_floor(tmp_path: Path):
    """Unterhalb von -60 dBFS bleibt es bei 0.0 statt negativ zu werden."""
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0, confidence=1.0)
    curve = energy_curve(_steady_noise(tmp_path / "inaudible.wav", -70.0), grid)
    assert curve
    assert max(curve) == 0.0


def test_mix_regions_of_a_uniform_track_span_the_whole_file():
    """Ohne erkennbaren Auf- und Abbau ist der ganze Track mischbar.

    Der Spec nennt diesen Fall ausdruecklich. Frueher stand dafuer ein
    eigener Zweig im Code, der fuer eine Kurve mit Werten >= 0 nie erreicht
    wurde - der Normalweg liefert dasselbe Ergebnis, jetzt getestet statt
    behauptet.
    """
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0, confidence=1.0)
    regions = find_mix_regions([0.5] * 6, grid, duration_s=48.0)
    assert regions.intro_end_s == 0.0
    assert regions.outro_start_s == 48.0


def test_mix_regions_of_a_silent_track_do_not_crash():
    """Die Nullkurve ist der Grenzfall der gleichfoermigen Kurve."""
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0, confidence=1.0)
    regions = find_mix_regions([0.0] * 6, grid, duration_s=48.0)
    assert regions.intro_end_s == 0.0
    assert regions.outro_start_s == 48.0


@pytest.mark.parametrize(
    "quiet_db, body_db",
    [(-20.0, -10.0), (-30.0, -8.0), (-45.0, -8.0)],
)
def test_mix_regions_follow_a_graded_track(
    tmp_path: Path, quiet_db: float, body_db: float
):
    """Der Regionentest, der seine Kurve aus energy_curve bezieht.

    Regression fuer die Schwelle in find_mix_regions: sie war als Faktor auf
    eine peak-normierte, LINEARE Kurve geschrieben (dort exakt 6 dB unter
    Peak). Auf der jetzt dB-linearen Kurve bedeutete derselbe Faktor 25 dB
    und mehr unter Peak - ein Intro 10 bis 22 dB unter dem Koerper, bei
    Suno-Material der Normalfall, verschwand vollstaendig und der ganze Track
    galt als mischbar. Die handgeschriebene Kurve in
    test_mix_regions_exclude_quiet_head_and_tail konnte das nicht fangen: sie
    hat eine Form, die energy_curve gar nicht mehr erzeugt.

    Deshalb kommt das Material hier durch die echte Kette - Datei ->
    energy_curve -> find_mix_regions - und der Kontrast ist mit 10, 22 und
    37 dB so gestuft, dass er den Bereich abdeckt, in dem der Fehler auftrat.
    """
    grid = BeatGrid(first_downbeat_s=0.0, bar_length_s=2.0, confidence=1.0)
    # Schritt = 4 Takte a 2 s = 8 s. 16 s Intro, 24 s Koerper, 8 s Outro.
    audio = graded_noise_track(
        tmp_path / "graded.wav",
        [(16.0, quiet_db), (24.0, body_db), (8.0, quiet_db)],
    )

    curve = energy_curve(audio, grid)
    regions = find_mix_regions(curve, grid, duration_s=48.0)

    # Der Kern: der Track ist NICHT von vorn bis hinten "im Groove".
    assert regions.intro_end_s > 0.0
    assert regions.outro_start_s < 48.0
    # Und die Grenzen liegen auf den echten Uebergaengen.
    assert regions.intro_end_s == 16.0
    assert regions.outro_start_s == 40.0
