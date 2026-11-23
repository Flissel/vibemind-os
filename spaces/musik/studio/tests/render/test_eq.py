import numpy as np
import pytest
from scipy import signal as sig

from djset.contracts import EqCurve, EqPoint
from djset.render.eq import apply_eq, envelope, split_bands

SR = 44_100


def _noise(seconds: float = 1.0) -> np.ndarray:
    generator = np.random.default_rng(7)
    return generator.standard_normal(int(seconds * SR)).astype(np.float32) * 0.1


def _flat(gain_db: float = 0.0) -> EqCurve:
    points = [EqPoint(position=0.0, gain_db=gain_db),
              EqPoint(position=1.0, gain_db=gain_db)]
    return EqCurve(low=points, mid=points, high=points)


def test_the_three_bands_add_back_up_to_the_original() -> None:
    """Die Zerlegung rekonstruiert exakt - darauf ruht die ganze EQ-Stufe."""
    signal = _noise()

    low, mid, high = split_bands(signal, SR)

    assert np.allclose(low + mid + high, signal, atol=1e-5)


def test_the_bands_actually_separate_frequencies() -> None:
    t = np.linspace(0.0, 1.0, SR, endpoint=False)
    bass = np.sin(2 * np.pi * 60.0 * t).astype(np.float32)

    low, mid, high = split_bands(bass, SR)

    assert float(np.max(np.abs(low))) > 0.8
    assert float(np.max(np.abs(high))) < 0.1


def test_a_flat_curve_returns_the_signal_unchanged() -> None:
    signal = _noise()

    assert np.allclose(apply_eq(signal, SR, _flat()), signal, atol=1e-5)


def test_full_attenuation_silences_the_signal() -> None:
    signal = _noise()

    result = apply_eq(signal, SR, _flat(gain_db=-60.0))

    assert float(np.max(np.abs(result))) < 0.01 * float(np.max(np.abs(signal)))


def test_the_envelope_interpolates_between_its_points() -> None:
    points = [EqPoint(position=0.0, gain_db=-20.0),
              EqPoint(position=1.0, gain_db=0.0)]

    values = envelope(points, 101)

    assert len(values) == 101
    assert abs(values[0] - 10 ** (-20 / 20)) < 1e-6
    assert abs(values[-1] - 1.0) < 1e-6
    assert values[50] > values[10]


def test_the_envelope_holds_before_the_first_and_after_the_last_point() -> None:
    points = [EqPoint(position=0.25, gain_db=-6.0),
              EqPoint(position=0.75, gain_db=0.0)]

    values = envelope(points, 100)

    assert abs(values[0] - 10 ** (-6 / 20)) < 1e-6
    assert abs(values[-1] - 1.0) < 1e-6


def test_a_ramp_down_really_falls() -> None:
    signal = np.ones(SR, dtype=np.float32) * 0.5
    curve = EqCurve(
        low=[EqPoint(position=0.0, gain_db=0.0), EqPoint(position=1.0, gain_db=-60.0)],
        mid=[EqPoint(position=0.0, gain_db=0.0), EqPoint(position=1.0, gain_db=-60.0)],
        high=[EqPoint(position=0.0, gain_db=0.0), EqPoint(position=1.0, gain_db=-60.0)],
    )

    result = apply_eq(signal, SR, curve)

    assert abs(float(result[0]) - 0.5) < 0.02
    assert abs(float(result[-1])) < 0.01


def test_a_window_below_the_filter_minimum_falls_back_to_broadband_gain() -> None:
    """Unter 16 Samples kann sosfiltfilt nicht filtern (gemessen, siehe eq.py).

    apply_eq darf dabei nicht mit einem scipy-ValueError abbrechen, sondern legt
    den Mittelwert der drei Huellkurven als eine Breitbandverstaerkung an.
    """
    signal = np.ones(10, dtype=np.float32) * 0.5
    curve = EqCurve(
        low=[EqPoint(position=0.0, gain_db=0.0), EqPoint(position=1.0, gain_db=0.0)],
        mid=[EqPoint(position=0.0, gain_db=-6.0), EqPoint(position=1.0, gain_db=-6.0)],
        high=[EqPoint(position=0.0, gain_db=6.0), EqPoint(position=1.0, gain_db=6.0)],
    )

    result = apply_eq(signal, SR, curve)

    expected_gain = (10 ** (0 / 20) + 10 ** (-6 / 20) + 10 ** (6 / 20)) / 3.0
    assert len(result) == 10
    assert np.allclose(result, signal * expected_gain, atol=1e-6)


def test_the_fallback_agrees_with_the_normal_path_for_identical_curves() -> None:
    """Fuer drei gleiche Kurven liefert der Breitband-Fallback denselben Wert
    wie der gefilterte Normalweg - der Uebergang bei der Mindestlaenge ist
    stetig, kein Sprung.
    """
    length = 17  # knapp oberhalb der sosfiltfilt-Mindestlaenge von 16 Samples
    generator = np.random.default_rng(3)
    signal = generator.standard_normal(length).astype(np.float32) * 0.1
    points = [EqPoint(position=0.0, gain_db=-6.0), EqPoint(position=1.0, gain_db=3.0)]
    curve = EqCurve(low=points, mid=points, high=points)

    normal_path = apply_eq(signal, SR, curve)

    gain = envelope(points, length)
    fallback_path = (signal * gain).astype(np.float32)

    assert np.allclose(normal_path, fallback_path, atol=1e-5)


def test_the_filter_minimum_matches_what_scipy_actually_demands() -> None:
    """Die Mindestlaenge ist hergeleitet, nicht hingeschrieben - hier gemessen.

    _MIN_FILTER_SAMPLES stand als 16 da, gegen _ORDER = 4 gemessen und
    richtig - aber eben nur dagegen. Eine Aenderung der Ordnung haette sie
    stillschweigend falsch gemacht, und zwar in die unsichere Richtung: eine
    hoehere Ordnung braucht MEHR Samples, apply_eq waere dann mit einem
    scipy-ValueError abgebrochen statt in den Breitband-Rueckfall zu gehen.

    Geprueft wird gegen scipy selbst, nicht gegen eine zweite Formel: bei
    einem Sample weniger muss sosfiltfilt werfen, bei genau der Mindestlaenge
    muss es rechnen. Damit faellt eine geaenderte Ordnung hier auf.
    """
    from djset.render.eq import _MIN_FILTER_SAMPLES, LOW_CROSSOVER_HZ, _sos

    sos = _sos(LOW_CROSSOVER_HZ, SR, "lowpass")

    with pytest.raises(ValueError):
        sig.sosfiltfilt(sos, np.ones(_MIN_FILTER_SAMPLES - 1))

    assert len(sig.sosfiltfilt(sos, np.ones(_MIN_FILTER_SAMPLES))) == (
        _MIN_FILTER_SAMPLES
    )
