import itertools
import random

import pytest
from pydantic import ValidationError

from djset.camelot import (
    EXACT_LIMIT,
    InvalidCamelot,
    break_indices,
    is_compatible,
    min_key_breaks,
    neighbours,
    parse,
)
from djset.contracts import Key
from tests.fixtures.analyses import wheel_walk


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


# --- A1: Mindestzahl an Tonart-Bruechen -----------------------------------

def _brute_force(keys: list[str], strictness: str) -> int:
    """Alle Reihenfolgen durchprobieren - nur fuer kleine Listen."""
    return min(
        len(break_indices(list(order), strictness))
        for order in itertools.permutations(keys)
    )


def test_ohne_bruch_wenn_eine_vertraegliche_reihenfolge_existiert():
    # 8A-9B passt nicht, aber 8A-8B-9B schon.
    assert min_key_breaks(["8A", "9B", "8B"], "strict") == 0


def test_ein_isolierter_song_kostet_einen_bruch():
    assert min_key_breaks(["8A", "8B", "2A"], "strict") == 1


def test_drei_inseln_kosten_zwei_brueche():
    assert min_key_breaks(["1A", "5A", "9A"], "strict") == 2


def test_die_strenge_entscheidet_mit():
    # Zwei Positionen auf dem Rad: loose ja, strict nein.
    assert min_key_breaks(["8A", "10A", "12A"], "loose") == 0
    assert min_key_breaks(["8A", "10A", "12A"], "strict") == 2


def test_null_oder_ein_song_hat_keinen_bruch():
    assert min_key_breaks([], "strict") == 0
    assert min_key_breaks(["8A"], "strict") == 0


def test_break_indices_nennt_die_naht():
    assert break_indices(["8A", "8B", "2A", "2B"], "strict") == [1]


def test_exakt_wie_alle_reihenfolgen_durchprobiert():
    zufall = random.Random(20261123)
    rad = [f"{n}{l}" for n in range(1, 13) for l in "AB"]
    for _ in range(150):
        keys = [zufall.choice(rad) for _ in range(zufall.randint(2, 6))]
        for strictness in ("strict", "loose"):
            assert min_key_breaks(keys, strictness) == _brute_force(keys, strictness), keys


def test_zwoelf_songs_werden_noch_exakt_gerechnet():
    """An der Grenze: zwei Inseln (8A/8B/9A... und 2A/2B/3A...) zu je sechs."""
    insel_1 = ["8A", "8B", "9B", "9A", "10A", "10B"]
    insel_2 = ["2A", "2B", "3B", "3A", "4A", "4B"]
    assert EXACT_LIMIT == 12
    assert min_key_breaks(insel_1 + insel_2, "strict") == 1


def test_ueber_zwoelf_findet_die_naeherung_einen_rundgang_ohne_bruch():
    keys = wheel_walk(16)
    zufall = random.Random(7)
    zufall.shuffle(keys)
    assert min_key_breaks(keys, "strict") == 0


def test_ueber_zwoelf_ist_die_naeherung_nie_zu_optimistisch():
    """Ueber EXACT_LIMIT rechnet die gierige Naeherung - sie darf ueberschaetzen,
    aber nie weniger Brueche nennen, als eine echte Reihenfolge braucht. Zwei
    Inseln kosten mindestens einen Bruch."""
    insel_1 = ["8A", "8B", "9B", "9A", "10A", "10B", "11B"]
    insel_2 = ["2A", "2B", "3B", "3A", "4A", "4B", "5B"]
    assert min_key_breaks(insel_1 + insel_2, "strict") == 1
