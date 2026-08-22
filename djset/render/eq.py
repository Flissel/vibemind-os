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

sosfiltfilt braucht mehr Samples als der Filterorder verlangt (bei Order 4 hier
16 Samples, siehe padlen in scipy). Ein Uebergang ist per Vertrag mindestens
einen Takt lang (contracts.TransitionRules.bars_min >= 1), bei 174 BPM also
rund 1,4 Sekunden respektive ueber 60000 Samples - weit ueber dieser Grenze,
daher kein Sonderfall fuer kurze Fenster noetig.
"""

import numpy as np
from scipy import signal as sig

from djset.contracts import EqCurve, EqPoint

# Trennfrequenzen wie an einem DJ-Mixer: der Bass unter 200 Hz, die Hoehen ab
# 4 kHz, alles dazwischen sind die Mitten.
LOW_CROSSOVER_HZ = 200.0
HIGH_CROSSOVER_HZ = 4_000.0

_ORDER = 4


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
    """Legt die drei Baender-Huellkurven ueber ein Signal."""
    if signal.size == 0:
        return signal.astype(np.float32)
    low, mid, high = split_bands(signal, sr)
    return (
        low * envelope(curve.low, signal.size)
        + mid * envelope(curve.mid, signal.size)
        + high * envelope(curve.high, signal.size)
    ).astype(np.float32)
