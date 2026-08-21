import pytest

from djset.camelot import InvalidCamelot, is_compatible, neighbours, parse


def test_parse_splits_number_and_letter():
    assert parse("8A") == (8, "A")
    assert parse("12B") == (12, "B")


def test_parse_rejects_garbage():
    with pytest.raises(InvalidCamelot):
        parse("13A")
    with pytest.raises(InvalidCamelot):
        parse("8C")


def test_strict_neighbours_are_wheel_step_and_mode_switch():
    assert neighbours("8A", "strict") == {"8A", "7A", "9A", "8B"}


def test_wheel_wraps_at_twelve():
    assert neighbours("12A", "strict") == {"12A", "11A", "1A", "12B"}
    assert neighbours("1A", "strict") == {"1A", "12A", "2A", "1B"}


def test_loose_adds_two_steps():
    assert neighbours("8A", "loose") == {"8A", "6A", "7A", "9A", "10A", "8B"}


def test_is_compatible_uses_strictness():
    assert is_compatible("8A", "10A", "loose") is True
    assert is_compatible("8A", "10A", "strict") is False
