"""Fund 3 der Abnahme: ein Song hat nicht ueberall dasselbe Tempo.

farewell-kiss lief im Intro bei 93.0, im Outro bei 95.1, im Mittel 94.06 BPM.
Das globale Raster ist eine einzige Gerade ab dem ersten Beat. Im
Mischbereich lagen die echten Beats daneben, und mit dem Gesamttempo
gestreckt liefen zwei Spuren ueber 16 Takte um 0,7 Schlaege auseinander
(Felix 23.11.: "Stolpert hoerbar"). lokales_raster legt eine eigene Gerade
nur durch die Beats eines Mischbereichs.
"""

import numpy as np
import pytest

from djset.contracts import BeatGrid
from djset.dsp.analyze import MINDEST_LOKALE_BEATS, lokales_raster

GLOBAL = BeatGrid(first_downbeat_s=0.0, bar_length_s=4 * 60 / 94.06, confidence=0.8)


def _beats(bpm: float, von: float, anzahl: int) -> np.ndarray:
    return von + np.arange(anzahl) * 60.0 / bpm


def test_das_lokale_tempo_kommt_aus_dem_bereich_nicht_aus_dem_ganzen_song():
    beats = _beats(93.04, 0.2, 64)

    raster = lokales_raster(beats, 0.0, 45.0, GLOBAL)

    assert raster is not None
    assert 4 * 60 / raster.bar_length_s == pytest.approx(93.04, abs=0.01)
    assert raster.bpm == pytest.approx(93.04, abs=0.01)
    assert raster.streuung_schlaege < 0.01
    assert raster.beats == 64


def test_der_lokale_downbeat_ist_ein_echter_beat_nahe_einem_globalen_downbeat():
    # Die Beats im Outro liegen 0,45 Schlaege neben dem globalen Raster
    # (smaragd-green-eyes). Ein Einsatz auf dem globalen Raster faellt
    # zwischen zwei Schlaege.
    schlag = GLOBAL.bar_length_s / 4
    von = 40 * GLOBAL.bar_length_s + 0.45 * schlag
    beats = _beats(94.06, von, 64)

    raster = lokales_raster(beats, von - 1.0, von + 50.0, GLOBAL)

    assert raster is not None
    # Auf einem echten Beat ...
    assert np.min(np.abs(beats - raster.downbeat_s)) < 1e-6
    # ... und dem globalen Takt nach ein Taktanfang, keine Zaehlzeit 2 bis 4.
    takte = (raster.downbeat_s - GLOBAL.first_downbeat_s) / GLOBAL.bar_length_s
    assert abs(takte - round(takte)) < 0.2


def test_ein_fehlender_beat_verschiebt_das_tempo_nicht():
    beats = np.delete(_beats(93.04, 0.2, 64), 30)

    raster = lokales_raster(beats, 0.0, 45.0, GLOBAL)

    assert raster is not None
    assert raster.bpm == pytest.approx(93.04, abs=0.01)


def test_zu_wenige_beats_ergeben_kein_lokales_raster():
    beats = _beats(93.04, 0.2, MINDEST_LOKALE_BEATS - 1)

    assert lokales_raster(beats, 0.0, 45.0, GLOBAL) is None


def test_measure_track_speichert_die_lokalen_raster(tmp_path):
    from djset.dsp.analyze import measure_track
    from tests.fixtures.make_audio import click_track

    pfad = click_track(tmp_path / "c.wav", bpm=126.0, bars=24)

    messung = measure_track(pfad, "c")

    assert messung.lokale_raster is not None
    for raster in (messung.lokale_raster.intro, messung.lokale_raster.outro):
        assert raster is not None
        assert raster.bpm == pytest.approx(126.0, abs=0.5)
        assert raster.streuung_schlaege < 0.05


def test_unruhige_beats_zeigen_sich_in_der_streuung():
    rng = np.random.default_rng(7)
    schlag = 60.0 / 95.0
    beats = _beats(95.0, 100.0, 64) + rng.uniform(-0.3, 0.3, 64) * schlag

    raster = lokales_raster(np.sort(beats), 99.0, 145.0, GLOBAL)

    assert raster is not None
    assert raster.streuung_schlaege > 0.1
