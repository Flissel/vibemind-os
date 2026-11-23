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


# Bis zu so vielen Tracks wird die Mindestzahl an Bruechen exakt gerechnet
# (dynamische Programmierung ueber Teilmengen, 2^n * n^2 Schritte - bei zwoelf
# Tracks rund 600 000, also Sekundenbruchteile). Darueber rechnet eine gierige
# Naeherung, die ueberschaetzen, aber nie unterschaetzen kann.
EXACT_LIMIT = 12


def break_indices(keys: list[str], strictness: Strictness) -> list[int]:
    """Die Nahtstellen i, an denen keys[i] und keys[i+1] nicht zusammenpassen."""
    return [
        index
        for index, (a, b) in enumerate(zip(keys, keys[1:]))
        if not is_compatible(a, b, strictness)
    ]


def _exact_min_breaks(fits: list[list[bool]]) -> int:
    """Kuerzester Weg durch alle Tracks, ein Bruch kostet eins (Held-Karp)."""
    count = len(fits)
    full = 1 << count
    unreachable = count  # mehr Brueche als Naehte gibt es nicht
    best = [[unreachable] * count for _ in range(full)]
    for start in range(count):
        best[1 << start][start] = 0
    for mask in range(1, full):
        row = best[mask]
        for last in range(count):
            cost = row[last]
            if cost == unreachable:
                continue
            last_fits = fits[last]
            for following in range(count):
                bit = 1 << following
                if mask & bit:
                    continue
                total = cost if last_fits[following] else cost + 1
                target = best[mask | bit]
                if total < target[following]:
                    target[following] = total
    return min(best[full - 1])


def _greedy_min_breaks(fits: list[list[bool]]) -> int:
    """Gierige Naeherung: von jedem Start aus immer zum passenden Nachbarn mit
    den wenigsten noch freien passenden Nachbarn (Warnsdorff); gibt es keinen,
    kostet der naechste Schritt einen Bruch. Das Ergebnis ist die Zahl einer
    tatsaechlich gefundenen Reihenfolge, also nie zu optimistisch."""
    count = len(fits)
    best = count
    for start in range(count):
        free = set(range(count)) - {start}
        last = start
        breaks = 0
        while free:

            def open_neighbours(track: int) -> int:
                return sum(1 for other in free if other != track and fits[track][other])

            fitting = [track for track in free if fits[last][track]]
            if not fitting:
                breaks += 1
                fitting = list(free)
            last = min(fitting, key=lambda track: (open_neighbours(track), track))
            free.remove(last)
        best = min(best, breaks)
    return best


def min_key_breaks(keys: list[str], strictness: Strictness) -> int:
    """Wie viele Tonart-Brueche eine Reihenfolge dieser Tonarten mindestens hat.

    0 heisst: es gibt eine Reihenfolge, in der je zwei Nachbarn harmonisch
    zusammenpassen. Exakt bis EXACT_LIMIT Tracks, darueber eine gierige
    Naeherung (eine obere Schranke). Reine Rechnung, kein Modellurteil.
    """
    if len(keys) < 2:
        return 0
    fits = [[is_compatible(a, b, strictness) for b in keys] for a in keys]
    if len(keys) <= EXACT_LIMIT:
        return _exact_min_breaks(fits)
    return _greedy_min_breaks(fits)
