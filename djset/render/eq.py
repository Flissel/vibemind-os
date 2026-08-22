"""Bandzerlegung und Verstaerkungshuellkurven fuer die Uebergaenge.

Bewusst in numpy statt in einem ffmpeg-Filtergraph. Drei Gruende: ein
Filtergraph fuer dreizehn Tracks mit je drei Baendern und Huellkurven waere
unlesbar und praktisch nicht zu debuggen; ffmpegs equalizer faehrt Verstaerkung
ueber sendcmd nur stufig, waehrend eine EqCurve stetige Stuetzpunkte
beschreibt; und ein Renderer, der fuer jeden Test einen Unterprozess startet,
ist weder schnell noch gut pruefbar. Das hier ist Arithmetik ueber Samples.

Die Zerlegung rekonstruiert exakt: mid ist definiert als das, was nach Abzug
von low und high uebrigbleibt. Damit liefert eine flache Kurve das
Eingangssignal unveraendert zurueck, und genau das ist der Test, auf dem die
ganze Stufe ruht.

sosfiltfilt braucht mehr Samples als seine Randlaenge (padlen): gemessen bei
Ordnung 4 sind das 16 Samples, sowohl fuer den Tiefpass- als auch den
Hochpass-Zweig (padlen = 15, siehe _MIN_FILTER_SAMPLES). Ein handgeschriebener
MixPlan kann ein kuerzeres Fenster erzeugen - MixEntry.play_from_s/play_to_s
erzwingen nur play_from_s < play_to_s, keine Mindestdauer, und Transition.bars
schuetzt hier nicht, weil der Renderer das Fenster zusaetzlich mit
block.size deckelt. apply_eq faengt das ab, statt mit scipys internem
ValueError abzubrechen: siehe _MIN_FILTER_SAMPLES unten.
"""

import numpy as np
from scipy import signal as sig

from djset.contracts import EqCurve, EqPoint

# Trennfrequenzen wie an einem DJ-Mixer: der Bass unter 200 Hz, die Hoehen ab
# 4 kHz, alles dazwischen sind die Mitten.
LOW_CROSSOVER_HZ = 200.0
HIGH_CROSSOVER_HZ = 4_000.0

_ORDER = 4

# Unterhalb dieser Laenge kann sosfiltfilt nicht arbeiten: die nullphasige
# Filterung braucht mehr Samples als ihre Randlaenge (padlen = 15 bei
# Ordnung 4). Gemessen, nicht geschaetzt.
_MIN_FILTER_SAMPLES = 16


def _sos(cutoff_hz: float, sr: int, kind: str) -> np.ndarray:
    return sig.butter(_ORDER, cutoff_hz / (sr / 2), btype=kind, output="sos")


def split_bands(
    signal: np.ndarray, sr: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Zerlegt ein Signal in Tiefen, Mitten und Hoehen.

    Nullphasig gefiltert (sosfiltfilt), damit die Baender beim Summieren nicht
    gegeneinander verschoben sind. Fuer Offline-Rendering ist die Nichtkausalitaet
    ohne Belang.
    """
    low = sig.sosfiltfilt(_sos(LOW_CROSSOVER_HZ, sr, "lowpass"), signal)
    high = sig.sosfiltfilt(_sos(HIGH_CROSSOVER_HZ, sr, "highpass"), signal)
    mid = signal - low - high
    return low.astype(np.float32), mid.astype(np.float32), high.astype(np.float32)


def envelope(points: list[EqPoint], samples: int) -> np.ndarray:
    """Baut aus Stuetzpunkten eine lineare Verstaerkungshuellkurve.

    Positionen laufen von 0 bis 1 ueber die Laenge des Uebergangs. Vor dem
    ersten und nach dem letzten Stuetzpunkt wird gehalten, nicht extrapoliert -
    eine Kurve, die erst bei 0,25 anfaengt, faengt eben mit ihrem ersten Wert an.
    """
    if samples <= 0:
        return np.zeros(0, dtype=np.float32)
    positions = np.array([p.position for p in points], dtype=np.float64)
    gains = np.array([10 ** (p.gain_db / 20.0) for p in points], dtype=np.float64)
    order = np.argsort(positions)
    grid = np.linspace(0.0, 1.0, samples, endpoint=True)
    return np.interp(grid, positions[order], gains[order]).astype(np.float32)


def apply_eq(signal: np.ndarray, sr: int, curve: EqCurve) -> np.ndarray:
    """Legt die drei Baender-Huellkurven ueber ein Signal.

    Ist das Fenster zu kurz zum Filtern, wird stattdessen der Mittelwert der
    drei Huellkurven als Breitbandverstaerkung angelegt. Bei 44,1 kHz sind das
    unter 0,36 Millisekunden - eine bandselektive Bearbeitung waere dort ohnehin
    bedeutungslos, und einen ganzen Lauf daran scheitern zu lassen waere die
    falsche Abwaegung. Fuer drei gleiche Kurven liefert der Rueckfall exakt
    dasselbe wie der normale Weg.
    """
    if signal.size == 0:
        return signal.astype(np.float32)

    if signal.size < _MIN_FILTER_SAMPLES:
        broadband = (
            envelope(curve.low, signal.size)
            + envelope(curve.mid, signal.size)
            + envelope(curve.high, signal.size)
        ) / 3.0
        return (signal * broadband).astype(np.float32)

    low, mid, high = split_bands(signal, sr)
    return (
        low * envelope(curve.low, signal.size)
        + mid * envelope(curve.mid, signal.size)
        + high * envelope(curve.high, signal.size)
    ).astype(np.float32)
