from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.agents.designer import TransitionChoice, build_mixplan
from djset.contracts import MixEntry, MixPlan, Profile, Setlist, Transition
from djset.qa import TRUE_PEAK_CEILING_DB, check_set
from djset.render.engine import bar_length_s, render
from djset.report import correction_gain_db
from tests.fixtures.analyses import make_off_grid
from tests.fixtures.render import flat_curve, pulsed_tone, simple_plan, sources_for


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

    inserted = np.zeros((shift_samples, *signal.shape[1:]), dtype=signal.dtype)
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


def test_a_clean_render_at_a_different_tempo_has_no_seam_finding(tmp_path: Path) -> None:
    """Pinnt die Verallgemeinerung ueber 174 BPM hinaus.

    Regression fuer einen Fund des Reviewers: bei 128 BPM (und weiteren
    Tempi, die 60 nicht glatt teilen) feuerte die Nahtpruefung auf einem
    SAUBEREN Render, weil zwei verschiedene Fehler sich ueberlagerten - ein
    zu grosszuegiges Statistikmass (behoben in Fix Round 1/2, siehe
    _seam_drift_beats) UND ein Fixture-Fehler: simple_plan()/sources_for()
    schnitten Eintraege bei fest 10.0 Sekunden zu, was nur bei Tempi, die 60
    glatt teilen (120, 150, 174, ...), auf einen ganzen Schlag faellt. Bei
    128 BPM lag das Trackende mitten in einem Schlag, und die beiden
    ueberblendeten Pulsraster liefen an der Naht strukturell phasenversetzt -
    unabhaengig von jeder Pruefung. tests/fixtures/render.py::_entry_length_s
    behebt das, indem sie die Eintragslaenge auf den naechsten ganzen Schlag
    rundet (bei 174 BPM bitgleich zu den bisherigen 10.0 Sekunden). Dieser
    Test haelt beide Fixe fest: ohne sie faellt er bei 128 BPM durch, mit
    ihnen bleibt er bei jedem getesteten Tempo gruen.
    """
    plan = simple_plan(target_bpm=128.0)
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path, target_bpm=128.0), target, master_gain_db=-12.0)

    report = check_set(target, plan)

    assert not any(f.kind == "seam" for f in report.findings)


def test_a_masked_removal_break_is_still_caught(tmp_path: Path) -> None:
    """Regression fuer Fix Round 3: ein an der Naht maskierter Bruch.

    0,1 Schlag Audio wird GENAU auf dem Pulsschlag an der Naht entfernt.
    Direkte Diagnose (siehe task-6-report.md, Fix Round 2/3) zeigt: das
    Intervall, das die Naht direkt ueberspannt, ist danach BYTEIDENTISCH mit
    dem eines sauberen Renders - der Schnitt springt in die abklingende
    Huellkurve und sieht fuer librosas Beat-Tracker wie ein normaler Einsatz
    an fast derselben Stelle aus. Der Versatz zeigt sich erst im NAECHSTEN
    Intervall. Fix Round 2 (nur das ueberspannende Intervall) verpasste
    diesen Fall bei allen sieben getesteten Tempi; die Nachbarschaft aus Fix
    Round 3 (SEAM_NEIGHBOURHOOD_BEATS) sieht auch den Nachbarn und faengt ihn.
    """
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)

    signal, sr = sf.read(target, dtype="float32")
    beat_len_s = bar_length_s(plan.target_bpm) / 4.0
    shift_samples = int(round(0.1 * beat_len_s * sr))
    seam_sample = int(round(plan.transitions[0].start_at_s * sr))

    shifted = np.concatenate([signal[:seam_sample], signal[seam_sample + shift_samples:]])
    pad = np.zeros((len(signal) - len(shifted), *signal.shape[1:]), dtype=signal.dtype)
    shifted = np.concatenate([shifted, pad])
    sf.write(target, shifted, sr)

    report = check_set(target, plan)

    assert any(f.kind == "seam" for f in report.findings)


def test_the_report_carries_the_measured_numbers(tmp_path: Path) -> None:
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)

    report = check_set(target, plan)

    assert abs(report.length_s - plan.target_length_s) < 0.1
    assert report.target_length_s == plan.target_length_s
    assert report.lufs_integrated < 0.0
    assert report.true_peak < 0.0


def _hand_written_plan(
    length_s: float, starts: tuple[float, ...], target_bpm: float
) -> MixPlan:
    """Ein dreiteiliger Mixplan mit frei gesetzten Nahtpositionen.

    simple_plan() ist mit rund 27 Sekunden zu kurz, um die Minutenhaelfte
    ueberhaupt zu erreichen - die beiden Zeitstempel-Regressionen brauchen
    deshalb einen laengeren Plan. Er muss keine echte Auslieferung ergeben und
    entsteht daher nicht ueber render().
    """
    return MixPlan(
        target_length_s=length_s,
        target_bpm=target_bpm,
        entries=[
            MixEntry(
                track_id=f"t{i}", play_from_s=0.0, play_to_s=length_s,
                timestretch_ratio=1.0, gain_db=0.0,
            )
            for i in range(3)
        ],
        transitions=[
            Transition(
                from_track=f"t{i}", to_track=f"t{i + 1}", start_at_s=start,
                overlap_s=bar_length_s(target_bpm), bars=1, technique="blend",
                eq_out=flat_curve(), eq_in=flat_curve(),
            )
            for i, start in enumerate(starts)
        ],
    )


def test_a_gap_past_the_minute_half_gets_a_floored_timestamp(tmp_path: Path) -> None:
    """Regression: die Gap-Meldung rundete die Minute statt sie zu floorn.

    `start / 60` mit `:.0f` und `start % 60` mit `:04.1f` liefen unabhaengig
    voneinander - bei einem Gap um 111.4s (= 1:51.4) rundete der erste Anteil
    auf 2 hoch, waehrend der zweite bei 51.4 blieb, macht "2:51" statt "1:51".
    djset/render/tracklist.py hat mit timestamp() bereits eine floorende
    Funktion; qa.py nutzt sie jetzt statt der eigenen Rechnung.

    simple_plan() ist mit rund 27 Sekunden zu kurz, um die Minutenhaelfte zu
    ueberhaupt zu erreichen - hier steht deshalb ein eigener, laengerer
    Mixplan, der nur fuer diesen Test gebraucht wird, keine echte Auslieferung
    ergeben muss und daher nicht ueber render() erzeugt wird.
    """
    length = 130.0
    target = tmp_path / "set.wav"
    pulsed_tone(target, seconds=length)
    signal, sr = sf.read(target, dtype="float32")
    gap_start = 111.4
    signal[int(gap_start * sr) : int((gap_start + 0.6) * sr)] = 0.0
    sf.write(target, signal, sr)

    # Weit weg vom Gap bei 111.4s, damit das Nahtfenster (rund 11 Sekunden bei
    # 174 BPM/8 Takten) es nicht mit anfasst - dieser Test soll ausschliesslich
    # die Gap-Meldung pruefen.
    plan = _hand_written_plan(length, (40.0, 80.0), target_bpm=174.0)

    report = check_set(target, plan)

    gap = next(f for f in report.findings if f.kind == "gap")
    assert "1:51" in gap.text
    assert "2:51" not in gap.text


def test_a_seam_past_the_minute_half_gets_a_floored_timestamp(tmp_path: Path) -> None:
    """Dasselbe an der zweiten Fundstelle: die Naht-Meldung rundete die Minute.

    Der Fix fuer die Luecken liess die identische Rechnung im Naht-Befund
    stehen (`start_at_s / 60` mit `:.0f` neben `start_at_s % 60` mit
    `:04.1f`). Eine Naht bei 111.4 s (= 1:51.4) wurde damit als "2:51.4"
    gemeldet - in einem einstuendigen Set weist das den Hoerer eine ganze
    Minute neben die Stelle.

    Der Bruch entsteht hier nicht durch Schnitte im Signal, sondern durch ein
    Tempo im Plan, das im Material nicht steckt: der Impulszug laeuft mit 174
    BPM, der Plan behauptet 150. Damit schlaegt die grobe Zweitpruefung
    (SEAM_BPM_TOLERANCE) an jeder Naht an, und zwar deterministisch.
    """
    length = 130.0
    target = tmp_path / "set.wav"
    pulsed_tone(target, seconds=length, bpm=174.0)

    plan = _hand_written_plan(length, (40.0, 111.4), target_bpm=150.0)

    report = check_set(target, plan)

    seams = [f for f in report.findings if f.kind == "seam"]
    late = next(f for f in seams if "t1" in f.text and "t2" in f.text)
    assert "1:51" in late.text
    assert "2:51" not in late.text


def test_an_unstretchable_track_leaves_no_silence_before_the_seam(
    tmp_path: Path, profile: Profile
) -> None:
    """Regression fuer Critical 1: das Uebergangsfenster wurde zweimal gerechnet.

    Stufe 6 mass in Takten des QUELLTRACKS (bar_length_s / timestretch_ratio),
    Stufe 8 in Takten des ZIELTEMPOS (4 * 60 / target_bpm). Beide stimmen nur
    ueberein, solange das Verhaeltnis wirklich target_bpm/source_bpm ist - und
    das ist es nicht mehr, sobald ein Track ausserhalb der Stretch-Grenze
    liegt: _ratio zwingt ihn dann auf 1.0, und der Uebergang wird zum Schnitt,
    dessen eq_out ueber das ganze Fenster flach auf -60 dB steht.

    Hier laeuft eine 200-BPM-Quelle gegen ein 174-BPM-Ziel bei 4 Takten.
    Material hinter der Naht: 4 * 4 * 60/200 = 4,8000 s. Das alte Fenster von
    Stufe 8: 4 * 4 * 60/174 = 5,5172 s. Die Differenz von 0,7172 s legte der
    Renderer VOR die Naht - digitale Stille, bevor der eingehende Track
    ueberhaupt einsetzt (gemessen: 0,8237 s Stille ab 23,1763 s bei einer Naht
    auf 24,0000 s, von der QA als `gap` gemeldet). Mit Transition.overlap_s
    liest Stufe 8 die Dauer aus dem Plan und rechnet sie nicht mehr nach.
    """
    tracks = [
        make_off_grid("t0", bpm=200.0, duration_s=30.0,
                      intro_end_s=8 * 4 * 60.0 / 200.0, outro_start_s=24.0, energy=0.3),
        make_off_grid("t1", bpm=174.0, duration_s=30.0,
                      intro_end_s=8 * bar_length_s(174.0),
                      outro_start_s=16 * bar_length_s(174.0), energy=0.5),
        make_off_grid("t2", bpm=174.0, duration_s=30.0,
                      intro_end_s=8 * bar_length_s(174.0),
                      outro_start_s=16 * bar_length_s(174.0), energy=0.7),
    ]
    plan = build_mixplan(
        Setlist(order=[t.id for t in tracks], note=""),
        tracks,
        [TransitionChoice(technique="blend", bars=4) for _ in range(2)],
        profile,
    )
    sources = {
        track.id: pulsed_tone(tmp_path / f"{track.id}.wav", seconds=track.duration_s,
                              hz=220.0, bpm=track.bpm.value)
        for track in tracks
    }
    target = tmp_path / "set.wav"
    render(plan, sources, target, master_gain_db=-12.0)

    seam = plan.transitions[0]
    # Der Pfad, um den es geht: der Track laesst sich nicht angleichen, also
    # bleibt er ungestreckt und der Uebergang wird zum Schnitt.
    assert seam.technique == "cut"
    assert plan.entries[0].timestretch_ratio == 1.0
    # Das Fenster ist die Schnittmenge beider Seiten - 4,8 s Outro-Material,
    # nicht die 5,5172 s, die 4 Takte im ZIELTEMPO dauern wuerden.
    assert seam.overlap_s == pytest.approx(4 * 4 * 60.0 / 200.0, abs=1e-6)
    assert seam.overlap_s < 4 * bar_length_s(plan.target_bpm)

    report = check_set(target, plan)
    assert not any(f.kind == "gap" for f in report.findings), [
        f.text for f in report.findings
    ]

    # Direkt nachgemessen, unabhaengig von der Gap-Schwelle: die 0,72 s vor der
    # Naht tragen Signal, statt stillgelegt zu sein.
    signal, sr = sf.read(target, dtype="float32")
    before = signal[int((seam.start_at_s - 0.72) * sr) : int(seam.start_at_s * sr)]
    assert float(np.max(np.abs(before))) > 0.001


def test_the_one_permitted_correction_lands_below_the_ceiling(tmp_path: Path) -> None:
    """Regression fuer Critical 2: der Rundlauf aus Messen, Korrigieren, Nachmessen.

    Der vorhandene Verdrahtungstest in tests/test_cli_build.py ersetzt
    check_set durch handgeschriebene Berichte und kann das deshalb nicht
    sehen. Hier laeuft dieselbe Folge wie in cmd_build ueber echtes Audio:
    absichtlich heiss gerendert (gain_db +6 auf jedem Eintrag, wie es ein
    Pegelabgleich gegen den Median liefert), gemessen, einmal korrigiert, neu
    gemessen.

    Gemessen vor dem Fix: erste Messung 3.870651 dBTP, Korrektur -4.870651 dB,
    zweite Messung -0.999999709 dBTP - 2,9e-07 dB UEBER der Decke, und qa.py
    vergleicht strikt. Damit meldete rund die Haelfte aller korrigierten
    Laeufe einen true_peak-Befund fuer ein File, das die Grenze um drei
    Zehnmillionstel dB reisst.
    """
    plan = simple_plan()
    hot = MixPlan.model_validate(
        plan.model_dump()
        | {"entries": [e.model_dump() | {"gain_db": 6.0} for e in plan.entries]}
    )
    sources = sources_for(tmp_path)
    target = tmp_path / "set.wav"

    render(hot, sources, target)
    first = check_set(target, hot)
    assert any(f.kind == "true_peak" for f in first.findings)

    correction = correction_gain_db(first)
    assert correction is not None

    render(hot, sources, target, master_gain_db=correction)
    second = check_set(target, hot)

    assert second.true_peak <= TRUE_PEAK_CEILING_DB
    assert not any(f.kind == "true_peak" for f in second.findings), second.true_peak


def test_an_unmeasurable_seam_gets_its_own_kind(tmp_path: Path, monkeypatch) -> None:
    """Eine unmessbare Naht ist keine gemessene Abweichung.

    Beide Faelle teilten sich bisher die Art `seam` - wer die Befunde danach
    filtert, konnte eine Naht, an der ueberhaupt nichts messbar war, nicht von
    einer erkannten Abweichung unterscheiden. Das ist kein Randfall: das
    Nahtfenster liegt auf Intro- und Outro-Material, dem duennsten Teil beider
    Tracks, waehrend SEAM_DRIFT_TOLERANCE an einem durchgehenden Impulszug
    kalibriert wurde.

    Beide Messungen werden hier zum Scheitern gebracht statt Material zu
    suchen, an dem librosa zufaellig aufgibt - der Zweig soll deterministisch
    geprueft sein, nicht wahrscheinlich.
    """
    def unmeasurable(*args: object, **kwargs: object) -> object:
        raise RuntimeError("kein Beatraster")

    monkeypatch.setattr("djset.qa.beat_times", unmeasurable)
    monkeypatch.setattr("djset.qa.detect_bpm_and_grid", unmeasurable)

    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)

    report = check_set(target, plan)

    kinds = [f.kind for f in report.findings]
    assert kinds == ["seam_unmeasurable", "seam_unmeasurable"]
    assert "kein Beatraster messen" in report.findings[0].text


def test_stereo_render_wird_als_stereo_gemeldet(tmp_path: Path) -> None:
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)

    report = check_set(target, plan)

    assert report.audio_format.channels == 2


def test_true_peak_nimmt_den_lauteren_kanal(tmp_path: Path) -> None:
    """Nur links laut: der Mono-Mittelwert wuerde 6 dB unterschlagen."""
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)
    signal, sr = sf.read(target, dtype="float32", always_2d=True)
    loud_left = np.stack([signal[:, 0], np.zeros(len(signal), np.float32)], axis=1)
    sf.write(target, loud_left, sr, subtype="FLOAT")
    peak_left = float(20 * np.log10(np.max(np.abs(loud_left))))

    report = check_set(target, plan)

    # True Peak liegt bei oder knapp ueber dem Abtastspitzenwert des lauten Kanals.
    assert report.true_peak >= peak_left - 0.1


# --- A4: Stille am Rand ist keine Luecke ------------------------------------


def test_stille_am_ende_und_am_anfang_ist_keine_luecke(tmp_path: Path) -> None:
    """Der letzte Song klingt aus, der erste kann still anfangen - beides ist
    kein Loch im Set und darf den Bericht nicht rot machen."""
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)
    signal, sr = sf.read(target, dtype="float32")
    signal[: int(1.5 * sr)] = 0.0
    signal[-int(2.0 * sr) :] = 0.0
    sf.write(target, signal, sr)

    report = check_set(target, plan)

    assert not [f for f in report.findings if f.kind == "gap"]


def test_stille_mitten_im_set_bleibt_eine_luecke_auch_mit_stillen_raendern(
    tmp_path: Path,
) -> None:
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)
    signal, sr = sf.read(target, dtype="float32")
    signal[: int(1.5 * sr)] = 0.0
    signal[int(5.0 * sr) : int(6.0 * sr)] = 0.0
    signal[-int(2.0 * sr) :] = 0.0
    sf.write(target, signal, sr)

    report = check_set(target, plan)

    gaps = [f for f in report.findings if f.kind == "gap"]
    # Eine Luecke, und zwar die in der Mitte (die Pulse davor klingen leise
    # aus, deshalb beginnt sie knapp vor Sekunde 5).
    assert len(gaps) == 1
    assert "0:04" in gaps[0].text or "0:05" in gaps[0].text


# --- B3: Laengen-Hinweis gegen die gewuenschte Laenge -----------------------


def test_der_laengen_hinweis_misst_gegen_die_gewuenschte_laenge(tmp_path: Path) -> None:
    """Der Plan rechnet seine Laenge aus dem Material - gegen sich selbst
    gemessen ist sie immer richtig. Gefragt ist, ob das Set so lang ist, wie
    es gewuenscht war."""
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)

    zu_kurz = check_set(target, plan, wunsch_laenge_s=plan.target_length_s * 2)
    passend = check_set(target, plan, wunsch_laenge_s=plan.target_length_s)
    ohne = check_set(target, plan)

    laenge = [f for f in zu_kurz.findings if f.kind == "length"]
    assert len(laenge) == 1 and "gewuenscht" in laenge[0].text
    assert not [f for f in passend.findings if f.kind == "length"]
    assert not [f for f in ohne.findings if f.kind == "length"]


def test_mit_wunschlaenge_zaehlt_die_laenge_des_plans_nicht(
    tmp_path: Path,
) -> None:
    """Ohne Wunschlaenge haette der doppelt so lange Plan einen Hinweis
    ausgeloest (test_a_length_deviation_is_caught) - mit ihr nicht mehr."""
    plan = simple_plan()
    target = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), target, master_gain_db=-12.0)
    gestreckt = MixPlan.model_validate(
        plan.model_dump() | {"target_length_s": plan.target_length_s * 2}
    )

    mit_wunsch = check_set(target, gestreckt, wunsch_laenge_s=plan.target_length_s)

    assert not [f for f in mit_wunsch.findings if f.kind == "length"]
