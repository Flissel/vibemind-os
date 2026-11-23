"""Fund 2 aus der Abnahme: ganze Songs haben kein gemessenes Outro.

find_mix_regions misst, wo ein Track leiser wird. Fertige Songs (Felix'
Suno-Songs) bleiben bis zum Schluss laut, also lag das Outro auf dem
Trackende und der Uebergang schrumpfte auf einen Takt. Felix 23.11.:
"Ja, so bauen" - ohne eigenes Outro wird ueber die letzten 16 Takte
geblendet, ohne eigenes Intro ueber die ersten 16.
"""

import pytest

from djset.dsp.analyze import MINDEST_MISCH_TAKTE, mischbereich
from tests.fixtures.analyses import make_off_grid

TAKT_120 = 2.0  # 4 * 60 / 120


def _auf_dem_raster(zeit: float, erster: float, takt: float) -> bool:
    takte = (zeit - erster) / takt
    return abs(takte - round(takte)) < 1e-9


def test_ein_durchgehend_lauter_song_bekommt_16_takte_an_beiden_enden():
    song = make_off_grid(
        "s", bpm=120.0, duration_s=205.7, first_downbeat_s=0.3,
        intro_end_s=0.0, outro_start_s=205.7,
    )

    bereich = mischbereich(song)

    assert MINDEST_MISCH_TAKTE == 16
    assert bereich.intro_end_s == pytest.approx(0.3 + 16 * TAKT_120)
    # Der letzte Downbeat, hinter dem noch mindestens 16 Takte Musik kommen.
    assert bereich.outro_start_s == pytest.approx(172.3)
    assert _auf_dem_raster(bereich.outro_start_s, 0.3, TAKT_120)
    assert 205.7 - bereich.outro_start_s >= 16 * TAKT_120


def test_ein_kurzes_gemessenes_outro_wird_auf_16_takte_verlaengert():
    song = make_off_grid(
        "s", bpm=120.0, duration_s=200.0,
        intro_end_s=4 * TAKT_120, outro_start_s=200.0 - 2 * TAKT_120,
    )

    bereich = mischbereich(song)

    assert bereich.intro_end_s == pytest.approx(16 * TAKT_120)
    assert bereich.outro_start_s == pytest.approx(200.0 - 16 * TAKT_120)


def test_ein_langes_eigenes_intro_und_outro_bleiben_wie_gemessen():
    song = make_off_grid(
        "s", bpm=120.0, duration_s=300.0,
        intro_end_s=40 * TAKT_120 + 0.4, outro_start_s=300.0 - 32 * TAKT_120 - 0.7,
    )

    bereich = mischbereich(song)

    assert bereich == song.mix_regions


def test_ein_kurzer_song_gibt_hoechstens_ein_drittel_an_jedes_ende():
    # 60 s bei 120 BPM sind 30 Takte - 16 Takte je Ende liessen keine Mitte.
    song = make_off_grid(
        "s", bpm=120.0, duration_s=60.0, intro_end_s=0.0, outro_start_s=60.0
    )

    bereich = mischbereich(song)

    assert bereich.intro_end_s == pytest.approx(10 * TAKT_120)
    assert bereich.outro_start_s == pytest.approx(60.0 - 10 * TAKT_120)
    assert bereich.intro_end_s < bereich.outro_start_s


def test_die_messung_selbst_bleibt_unveraendert():
    song = make_off_grid(
        "s", bpm=120.0, duration_s=200.0, intro_end_s=0.0, outro_start_s=200.0
    )

    mischbereich(song)

    assert song.mix_regions.intro_end_s == 0.0
    assert song.mix_regions.outro_start_s == 200.0
