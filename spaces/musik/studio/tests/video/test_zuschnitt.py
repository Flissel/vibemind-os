import numpy as np
import pytest

from djset.contracts import BeatGrid
from djset.video.laenge import VideoFehler
from djset.video.zuschnitt import AUSKLANG_MIN_S, traegt_s, zuschnitt_planen

GRID = BeatGrid(first_downbeat_s=0.5, bar_length_s=2.0, confidence=0.9)
DAUER = 60.0
LEER: list[float] = []


def test_anfang_beginnt_am_ersten_taktanfang() -> None:
    z = zuschnitt_planen(GRID, DAUER, 30.0, "anfang", LEER)
    assert z.start_s == pytest.approx(0.5)
    assert z.takte == 14
    assert z.schluss_s == pytest.approx(28.5)
    assert z.ende_s == pytest.approx(30.5)


@pytest.mark.parametrize("ziel", [4.0, 7.3, 30.03, 45.0])
def test_die_laenge_ist_exakt(ziel: float) -> None:
    z = zuschnitt_planen(GRID, DAUER, ziel, "anfang", LEER)
    assert z.ende_s - z.start_s == pytest.approx(ziel, abs=1e-9)
    assert z.ende_s - z.schluss_s >= AUSKLANG_MIN_S


def test_hoehepunkt_waehlt_das_lauteste_fenster() -> None:
    energie = [0.2] * 3 + [0.9] * 2 + [0.2] * 3
    z = zuschnitt_planen(GRID, DAUER, 16.0, "hoehepunkt", energie)
    assert 24.0 <= z.start_s <= 26.0
    assert (z.start_s - 0.5) % 2.0 == pytest.approx(0.0)


def test_eingerastet_auf_erkannte_beats() -> None:
    beats = np.arange(0.5, 60.0, 0.5) + 0.03
    z = zuschnitt_planen(GRID, DAUER, 30.0, "anfang", LEER, beats)
    assert z.start_s == pytest.approx(0.53)
    assert z.schluss_s == pytest.approx(28.53)
    assert z.ende_s - z.start_s == pytest.approx(30.0, abs=1e-9)


def test_zu_weit_verschobene_beats_werden_ignoriert() -> None:
    beats = np.arange(0.5, 60.0, 0.5) + 0.3
    z = zuschnitt_planen(GRID, DAUER, 30.0, "anfang", LEER, beats)
    assert z.start_s == pytest.approx(0.5)
    assert z.schluss_s == pytest.approx(28.5)


def test_einrasten_haelt_den_ausklang_ein() -> None:
    # Ziel 4.5: Raster schluss=4.5, ende=5.0 (Ausklang genau 0.5). Ein Beat bei 4.62
    # zieht den Schluss nach hinten, der Ausklang fiele auf 0.38 s: ein Takt weniger.
    beats = np.array([0.5, 4.62])
    z = zuschnitt_planen(GRID, DAUER, 4.5, "anfang", LEER, beats)
    assert z.takte == 1
    assert z.ende_s - z.schluss_s >= AUSKLANG_MIN_S
    assert z.ende_s - z.start_s == pytest.approx(4.5, abs=1e-9)


def test_zu_kurzes_ziel() -> None:
    with pytest.raises(VideoFehler, match=r"mindestens 2\.5 s"):
        zuschnitt_planen(GRID, DAUER, 2.0, "anfang", LEER)


def test_ziel_laenger_als_der_song() -> None:
    assert traegt_s(GRID, DAUER) == pytest.approx(59.5)
    with pytest.raises(VideoFehler, match=r"59\.5"):
        zuschnitt_planen(GRID, DAUER, 70.0, "anfang", LEER)

