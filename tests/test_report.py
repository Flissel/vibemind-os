from djset.contracts import QaFinding, QaReport, Rejection, Shortlist
from djset.report import correction_gain_db, render_qa_report


def _report(true_peak: float, findings: list[QaFinding]) -> QaReport:
    return QaReport(
        length_s=3600.0,
        target_length_s=3600.0,
        lufs_integrated=-9.0,
        true_peak=true_peak,
        findings=findings,
    )


def test_a_clean_report_needs_no_correction() -> None:
    assert correction_gain_db(_report(-2.0, [])) is None


def test_a_hot_render_gets_exactly_the_gain_it_needs() -> None:
    report = _report(+1.5, [QaFinding(kind="true_peak", text="zu laut")])

    gain = correction_gain_db(report)

    assert gain is not None
    assert abs(gain - (-1.0 - 1.5)) < 1e-6


def test_a_length_deviation_is_not_correctable_by_re_rendering() -> None:
    """Die Laenge steht im Mixplan - erneutes Rendern aendert daran nichts."""
    report = _report(-3.0, [QaFinding(kind="length", text="zu kurz")])

    assert correction_gain_db(report) is None


def test_the_report_names_every_rejection_and_finding() -> None:
    shortlist = Shortlist(
        accepted=["a", "b", "c"],
        rejected=[Rejection(track_id="d", reason="am Genre vorbei")],
        shortened=False,
        note="passt",
    )
    report = _report(-2.0, [QaFinding(kind="seam", text="Naht bei 3:20 wackelt")])

    text = render_qa_report(report, shortlist, [], None)

    assert "am Genre vorbei" in text
    assert "Naht bei 3:20 wackelt" in text
    assert "d" in text
