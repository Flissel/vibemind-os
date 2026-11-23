"""Zuschnitt der Videomusik: Start und Schluss auf Taktanfaengen, Laenge exakt.

Die Musik soll nicht mitten im Takt abreissen. Deshalb liegt der letzte
Taktanfang (`schluss_s`) so, dass danach noch ein kurzer Ausklang bis zum
Videoende bleibt; die Gesamtlaenge `ende_s - start_s` ist exakt das Ziel.
"""

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from djset.contracts import BeatGrid
from djset.video.laenge import VideoFehler

AUSKLANG_MIN_S = 0.5
# Hoechstens so viele Schlaege (Viertel eines Takts) wird ein Taktanfang auf
# einen erkannten Beat geschoben; weiter weg traut man dem Beat nicht mehr.
EINRASTEN_SCHLAEGE = 0.25
_EPS = 1e-9

Einstieg = Literal["anfang", "hoehepunkt"]


@dataclass(frozen=True)
class Zuschnitt:
    start_s: float
    schluss_s: float
    ende_s: float
    takte: int


def traegt_s(grid: BeatGrid, dauer_s: float) -> float:
    """Die laengste Videomusik, die der Song ab dem ersten Taktanfang traegt."""
    return max(0.0, dauer_s - grid.first_downbeat_s)


def _einrasten(t: float, beats: np.ndarray | None, toleranz_s: float) -> float:
    if beats is None or len(beats) == 0:
        return t
    abstaende = np.abs(beats - t)
    i = int(np.argmin(abstaende))
    return float(beats[i]) if abstaende[i] < toleranz_s else t


def _fenster_energie(energie: list[float], schritt_s: float, start: float, laenge: float) -> float:
    """Zeitgewichtete mittlere Energie von [start, start+laenge)."""
    ende = start + laenge
    summe = 0.0
    for i, wert in enumerate(energie):
        von = max(start, i * schritt_s)
        bis = min(ende, (i + 1) * schritt_s)
        if bis > von:
            summe += wert * (bis - von)
    return summe / laenge


def zuschnitt_planen(
    grid: BeatGrid,
    dauer_s: float,
    ziel_s: float,
    einstieg: Einstieg,
    energie: list[float],
    beats: np.ndarray | None = None,
) -> Zuschnitt:
    takt = grid.bar_length_s
    mindest_s = takt + AUSKLANG_MIN_S
    if ziel_s < mindest_s - _EPS:
        raise VideoFehler(f"Das Ziel von {ziel_s:.1f} s ist zu kurz: mindestens {mindest_s:.1f} s")
    tragbar = traegt_s(grid, dauer_s)
    if ziel_s > tragbar + _EPS:
        raise VideoFehler(
            f"Das Ziel von {ziel_s:.1f} s ist laenger als der Song traegt ({tragbar:.1f} s)"
        )

    starts: list[float] = []
    k = 0
    while grid.first_downbeat_s + k * takt + ziel_s <= dauer_s + _EPS:
        starts.append(grid.first_downbeat_s + k * takt)
        k += 1

    if einstieg == "anfang":
        start = starts[0]
    else:
        # max() liefert bei Gleichstand das erste Element: der fruehere Start.
        start = max(starts, key=lambda s: _fenster_energie(energie, 4 * takt, s, ziel_s))

    toleranz_s = EINRASTEN_SCHLAEGE * takt / 4
    eingerastet = _einrasten(start, beats, toleranz_s)
    if eingerastet + ziel_s <= dauer_s + _EPS:  # nie ueber das Songende hinaus
        start = eingerastet
    ende = start + ziel_s

    takte = math.floor((ziel_s - AUSKLANG_MIN_S) / takt + _EPS)
    while takte >= 1:
        schluss = _einrasten(start + takte * takt, beats, toleranz_s)
        if ende - schluss >= AUSKLANG_MIN_S - _EPS:
            return Zuschnitt(start_s=start, schluss_s=schluss, ende_s=ende, takte=takte)
        takte -= 1
    raise VideoFehler(f"Das Ziel von {ziel_s:.1f} s ist zu kurz: mindestens {mindest_s:.1f} s")
