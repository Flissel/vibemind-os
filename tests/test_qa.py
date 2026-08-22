from pathlib import Path

import soundfile as sf

from djset.contracts import MixPlan
from djset.qa import TRUE_PEAK_CEILING_DB, check_set
from djset.render.engine import render
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


def test_the_report_carries_the_measured_numbers(tmp_path: Path) -> None:
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)

    report = check_set(target, plan)

    assert abs(report.length_s - plan.target_length_s) < 0.1
    assert report.target_length_s == plan.target_length_s
    assert report.lufs_integrated < 0.0
    assert report.true_peak < 0.0
