from djset.contracts import QaFinding, QaReport, Rejection, Shortlist
from djset.qa import TRUE_PEAK_CEILING_DB
from djset.report import CORRECTION_HEADROOM_DB, correction_gain_db, render_qa_report


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


def test_a_hot_render_gets_the_gain_it_needs_plus_headroom() -> None:
    """Gezielt wird UNTER die Decke, nicht auf sie.

    Frueher war das Ziel exakt TRUE_PEAK_CEILING_DB. Der Weg vom gemessenen
    Spitzenwert ueber 20*log10 zurueck in einen float32-Faktor trifft die
    Decke nur bis auf etwa 1e-7 dB, mit zufaelligem Vorzeichen - und qa.py
    vergleicht mit einem strikten `>`. Gemessen ueber die echte Folge aus
    cmd_build: zweite Messung -0.999999709 dBTP, also 2,9e-07 dB darueber,
    Befund gemeldet.
    """
    report = _report(+1.5, [QaFinding(kind="true_peak", text="zu laut")])

    gain = correction_gain_db(report)

    assert gain is not None
    assert abs(gain - (TRUE_PEAK_CEILING_DB - CORRECTION_HEADROOM_DB - 1.5)) < 1e-6
    # Der korrigierte Spitzenwert landet unter der Decke, nicht auf ihr.
    assert report.true_peak + gain < TRUE_PEAK_CEILING_DB


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


def test_a_corrected_and_now_clean_report_says_so_in_the_headline() -> None:
    """Fix Round 1: eine Korrektur ist ein eigenes Ergebnis, keine stille
    Weglassung. `corrected=True` zusammen mit einem jetzt sauberen Report
    (dem NORMALEN Ausgang einer erfolgreichen Korrektur) darf die Statuszeile
    nicht als schlichtes "sauber ausgeliefert" melden - Spec Abschnitt 7
    verlangt Auslieferung "mit Vermerk", und dieser Vermerk gehoert in die
    eine Zeile, die ohne Weiterlesen beantwortet, was passiert ist.
    """
    shortlist = Shortlist(accepted=["a", "b", "c"], rejected=[], shortened=False, note="passt")
    report = _report(-2.0, [])

    text = render_qa_report(report, shortlist, [], None, corrected=True)

    status_line = next(line for line in text.splitlines() if line.startswith("**Status:**"))
    assert "korrigiert" in status_line
    assert status_line != "**Status:** Keine Befunde - sauber ausgeliefert."
