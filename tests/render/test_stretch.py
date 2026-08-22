from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.contracts import MAX_RATIO, MIN_RATIO
from djset.render.stretch import stretch


def _tone(path: Path, seconds: float, sr: int = 44_100) -> Path:
    t = np.linspace(0.0, seconds, int(seconds * sr), endpoint=False)
    signal = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    sf.write(path, signal, sr)
    return path


def _duration(path: Path) -> float:
    with sf.SoundFile(path) as handle:
        return len(handle) / handle.samplerate


@pytest.mark.parametrize("ratio", [MIN_RATIO, 1.0, MAX_RATIO])
def test_the_output_is_shorter_by_exactly_the_ratio(
    tmp_path: Path, ratio: float
) -> None:
    source = _tone(tmp_path / "in.wav", 4.0)
    target = tmp_path / "out.wav"

    stretch(source, target, ratio)

    expected = 4.0 / ratio
    assert abs(_duration(target) - expected) < 0.05


def test_a_ratio_of_one_leaves_the_length_alone(tmp_path: Path) -> None:
    source = _tone(tmp_path / "in.wav", 2.0)
    target = tmp_path / "out.wav"

    stretch(source, target, 1.0)

    assert abs(_duration(target) - 2.0) < 0.02


def test_the_pitch_survives_the_stretch(tmp_path: Path) -> None:
    """Zeitdehnung, nicht Abspielgeschwindigkeit: 440 Hz bleiben 440 Hz."""
    source = _tone(tmp_path / "in.wav", 4.0)
    target = tmp_path / "out.wav"

    stretch(source, target, MAX_RATIO)

    signal, sr = sf.read(target, dtype="float32")
    spectrum = np.abs(np.fft.rfft(signal))
    peak_hz = float(np.fft.rfftfreq(len(signal), 1 / sr)[int(np.argmax(spectrum))])
    assert abs(peak_hz - 440.0) < 5.0


def test_the_same_input_renders_bit_identically(tmp_path: Path) -> None:
    """Determinismus ist die Zusage des Spec: gleicher Mixplan, gleiches WAV."""
    source = _tone(tmp_path / "in.wav", 2.0)
    first, second = tmp_path / "a.wav", tmp_path / "b.wav"

    stretch(source, first, 1.03)
    stretch(source, second, 1.03)

    assert first.read_bytes() == second.read_bytes()


def test_an_out_of_range_ratio_is_refused(tmp_path: Path) -> None:
    source = _tone(tmp_path / "in.wav", 1.0)

    with pytest.raises(ValueError, match="0.94"):
        stretch(source, tmp_path / "out.wav", 1.5)
