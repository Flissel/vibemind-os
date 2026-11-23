import random

import pytest

from djset.contracts import MAX_RATIO, MIN_RATIO
from djset.tempo import TempoWahl, waehle_tempo


def test_waehle_tempo_nimmt_die_groesste_gruppe():
    wahl = waehle_tempo({"a": 124.0, "b": 126.0, "c": 128.0, "d": 174.0, "e": 90.0})
    assert sorted(wahl.drin) == ["a", "b", "c"]
    assert wahl.draussen == {"d": 174.0, "e": 90.0}
    assert all(
        MIN_RATIO <= wahl.bereich.target / t <= MAX_RATIO for t in (124.0, 126.0, 128.0)
    )
    assert (wahl.bereich.min, wahl.bereich.max) == (124.0, 128.0)


def test_waehle_tempo_ein_song():
    wahl = waehle_tempo({"a": 100.0})
    assert wahl.drin == ["a"]
    assert wahl.draussen == {}
    assert (wahl.bereich.target, wahl.bereich.min, wahl.bereich.max) == (
        100.0,
        100.0,
        100.0,
    )


def test_bei_gleicher_anzahl_gewinnt_die_kleinste_abweichung():
    """104 und 102 nehmen beide alle drei Songs auf - 104 weicht in Summe um
    8 BPM ab, 102 um 10. Gewinnen muss 104."""
    wahl = waehle_tempo({"a": 100.0, "b": 104.0, "c": 108.0})
    assert sorted(wahl.drin) == ["a", "b", "c"]
    assert wahl.bereich.target == 104.0


@pytest.mark.parametrize(
    "tempi",
    [
        {"a": 94.0, "b": 106.0},
        {"a": 120.0, "b": 121.5, "c": 140.0, "d": 141.0, "e": 142.0},
        {"a": 170.0, "b": 172.0, "c": 174.0, "d": 176.0, "e": 87.0},
        {"a": 60.0, "b": 200.0},
    ],
)
def test_jeder_song_im_bereich_ist_streckbar(tempi: dict[str, float]):
    wahl = waehle_tempo(tempi)
    assert set(wahl.drin) | set(wahl.draussen) == set(tempi)
    assert not set(wahl.drin) & set(wahl.draussen)
    for song_id in wahl.drin:
        assert MIN_RATIO <= wahl.bereich.target / tempi[song_id] <= MAX_RATIO
    for song_id, tempo in wahl.draussen.items():
        assert tempo == tempi[song_id]
        assert not MIN_RATIO <= wahl.bereich.target / tempo <= MAX_RATIO
    assert wahl.bereich.min == min(tempi[i] for i in wahl.drin)
    assert wahl.bereich.max == max(tempi[i] for i in wahl.drin)


def test_ohne_tempi_gibt_es_keine_wahl():
    with pytest.raises(ValueError):
        waehle_tempo({})


def test_die_wahl_uebersteht_den_json_weg():
    wahl = waehle_tempo({"a": 124.0, "b": 126.0, "c": 174.0})
    assert TempoWahl.model_validate_json(wahl.model_dump_json()) == wahl


# --- A2: Grenzkandidaten und Rundungsfalle ----------------------------------


def test_100_und_112_5_passen_gemeinsam():
    """Gemeinsam geht es nur zwischen 105,75 (= 112,5 * 0,94) und 106
    (= 100 * 1,06) - weder ein gemessenes Tempo noch die Mitte 106,25 liegt
    dort. Erst die Grenzkandidaten finden den Bereich."""
    tempi = {"a": 100.0, "b": 112.5}

    wahl = waehle_tempo(tempi)

    assert sorted(wahl.drin) == ["a", "b"]
    for tempo in tempi.values():
        assert MIN_RATIO <= wahl.bereich.target / tempo <= MAX_RATIO


def test_eigenschaft_kein_feineres_ziel_nimmt_mehr_songs_auf():
    zufall = random.Random(20261123)
    for _ in range(60):
        anzahl = zufall.randint(1, 9)
        tempi = {
            f"s{i}": round(zufall.uniform(80.0, 180.0), zufall.choice((0, 1, 2)))
            for i in range(anzahl)
        }
        wahl = waehle_tempo(tempi)
        ziel = wahl.bereich.target
        # Jeder Song in `drin` besteht die Division exakt - ohne Toleranz.
        for song_id in wahl.drin:
            assert MIN_RATIO <= ziel / tempi[song_id] <= MAX_RATIO, (tempi, ziel)
        for song_id in wahl.draussen:
            assert not MIN_RATIO <= ziel / tempi[song_id] <= MAX_RATIO
        # Kein Ziel aus einem feinen Raster nimmt mehr Songs auf.
        unten = int(min(tempi.values()) * 0.93 * 100)
        oben = int(max(tempi.values()) * 1.07 * 100) + 1
        raster = [schritt / 100 for schritt in range(unten, oben)]
        bestes = max(
            sum(1 for t in tempi.values() if MIN_RATIO <= z / t <= MAX_RATIO)
            for z in raster
        )
        assert len(wahl.drin) >= bestes, (tempi, wahl)


# --- B10: halb oder doppelt gemessen ----------------------------------------


def test_halbes_oder_doppeltes_tempo_wird_genannt_nicht_korrigiert():
    tempi = {"a": 124.0, "b": 126.0, "c": 128.0, "h": 63.0, "d": 252.0, "x": 90.0}

    wahl = waehle_tempo(tempi)

    assert sorted(wahl.drin) == ["a", "b", "c"]
    # Keine Korrektur: beide bleiben draussen, mit ihrem gemessenen Tempo.
    assert wahl.draussen == {"h": 63.0, "d": 252.0, "x": 90.0}
    assert wahl.oktave == {"h": "halb", "d": "doppelt"}


def test_ein_altes_tempo_json_ohne_oktave_bleibt_lesbar():
    alt = (
        '{"bereich": {"target": 126.0, "min": 124.0, "max": 128.0}, '
        '"drin": ["a"], "draussen": {}}'
    )
    assert TempoWahl.model_validate_json(alt).oktave == {}
