import numpy as np

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
