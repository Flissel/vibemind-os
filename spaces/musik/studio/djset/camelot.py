"""Camelot-Rad: welche Tonarten nebeneinander funktionieren.

Zwoelf Zahlen im Kreis, je ein Buchstabe fuer Moll (A) und Dur (B).
Gleiche Zahl bei unterschiedlichem Buchstaben ist die Paralleltonart.
"""

import re

from djset.contracts import CAMELOT_RE, Strictness

# Dasselbe Muster wie im Vertrag, nicht ein zweites daneben: ein eigenes
# Muster driftet unbemerkt. Es war bereits so weit, dass parse("8A\n") ein
# Ergebnis lieferte, waehrend Key(camelot="8A\n") ablehnte.
#
# fullmatch statt match: Pythons re laesst `$` auch vor einem abschliessenden
# Zeilenumbruch greifen, die Rust-Engine hinter pydantic nicht. fullmatch
# verlangt, dass die ganze Zeichenkette aufgeht, und schliesst genau diese
# Luecke. Der Regressionstest dazu steht in tests/test_camelot.py.
_PATTERN = re.compile(CAMELOT_RE)
_WHEEL_SIZE = 12


class InvalidCamelot(ValueError):
    """Die Zeichenkette ist keine gueltige Camelot-Notation."""


def parse(camelot: str) -> tuple[int, str]:
    match = _PATTERN.fullmatch(camelot)
    if match is None:
        raise InvalidCamelot(f"keine gueltige Camelot-Notation: {camelot!r}")
    return int(match.group(1)), match.group(2)


def _step(number: int, offset: int) -> int:
    """Schritt auf dem Rad, 1..12 mit Umbruch."""
    return (number - 1 + offset) % _WHEEL_SIZE + 1


def neighbours(camelot: str, strictness: Strictness) -> set[str]:
    number, letter = parse(camelot)
    offsets = (-1, 0, 1) if strictness == "strict" else (-2, -1, 0, 1, 2)
    result = {f"{_step(number, offset)}{letter}" for offset in offsets}
    other_letter = "B" if letter == "A" else "A"
    result.add(f"{number}{other_letter}")
    return result


def is_compatible(a: str, b: str, strictness: Strictness) -> bool:
    parse(b)  # validiert auch die zweite Seite
    return b in neighbours(a, strictness)
