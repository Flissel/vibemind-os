from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.render.engine import MissingSource, bar_length_s, render
from tests.fixtures.render import simple_plan, sources_for


def test_the_output_has_the_planned_length(tmp_path: Path) -> None:
    plan = simple_plan()
    target = tmp_path / "set.wav"

    render(plan, sources_for(tmp_path), target)

    with sf.SoundFile(target) as handle:
        assert abs(len(handle) / handle.samplerate - plan.target_length_s) < 0.05


def test_the_output_is_not_silent_anywhere(tmp_path: Path) -> None:
    """Keine ungewollten Luecken - jede halbe Sekunde traegt Signal."""
    plan = simple_plan()
    target = tmp_path / "set.wav"

    render(plan, sources_for(tmp_path), target)

    signal, sr = sf.read(target, dtype="float32")
    window = sr // 2
    blocks = [signal[i:i + window] for i in range(0, len(signal) - window, window)]
    assert all(float(np.max(np.abs(b))) > 0.01 for b in blocks)


def test_the_same_plan_renders_bit_identically(tmp_path: Path) -> None:
    plan = simple_plan()
    sources = sources_for(tmp_path)

    render(plan, sources, tmp_path / "a.wav")
    render(plan, sources, tmp_path / "b.wav")

    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()


def test_the_master_gain_scales_the_whole_set(tmp_path: Path) -> None:
    plan = simple_plan()
    sources = sources_for(tmp_path)
    render(plan, sources, tmp_path / "loud.wav")
    render(plan, sources, tmp_path / "quiet.wav", master_gain_db=-6.0)

    loud, _ = sf.read(tmp_path / "loud.wav", dtype="float32")
    quiet, _ = sf.read(tmp_path / "quiet.wav", dtype="float32")
    ratio = float(np.max(np.abs(quiet))) / float(np.max(np.abs(loud)))
    assert abs(ratio - 10 ** (-6 / 20)) < 0.02


def test_a_missing_source_is_named(tmp_path: Path) -> None:
    plan = simple_plan()
    sources = sources_for(tmp_path)
    del sources["t1"]

    with pytest.raises(MissingSource, match="t1"):
        render(plan, sources, tmp_path / "set.wav")


def test_the_bar_length_follows_the_target_tempo() -> None:
    assert abs(bar_length_s(174.0) - 4 * 60.0 / 174.0) < 1e-9
    assert abs(bar_length_s(120.0) - 2.0) < 1e-9
