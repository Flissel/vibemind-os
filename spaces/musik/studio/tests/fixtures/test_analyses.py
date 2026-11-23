from djset.camelot import is_compatible
from djset.contracts import TrackAnalysis
from tests.fixtures.analyses import make_analysis, many, wheel_walk


def test_a_made_analysis_is_a_valid_contract() -> None:
    analysis = make_analysis("t1")

    assert isinstance(analysis, TrackAnalysis)
    assert analysis.id == "t1"
    assert analysis.verdict.usable is True


def test_the_wheel_walk_stays_harmonically_adjacent() -> None:
    keys = wheel_walk(8)

    assert len(keys) == 8
    for first, second in zip(keys, keys[1:]):
        assert is_compatible(first, second, "strict")


def test_the_wheel_walk_stays_adjacent_across_the_wrap() -> None:
    """Der Uebergang vom letzten Eintrag zurueck zum ersten muss auch passen."""
    keys = wheel_walk(30)

    assert len(keys) == 30
    for first, second in zip(keys, keys[1:]):
        assert is_compatible(first, second, "strict"), f"{first} -> {second}"


def test_many_gives_distinct_ids_and_varied_energy() -> None:
    tracks = many(6)

    assert len({t.id for t in tracks}) == 6
    assert len({t.verdict.energy_level for t in tracks}) > 1
