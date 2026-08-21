"""Camelot-Rad: welche Tonarten nebeneinander funktionieren.

Zwoelf Zahlen im Kreis, je ein Buchstabe fuer Moll (A) und Dur (B).
Gleiche Zahl bei unterschiedlichem Buchstaben ist die Paralleltonart.
"""

import re

from djset.contracts import Strictness

_PATTERN = re.compile(r"^(1[0-2]|[1-9])([AB])$")
_WHEEL_SIZE = 12


class InvalidCamelot(ValueError):
    """Die Zeichenkette ist keine gueltige Camelot-Notation."""


def parse(camelot: str) -> tuple[int, str]:
    match = _PATTERN.match(camelot)
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
