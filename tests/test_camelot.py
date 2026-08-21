import pytest
from pydantic import ValidationError

from djset.camelot import InvalidCamelot, is_compatible, neighbours, parse
from djset.contracts import Key


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


# Fund 8: djset.camelot hatte das Camelot-Muster ein zweites Mal definiert,
# und die beiden waren bereits auseinandergelaufen - parse("8A\n") lieferte
# ein Ergebnis, Key(camelot="8A\n") lehnte ab. Ursache: Pythons re laesst `$`
# auch vor einem abschliessenden Zeilenumbruch greifen, die Rust-Engine hinter
# pydantic nicht. Dieser Test haelt beide Seiten aneinander fest.
@pytest.mark.parametrize(
    "candidate",
    ["8A", "12B", "1A", "12A", "13A", "0A", "8C", "8", "A8", "", "8a",
     " 8A", "8A ", "8A\n", "\n8A", "8AB", "08A"],
)
def test_parse_and_the_contract_accept_exactly_the_same_strings(candidate: str):
    try:
        parse(candidate)
        parser_accepts = True
    except InvalidCamelot:
        parser_accepts = False

    try:
        Key(camelot=candidate, confidence=0.5)
        contract_accepts = True
    except ValidationError:
        contract_accepts = False

    assert parser_accepts == contract_accepts, candidate


def test_parse_rejects_a_trailing_newline():
    """Der konkrete Fall, an dem die beiden Muster auseinanderliefen."""
    with pytest.raises(InvalidCamelot):
        parse("8A\n")
