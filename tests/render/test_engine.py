import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.contracts import MixEntry, MixPlan, Transition
from djset.render.engine import ImpossibleMixPlan, MissingSource, bar_length_s, render
from tests.fixtures.render import flat_curve, simple_plan, sources_for


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


def test_the_same_plan_renders_bit_identically_across_a_wall_clock_second(
    tmp_path: Path,
) -> None:
    """Regression: libsndfile schreibt bei Float-WAVs einen PEAK-Chunk, dessen
    zweites Feld time(NULL) ist. Zwei Renders, die verschiedene Sekunden
    ueberqueren, waeren ohne _strip_peak_timestamp in genau diesem einen
    Zeitstempel-Feld verschieden - die Audiodaten waeren trotzdem bitgleich.
    Der schnelle Test oben (ohne Pause) haette das nicht zuverlaessig gefangen:
    er faellt nur dann durch, wenn beide render()-Aufrufe zufaellig eine
    Sekundengrenze ueberqueren. Diese Version erzwingt genau das.
    """
    plan = simple_plan()
    sources = sources_for(tmp_path)

    render(plan, sources, tmp_path / "a.wav")
    time.sleep(2.0)
    render(plan, sources, tmp_path / "b.wav")

    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()


def test_an_entry_too_short_for_both_its_transition_windows_is_refused(
    tmp_path: Path,
) -> None:
    """Ein handgeschriebener Mixplan kann ein Kopf- und ein Schwanzfenster
    verlangen, die zusammen laenger sind als der klingende Abschnitt eines
    Eintrags dazwischen. block[:head] und block[-tail:] wuerden sich dann
    ueberlappen, und die zweite apply_eq-Anwendung liest bereits vom ersten
    Durchlauf veraenderte Samples - die beiden Kurven multiplizieren sich zu
    etwas, das in keinem Plan steht. Der Renderer weigert sich statt das
    stillschweigend zu tun.
    """
    target_bpm = 174.0
    bars = 4
    entries = [
        MixEntry(track_id="t0", play_from_s=0.0, play_to_s=10.0,
                 timestretch_ratio=1.0, gain_db=0.0),
        # Klingt nur 2 s lang - viel kuerzer als die zwei 4-Takt-Fenster, die
        # die Nachbar-Uebergaenge an Kopf und Schwanz verlangen.
        MixEntry(track_id="t1", play_from_s=0.0, play_to_s=2.0,
                 timestretch_ratio=1.0, gain_db=0.0),
        MixEntry(track_id="t2", play_from_s=0.0, play_to_s=10.0,
                 timestretch_ratio=1.0, gain_db=0.0),
    ]
    window = bars * bar_length_s(target_bpm)
    transitions = [
        Transition(from_track="t0", to_track="t1", start_at_s=8.0,
                   overlap_s=window, bars=bars, technique="blend",
                   eq_out=flat_curve(), eq_in=flat_curve()),
        Transition(from_track="t1", to_track="t2", start_at_s=9.0,
                   overlap_s=window, bars=bars, technique="blend",
                   eq_out=flat_curve(), eq_in=flat_curve()),
    ]
    plan = MixPlan(target_length_s=19.0, target_bpm=target_bpm,
                    entries=entries, transitions=transitions)
    sources = sources_for(tmp_path)

    with pytest.raises(ImpossibleMixPlan, match="t1"):
        render(plan, sources, tmp_path / "set.wav")


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
