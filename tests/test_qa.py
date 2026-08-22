from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import MixPlan
from djset.qa import TRUE_PEAK_CEILING_DB, check_set
from djset.render.engine import bar_length_s, render
from tests.fixtures.render import simple_plan, sources_for


def test_a_clean_render_has_no_findings(tmp_path: Path) -> None:
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)

    report = check_set(target, plan)

    assert [f.kind for f in report.findings] == []
    assert report.ok is True


def test_a_too_loud_render_is_caught(tmp_path: Path) -> None:
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=+12.0)

    report = check_set(target, plan)

    assert any(f.kind == "true_peak" for f in report.findings)
    assert report.true_peak > TRUE_PEAK_CEILING_DB


def test_a_length_deviation_is_caught(tmp_path: Path) -> None:
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)
    stretched = MixPlan.model_validate(
        plan.model_dump() | {"target_length_s": plan.target_length_s * 2}
    )

    report = check_set(target, stretched)

    assert any(f.kind == "length" for f in report.findings)


def test_a_silent_stretch_is_caught(tmp_path: Path) -> None:
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)
    signal, sr = sf.read(target, dtype="float32")
    signal[int(1.0 * sr) : int(3.0 * sr)] = 0.0
    sf.write(target, signal, sr)

    report = check_set(target, plan)

    assert any(f.kind == "gap" for f in report.findings)


def test_a_phase_break_at_the_seam_is_caught(tmp_path: Path) -> None:
    """Ein Versatz mitten in der Naht, den weder Laenge noch Stille sehen.

    Regression fuer einen Fund des Reviewers: das gemittelte Tempo an der
    Naht (SEAM_BPM_TOLERANCE) rechnet teleskopierend nur aus dem ersten und
    letzten Beat des Fensters und bleibt von einem Bruch MITTEN im Fenster
    fast unberuehrt - ein halber Schlag Versatz verschiebt es um weniger als
    drei BPM. Hier wird alles ab der Naht t0->t1 um einen halben Schlag
    spaeter geschoben (Stille eingefuegt) und der Schwanz zurueckgeschnitten,
    damit die Gesamtlaenge exakt zum Plan passt - weder die Laengen- noch die
    Luecken-Pruefung koennen das sehen. Nur der groesste Beatabstand-
    Ausreisser (SEAM_DRIFT_TOLERANCE) zeigt den Bruch.
    """
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)

    signal, sr = sf.read(target, dtype="float32")
    beat_len_s = bar_length_s(plan.target_bpm) / 4.0
    shift_samples = int(round(0.5 * beat_len_s * sr))
    seam_sample = int(round(plan.transitions[0].start_at_s * sr))

    inserted = np.zeros(shift_samples, dtype=signal.dtype)
    shifted = np.concatenate([signal[:seam_sample], inserted, signal[seam_sample:]])
    shifted = shifted[: len(signal)]
    sf.write(target, shifted, sr)

    report = check_set(target, plan)

    # Belegt, dass wirklich nur die Nahtpruefung anschlaegt: die
    # Gesamtlaenge ist unveraendert (Insert und Rueckschnitt gleichen sich
    # exakt aus), also darf hier ausser "seam" nichts stehen.
    assert abs(report.length_s - plan.target_length_s) < 0.01
    assert [f.kind for f in report.findings] == ["seam"]
    seam = plan.transitions[0]
    assert seam.from_track in report.findings[0].text
    assert seam.to_track in report.findings[0].text


def test_the_report_carries_the_measured_numbers(tmp_path: Path) -> None:
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)

    report = check_set(target, plan)

    assert abs(report.length_s - plan.target_length_s) < 0.1
    assert report.target_length_s == plan.target_length_s
    assert report.lufs_integrated < 0.0
    assert report.true_peak < 0.0
