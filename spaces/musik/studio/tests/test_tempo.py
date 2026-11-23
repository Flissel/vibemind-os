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
