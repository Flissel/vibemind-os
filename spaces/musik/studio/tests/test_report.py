from djset.contracts import (
    AudioFormat,
    BpmRange,
    MixPlan,
    Profile,
    QaFinding,
    QaReport,
    Rejection,
    Shortlist,
)
from djset.qa import TRUE_PEAK_CEILING_DB
from djset.report import CORRECTION_HEADROOM_DB, correction_gain_db, render_qa_report
from djset.tempo import TempoWahl
from tests.fixtures.render import simple_plan


def _report(
    true_peak: float,
    findings: list[QaFinding],
    audio_format: AudioFormat | None = None,
) -> QaReport:
    return QaReport(
        length_s=3600.0,
        target_length_s=3600.0,
        audio_format=audio_format
        or AudioFormat(channels=2, sample_rate=44_100, subtype="FLOAT"),
        lufs_integrated=-9.0,
        true_peak=true_peak,
        findings=findings,
    )


def _shortlist(note: str = "passt") -> Shortlist:
    return Shortlist(accepted=["a", "b", "c"], rejected=[], shortened=False, note=note)


def _long_transitions(plan: MixPlan, bars: int) -> MixPlan:
    """Derselbe Plan, aber mit einer Taktzahl, die den Korridor haelt."""
    return MixPlan.model_validate(
        plan.model_dump()
        | {
            "transitions": [
                t.model_dump() | {"bars": bars} for t in plan.transitions
            ]
        }
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


def test_the_report_names_every_rejection_and_finding(profile: Profile) -> None:
    shortlist = Shortlist(
        accepted=["a", "b", "c"],
        rejected=[Rejection(track_id="d", reason="am Genre vorbei")],
        shortened=False,
        note="passt",
    )
    report = _report(-2.0, [QaFinding(kind="seam", text="Naht bei 3:20 wackelt")])

    text = render_qa_report(report, shortlist, [], None, simple_plan(), profile)

    assert "am Genre vorbei" in text
    assert "Naht bei 3:20 wackelt" in text
    assert "d" in text


def test_a_corrected_and_now_clean_report_says_so_in_the_headline(
    profile: Profile,
) -> None:
    """Fix Round 1: eine Korrektur ist ein eigenes Ergebnis, keine stille
    Weglassung. `corrected=True` zusammen mit einem jetzt sauberen Report
    (dem NORMALEN Ausgang einer erfolgreichen Korrektur) darf die Statuszeile
    nicht als schlichtes "sauber ausgeliefert" melden - Spec Abschnitt 7
    verlangt Auslieferung "mit Vermerk", und dieser Vermerk gehoert in die
    eine Zeile, die ohne Weiterlesen beantwortet, was passiert ist.
    """
    report = _report(-2.0, [])

    text = render_qa_report(
        report, _shortlist(), [], None, simple_plan(), profile, corrected=True
    )

    status_line = next(line for line in text.splitlines() if line.startswith("**Status:**"))
    assert "korrigiert" in status_line
    assert status_line != "**Status:** Keine Befunde - sauber ausgeliefert."


def test_a_hints_only_report_says_hinweis_not_befund(profile: Profile) -> None:
    """Hinweise (length, seam_unmeasurable) sind kein Rot: die Statuszeile sagt
    Hinweis, nicht Befund, und die Hinweise stehen unter eigener Ueberschrift."""
    report = _report(
        -2.0,
        [
            QaFinding(kind="length", text="Laenge weicht ab"),
            QaFinding(kind="seam_unmeasurable", text="Naht nicht messbar"),
        ],
    )

    text = render_qa_report(report, _shortlist(), [], None, simple_plan(), profile)

    lines = text.splitlines()
    status_line = next(line for line in lines if line.startswith("**Status:**"))
    assert "Hinweis" in status_line
    assert "Befund" not in status_line
    hints_at = lines.index("## Hinweise")
    assert "- **length**: Laenge weicht ab" in lines[hints_at:]
    assert "- **seam_unmeasurable**: Naht nicht messbar" in lines[hints_at:]
    # Unter "Befunde der QA" steht davon nichts.
    findings_at = lines.index("## Befunde der QA")
    assert lines[findings_at + 2] == "- Keine."
    assert "Laenge weicht ab" not in "\n".join(lines[findings_at:hints_at])


def test_a_corrected_report_with_only_hints_says_so_in_the_headline(
    profile: Profile,
) -> None:
    report = _report(-2.0, [QaFinding(kind="length", text="Laenge weicht ab")])

    text = render_qa_report(
        report, _shortlist(), [], None, simple_plan(), profile, corrected=True
    )

    status_line = next(
        line for line in text.splitlines() if line.startswith("**Status:**")
    )
    assert "korrigiert" in status_line
    assert "Hinweis" in status_line
    assert "Befund" not in status_line


def test_the_report_names_the_format_of_the_delivered_file(profile: Profile) -> None:
    """Finding 6: das Format der ausgelieferten Datei stand nirgends.

    set.wav ist Stereo im Float-Subtyp, die Spec sagt 44,1 kHz / 24 Bit Stereo
    zu. Die Abweichung beim Subtyp ist bewusst - aber der Bericht ist die einzige
    Rechenschaft eines unbeaufsichtigten Laufs, und was geliefert wurde, muss
    darin stehen.
    """
    report = _report(-2.0, [])

    text = render_qa_report(report, _shortlist(), [], None, simple_plan(), profile)

    format_line = next(line for line in text.splitlines() if line.startswith("- Format:"))
    assert "Stereo, 44100 Hz" in format_line
    assert "44100 Hz" in format_line
    assert "32 Bit Float" in format_line
    # Und die Zusage der Spec daneben, sonst faellt die Abweichung nicht auf.
    assert "24 Bit Stereo" in format_line


def test_the_report_lists_transitions_below_the_profile_corridor(
    profile: Profile,
) -> None:
    """Finding 7: unter den Korridor geklammerte Uebergaenge waren unsichtbar.

    Liefert find_mix_regions entartete Regionen - was sie fuer gleichfoermig
    laut gemastertes Material ausdruecklich dokumentiert -, klammert
    build_mixplan jeden Uebergang auf einen Takt. Der Critic kann das nicht
    beanstanden, sein Minimum IST ein Takt, und die einzige Spur war eine
    Tracklist, in der ueberall "blend, 1 Takte" steht.
    """
    report = _report(-2.0, [])
    # simple_plan() liefert Ein-Takt-Uebergaenge, das Profil verlangt 16.
    text = render_qa_report(report, _shortlist(), [], None, simple_plan(), profile)

    assert "## Verkuerzte Uebergaenge" in text
    assert "`t0` nach `t1`: 1 statt 16 Takte" in text
    assert "`t1` nach `t2`: 1 statt 16 Takte" in text


def test_the_report_says_so_when_no_transition_was_shortened(
    profile: Profile,
) -> None:
    """Keine Sektion faellt weg, wenn nichts zu melden ist - ein Leser soll
    nie raten muessen, ob sie fehlt oder ob nichts war."""
    report = _report(-2.0, [])
    plan = _long_transitions(simple_plan(), profile.transition.bars_min)

    text = render_qa_report(report, _shortlist(), [], None, plan, profile)

    assert "## Verkuerzte Uebergaenge" in text
    assert "Keine - jeder Uebergang haelt die 16 Takte des Profils." in text


def test_an_empty_curator_note_leaves_no_stray_blank_line(profile: Profile) -> None:
    """Shortlist.note hat kein min_length. War sie leer, stand statt der Zeile
    eine dritte Leerzeile im Bericht."""
    text = render_qa_report(
        _report(-2.0, []), _shortlist(note=""), [], None, simple_plan(), profile
    )

    assert "\n\n\n" not in text


def test_the_report_names_every_song_outside_the_tempo_range(profile: Profile) -> None:
    """Ein Set aus dem Bestand hat einen Tempo-Bereich. Was nicht hineinpasst,
    bleibt draussen - und muss im Bericht mit seinem Tempo stehen, sonst sucht
    der Leser den Song im Set und findet ihn nicht."""
    tempo = TempoWahl(
        bereich=BpmRange(target=126.0, min=124.0, max=128.0),
        drin=["a", "b", "c"],
        draussen={"d": 174.0, "e": 90.0},
    )

    text = render_qa_report(
        _report(-2.0, []), _shortlist(), [], None, simple_plan(), profile, tempo=tempo
    )

    abschnitt = text.split("## Nicht im Tempo-Bereich", 1)[1].split("\n## ", 1)[0]
    assert "`d`: 174.0 BPM" in abschnitt
    assert "`e`: 90.0 BPM" in abschnitt
    assert "124.0" in abschnitt and "128.0" in abschnitt


def test_the_tempo_section_says_so_when_every_song_fits(profile: Profile) -> None:
    tempo = TempoWahl(
        bereich=BpmRange(target=126.0, min=124.0, max=128.0),
        drin=["a", "b", "c"],
        draussen={},
    )

    text = render_qa_report(
        _report(-2.0, []), _shortlist(), [], None, simple_plan(), profile, tempo=tempo
    )

    abschnitt = text.split("## Nicht im Tempo-Bereich", 1)[1].split("\n## ", 1)[0]
    assert "Keine" in abschnitt


def test_without_tempo_information_there_is_no_tempo_section(profile: Profile) -> None:
    """Ein Lauf aus dem watch-Ordner hat keinen Tempo-Bereich aus dem Bestand."""
    text = render_qa_report(_report(-2.0, []), _shortlist(), [], None, simple_plan(), profile)

    assert "Tempo-Bereich" not in text


# --- A1: Tonart-Brueche im Bericht ------------------------------------------


def _abschnitt(text: str, titel: str) -> str:
    return text.split(f"## {titel}", 1)[1].split("\n## ", 1)[0]


def test_der_bericht_nennt_jeden_tonart_bruch(profile: Profile) -> None:
    """simple_plan: t0 -> t1 -> t2, beide Uebergaenge blend. t1 (8B) nach t2
    (2A) passt nicht - der Bericht nennt beide Songs und beide Tonarten, und
    dass blend dort gar nicht haette stehen duerfen, faellt durch die Technik
    in der Zeile auf."""
    tonarten = {"t0": "8A", "t1": "8B", "t2": "2A"}

    text = render_qa_report(
        _report(-2.0, []), _shortlist(), [], None, simple_plan(), profile,
        tonarten=tonarten,
    )

    abschnitt = _abschnitt(text, "Tonart-Brueche")
    assert "`t1` (8B) nach `t2` (2A)" in abschnitt
    assert "`t0`" not in abschnitt


def test_eine_ergaenzte_technik_am_bruch_wird_genannt(profile: Profile) -> None:
    """cut steht nicht in den Techniken des Profils - am Bruch ist er trotzdem
    erlaubt, und der Bericht sagt das."""
    plan = simple_plan()
    plan = MixPlan.model_validate(
        plan.model_dump()
        | {
            "transitions": [
                plan.transitions[0].model_dump(),
                plan.transitions[1].model_dump() | {"technique": "cut"},
            ]
        }
    )
    assert "cut" not in profile.transition.techniques

    text = render_qa_report(
        _report(-2.0, []), _shortlist(), [], None, plan, profile,
        tonarten={"t0": "8A", "t1": "8B", "t2": "2A"},
    )

    abschnitt = _abschnitt(text, "Tonart-Brueche")
    assert "cut" in abschnitt
    assert "ergaenzt" in abschnitt


def test_ohne_bruch_sagt_der_abschnitt_keine(profile: Profile) -> None:
    text = render_qa_report(
        _report(-2.0, []), _shortlist(), [], None, simple_plan(), profile,
        tonarten={"t0": "8A", "t1": "8B", "t2": "9B"},
    )

    assert "Keine" in _abschnitt(text, "Tonart-Brueche")


def test_der_bericht_nennt_vermutlich_halb_gemessene_songs(profile: Profile) -> None:
    """B10: 63 BPM neben einem Bereich um 126 ist vermutlich halb gemessen."""
    tempo = TempoWahl(
        bereich=BpmRange(target=126.0, min=124.0, max=128.0),
        drin=["a", "b", "c"],
        draussen={"h": 63.0, "d": 252.0, "x": 90.0},
        oktave={"h": "halb", "d": "doppelt"},
    )

    text = render_qa_report(
        _report(-2.0, []), _shortlist(), [], None, simple_plan(), profile, tempo=tempo
    )

    abschnitt = _abschnitt(text, "Nicht im Tempo-Bereich")
    assert "`h`: 63.0 BPM (vermutlich halb gemessen)" in abschnitt
    assert "`d`: 252.0 BPM (vermutlich doppelt gemessen)" in abschnitt
    assert "`x`: 90.0 BPM\n" in abschnitt
