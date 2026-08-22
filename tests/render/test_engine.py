import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.contracts import EqCurve, EqPoint, MixEntry, MixPlan, Transition
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


def _constant(path: Path, value: float, seconds: float, sr: int = 44_100) -> Path:
    """Ein Signal konstanter Amplitude - jeder Sample derselbe Wert.

    Damit ist der gerenderte Wert an jeder Stelle direkt die Huellkurve mal
    diesem Wert, ohne dass eine Wellenform dazwischenliegt.
    """
    sf.write(path, np.full(int(seconds * sr), value, dtype=np.float32), sr)
    return path


def _ramp(start_db: float, end_db: float) -> EqCurve:
    """Eine Rampe von start_db auf end_db, auf allen drei Baendern gleich.

    Auf allen dreien dieselbe Kurve, und das ist der Kern des Aufbaus: apply_eq
    rechnet low*e + mid*e + high*e, und mid ist als signal - low - high
    definiert. Bei identischen Kurven faellt die Bandzerlegung damit exakt
    heraus und uebrig bleibt signal * e - eine Huellkurve, die sich
    hinschreiben laesst.
    """
    points = [EqPoint(position=0.0, gain_db=start_db),
              EqPoint(position=1.0, gain_db=end_db)]
    return EqCurve(low=points, mid=points, high=points)


def test_the_crossfade_follows_the_two_eq_curves_sample_for_sample(
    tmp_path: Path,
) -> None:
    """Pinnt, was Stufe 8 zu einem MIX statt zu einer Aneinanderreihung macht.

    Vor diesem Test war das gesamte EQ- und Crossfade-Verhalten ungepinnt:
    jeder Render-Test baut seinen Plan aus simple_plan(), dessen Kurven aus
    flat_curve() kommen - 0 dB auf allen drei Baendern, apply_eq also ueberall
    ein Nulldurchgang. Der Reviewer hat vier Mutationen durch die volle Suite
    geschickt (Kopf bekommt die AUSGEHENDE Kurve, Schwanz die EINGEHENDE,
    beide Fenster halb so breit, Kopf-EQ gar nicht angewandt) - alle vier
    blieben gruen.

    Der Aufbau macht die Erwartung analytisch:
    - Beide Quellen sind konstant (0,8 und 0,2), also ist der gerenderte Wert
      an jeder Stelle Amplitude mal Huellkurve.
    - Beide Kurven laufen auf allen drei Baendern gleich, die Bandzerlegung
      faellt damit exakt heraus (siehe _ramp).
    - envelope() interpoliert LINEAR im Verstaerkungsfaktor, nicht in dB: die
      Rampe von 0 dB auf -60 dB laeuft von 1,0 auf 0,001 geradlinig.

    Erwartet an der Naht (Position 0), in der Mitte (0,5) und am Fensterende
    (1,0): 0,8002 - 0,5005 - 0,2008. Die drei Zahlen sind verschieden genug,
    dass jede der vier Mutationen mindestens eine davon verfehlt.
    """
    sr = 44_100
    seconds = 10.0
    window_s = 2.0
    silent = 10 ** (-60.0 / 20.0)
    amplitudes = {"t0": 0.8, "t1": 0.2, "t2": 0.5}

    entries = [
        MixEntry(track_id=track_id, play_from_s=0.0, play_to_s=seconds,
                 timestretch_ratio=1.0, gain_db=0.0)
        for track_id in amplitudes
    ]
    starts = [seconds - window_s, 2 * (seconds - window_s)]
    transitions = [
        Transition(from_track=f"t{i}", to_track=f"t{i + 1}", start_at_s=starts[i],
                   overlap_s=window_s, bars=1, technique="blend",
                   eq_out=_ramp(0.0, -60.0), eq_in=_ramp(-60.0, 0.0))
        for i in range(2)
    ]
    plan = MixPlan(target_length_s=starts[-1] + seconds, target_bpm=174.0,
                   entries=entries, transitions=transitions)
    sources = {
        track_id: _constant(tmp_path / f"{track_id}.wav", value, seconds, sr)
        for track_id, value in amplitudes.items()
    }

    target = tmp_path / "set.wav"
    render(plan, sources, target, sr=sr)
    mixed, _ = sf.read(target, dtype="float32")

    seam = int(round(starts[0] * sr))
    width = int(round(window_s * sr))

    def expected(position: float) -> float:
        out_gain = 1.0 + position * (silent - 1.0)
        in_gain = silent + position * (1.0 - silent)
        return amplitudes["t0"] * out_gain + amplitudes["t1"] * in_gain

    for index, position in ((0, 0.0), ((width - 1) // 2, 0.5), (width - 1, 1.0)):
        assert mixed[seam + index] == pytest.approx(expected(position), abs=1e-4), (
            f"Position {position} des Fensters"
        )

    # Ausserhalb des Fensters laeuft jeder Track flach: davor nur t0, danach
    # nur t1. Ohne das koennte ein zu breites Fenster unbemerkt bleiben.
    assert mixed[int(4.0 * sr)] == pytest.approx(amplitudes["t0"], abs=1e-4)
    assert mixed[int(12.0 * sr)] == pytest.approx(amplitudes["t1"], abs=1e-4)
