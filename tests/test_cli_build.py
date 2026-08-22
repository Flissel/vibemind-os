import json
from datetime import date
from pathlib import Path

import pytest
import soundfile as sf

from djset import cli
from djset.agents.base import Budget
from djset.cli import cmd_build
from djset.contracts import (
    REORDERABLE_KINDS,
    CritiqueLog,
    MixPlan,
    Profile,
    QaFinding,
    QaReport,
    Shortlist,
)
from djset.qa import TRUE_PEAK_CEILING_DB
from djset.report import CORRECTION_HEADROOM_DB
from djset.run import Run
from tests.fixtures.fake_client import FakeClient
from tests.fixtures.make_audio import click_track, click_track_with_quiet_edges


def _budget() -> Budget:
    return Budget(max_tokens=400_000, max_seconds=300.0)


def _analyst_answer(energy: float) -> str:
    return json.dumps(
        {
            "usable": True,
            "notes": "Passt.",
            "energy_level": energy,
            "remeasure_with_bpm": None,
        }
    )


def _full_run_responses() -> list[str]:
    """Antworten fuer einen vollstaendigen Lauf ueber vier Fixture-Tracks.

    Gestaffelte Energie, und das ist kein Zufall: die vier Fixture-Tracks sind
    identische Clicktracks, haben also dieselbe Tonart und dasselbe Tempo.
    Bekaemen sie auch noch denselben Energiepegel, traefen alle drei
    Bedingungen der Aehnlichkeitsregel zusammen und der Architect wiese jede
    Reihenfolge zurueck. Ein Abstand von 0,2 je Nachbarpaar reicht.

    Der letzte Track liegt mit 0,9 ueber dem Bogen (der dort 0,5 verlangt) -
    eine umsortierbare Beanstandung, die Critic-Schleife laeuft also alle drei
    Runden durch und der Lauf nimmt am Ende den besten Plan.
    """
    curator = json.dumps(
        {"accepted": ["t0", "t1", "t2", "t3"], "rejected": [], "note": "Alles gut."}
    )
    architect = json.dumps({"order": ["t0", "t1", "t2", "t3"], "note": "Aufbau."})
    designer = json.dumps(
        {"choices": [{"technique": "blend", "bars": 16} for _ in range(3)]}
    )
    return (
        [_analyst_answer(e) for e in (0.3, 0.5, 0.7, 0.9)]
        + [curator]
        + [architect, designer] * 3
    )


def _prepared(tmp_path: Path, profile: Profile, count: int) -> Run:
    """Ein Lauf mit Profil und genug Material im watch-Ordner."""
    run = _run_with_profile(tmp_path, profile, count)
    watch = tmp_path / "watch"
    watch.mkdir(exist_ok=True)
    for index in range(count):
        click_track(watch / f"t{index}.wav", bpm=174.0, bars=14)
    return run


def _prepared_with_mixable_tracks(
    tmp_path: Path, profile: Profile, count: int
) -> Run:
    """Wie _prepared, aber mit Material, das sich wirklich mischen laesst.

    Zwei Unterschiede, beide noetig, damit am Ende NUR die Laengenbeanstandung
    uebrigbleibt:

    1. Die Tracks haben leise Raender. Der blosse click_track ist ueber seine
       ganze Laenge gleich laut, hat also weder Intro noch Outro - jeder
       Uebergang darauf wird zur Stossnaht, die der Critic zu Recht beanstandet
       (und die ist umsortierbar).
    2. Der Energiebogen des Profils ist flach. Welcher Thread welche
       Analystenantwort abbekommt, ist nicht vorhersagbar - analyse_all faechert
       ueber vier Threads auf. Bei einem flachen Bogen ist das egal: die
       Zielenergie ist ueberall dieselbe, und die vier gestaffelten Pegel liegen
       alle in der Toleranz, in welcher Reihenfolge sie auch landen. Gestaffelt
       muessen sie sein, weil die Tracks sonst - gleiche Tonart, gleiches Tempo,
       gleiche Energie - unter die Aehnlichkeitsregel fielen.
    """
    run = _run_with_profile(
        tmp_path,
        profile,
        count,
        energy_arc=[{"position": 0.0, "energy": 0.5}, {"position": 1.0, "energy": 0.5}],
    )
    watch = tmp_path / "watch"
    watch.mkdir(exist_ok=True)
    for index in range(count):
        click_track_with_quiet_edges(
            watch / f"t{index}.wav", bpm=174.0, bars=24, edge_bars=4
        )
    return run


def _run_with_profile(
    tmp_path: Path,
    profile: Profile,
    count: int,
    energy_arc: list[dict[str, float]] | None = None,
) -> Run:
    run = Run.create(tmp_path, "liquid dnb", date(2026, 8, 21))
    changes: dict[str, object] = {
        "track_count": {"needed": count, "request": count}
    }
    if energy_arc is not None:
        changes["energy_arc"] = energy_arc
    small = Profile.model_validate(profile.model_dump() | changes)
    run.write_model("profile.json", small)
    return run


def test_build_without_a_profile_fails_cleanly(tmp_path: Path, capsys) -> None:
    Run.create(tmp_path, "liquid dnb", date(2026, 8, 21))

    code = cmd_build(tmp_path, None, FakeClient([]), _budget())

    assert code != 0
    assert "profile.json" in capsys.readouterr().err


def test_build_with_too_little_material_stops_before_any_agent(
    tmp_path: Path, profile: Profile, capsys
) -> None:
    run = _prepared(tmp_path, profile, 3)
    for leftover in (tmp_path / "watch").iterdir():
        leftover.unlink()
    client = FakeClient([])

    code = cmd_build(tmp_path, run.run_id, client, _budget())

    assert code != 0
    assert client.calls == 0
    assert "watch" in capsys.readouterr().err


def test_a_full_build_writes_every_artefact(
    tmp_path: Path, profile: Profile
) -> None:
    run = _prepared(tmp_path, profile, 4)
    client = FakeClient(_full_run_responses())

    code = cmd_build(tmp_path, run.run_id, client, _budget())

    assert code == 0
    assert run.has("shortlist.json")
    assert run.has("setlist.json")
    assert run.has("mixplan.json")
    assert run.has("critique.json")
    plan = run.read_model("mixplan.json", MixPlan)
    assert [e.track_id for e in plan.entries] == ["t0", "t1", "t2", "t3"]
    shortlist = run.read_model("shortlist.json", Shortlist)
    assert shortlist.accepted == ["t0", "t1", "t2", "t3"]


def test_every_critic_round_lands_in_an_artifact(tmp_path: Path, profile: Profile) -> None:
    """Erfolgskriterium 7: jede Beanstandung muss nachvollziehbar sein.

    Auch die der VERWORFENEN Runden - das sind gerade die, die zur
    Neuanordnung gefuehrt haben. Auf der Standardausgabe eines
    unbeaufsichtigten Laufs sieht sie hinterher niemand.
    """
    run = _prepared(tmp_path, profile, 4)
    client = FakeClient(_full_run_responses())

    assert cmd_build(tmp_path, run.run_id, client, _budget()) == 0

    log = run.read_model("critique.json", CritiqueLog)
    assert len(log.rounds) >= 1
    assert [entry.round for entry in log.rounds] == list(
        range(1, len(log.rounds) + 1)
    )
    assert [entry.chosen for entry in log.rounds].count(True) == 1
    chosen = next(entry for entry in log.rounds if entry.chosen)
    assert chosen.round == log.chosen_round
    # Die kurzen Fixture-Tracks verfehlen die Ziellaenge zwangslaeufig, also
    # steht in jeder Runde mindestens eine Beanstandung - und ihr Wortlaut
    # samt Reihenfolge der Runde ist im Artefakt nachlesbar.
    for entry in log.rounds:
        assert entry.order == ["t0", "t1", "t2", "t3"]
        assert entry.complaints
        assert all(complaint.text for complaint in entry.complaints)


def test_a_length_complaint_alone_does_not_buy_another_round(
    tmp_path: Path, profile: Profile
) -> None:
    """Finding 5: Stufe 5 kann nur umsortieren.

    Ein zu kurzes Set wird durch eine andere Reihenfolge nicht laenger, ein
    Cue-Punkt ruckt dadurch nicht auf den Downbeat. Bleibt nach dem Aussieben
    keine umsortierbare Beanstandung uebrig, ist eine weitere Runde
    verschenkt - sie kostet einen Architect- und einen Designer-Aufruf und
    liefert denselben Plan.

    Das Material hier hat leise Raender und der Bogen ist flach (siehe
    _prepared_with_mixable_tracks), es bleibt also weder eine Overlap- noch
    eine Energiebeanstandung uebrig. Die Ziellaenge verfehlen vier Tracks von
    je gut einer halben Minute zwangslaeufig - und diese Beanstandung ist
    nicht umsortierbar.
    """
    run = _prepared_with_mixable_tracks(tmp_path, profile, 4)
    curator = json.dumps(
        {"accepted": ["t0", "t1", "t2", "t3"], "rejected": [], "note": "Alles gut."}
    )
    architect = json.dumps({"order": ["t0", "t1", "t2", "t3"], "note": "Aufbau."})
    designer = json.dumps(
        {"choices": [{"technique": "blend", "bars": 16} for _ in range(3)]}
    )
    client = FakeClient(
        [_analyst_answer(e) for e in (0.2, 0.4, 0.6, 0.8)]
        + [curator, architect, designer]
    )

    assert cmd_build(tmp_path, run.run_id, client, _budget()) == 0

    log = run.read_model("critique.json", CritiqueLog)
    assert len(log.rounds) == 1
    kinds = {c.kind for c in log.rounds[0].complaints}
    assert "length" in kinds
    assert not kinds & REORDERABLE_KINDS
    # Vier Analysten, ein Curator, ein Architect, ein Designer. Ohne die
    # Aussortierung waeren es drei Architect- und drei Designer-Aufrufe.
    assert client.calls == 7


def test_build_takes_its_base_from_the_command_line(
    tmp_path: Path, monkeypatch
) -> None:
    """Finding 7: plan kennt --base, build nahm bisher fest Path.cwd().

    Wer `dj-set plan --base /projects/mix` und danach `dj-set build` laeuft,
    bekam "kein unfertiger Lauf gefunden", ohne zu erfahren, dass nur an der
    falschen Stelle gesucht wurde.
    """
    seen: dict[str, object] = {}

    def fake_build(base, run_id, client, budget) -> int:
        seen["base"] = base
        seen["run_id"] = run_id
        return 0

    monkeypatch.setattr(cli, "cmd_build", fake_build)

    assert cli.main(["build", "--base", str(tmp_path), "--run", "irgendein-lauf"]) == 0

    assert seen["base"] == tmp_path
    assert seen["run_id"] == "irgendein-lauf"


def test_a_full_build_renders_the_set(tmp_path: Path, profile: Profile) -> None:
    """Der ganze Weg: watch-Ordner rein, hoerbares Set raus."""
    run = _prepared_with_mixable_tracks(tmp_path, profile, 4)
    client = FakeClient(_full_run_responses())

    code = cmd_build(tmp_path, run.run_id, client, _budget())

    assert code == 0
    assert run.has("set.wav")
    assert run.has("tracklist.md")
    assert run.has("qa-report.md")

    with sf.SoundFile(run.path("set.wav")) as handle:
        assert len(handle) > 0

    bericht = run.path("qa-report.md").read_text(encoding="utf-8")
    assert "True Peak" in bericht


def test_a_finished_run_is_no_longer_unfinished(
    tmp_path: Path, profile: Profile
) -> None:
    """Run.latest_unfinished erkennt einen Lauf an set.wav als fertig."""
    run = _prepared_with_mixable_tracks(tmp_path, profile, 4)
    cmd_build(tmp_path, run.run_id, FakeClient(_full_run_responses()), _budget())

    assert Run.latest_unfinished(tmp_path) is None


def test_a_true_peak_finding_triggers_exactly_one_corrective_re_render(
    tmp_path: Path, profile: Profile, monkeypatch
) -> None:
    """Wiring-Test fuer Stufe 9, ohne echtes Audio.

    render() und check_set() sind hier durch Fakes ersetzt - der Test prueft
    ausschliesslich die Verdrahtung in cmd_build, nicht den Renderer oder die
    QA-Messung selbst (die sind in tests/render/ und tests/test_qa.py
    getestet). Drei Dinge muessen stimmen, und keins davon sieht man an
    render() oder check_set() fuer sich allein:

    1. Ein True-Peak-Befund loest einen zweiten render()-Aufruf mit
       negativem master_gain_db aus.
    2. Der QA-Bericht traegt die Zahlen der ZWEITEN Messung (der, die
       tatsaechlich ausgeliefert wurde), nicht die der ersten, die die
       Korrektur nur ausgeloest hat.
    3. Das bleibt bei GENAU EINEM Re-Render, auch wenn die zweite Messung
       immer noch einen True-Peak-Befund zeigt (simuliert hier eine Korrektur,
       die nicht vollstaendig hilft) - Spec Abschnitt 7 sagt "hoechstens
       einmal", nicht "bis es passt". Ein Bug, der das zu einer Schleife
       macht, wuerde hier einen dritten render()-Aufruf ausloesen.
    """
    run = _prepared_with_mixable_tracks(tmp_path, profile, 4)
    client = FakeClient(_full_run_responses())

    reports = [
        QaReport(
            length_s=110.0,
            target_length_s=110.0,
            lufs_integrated=-14.0,
            true_peak=0.3,
            findings=[QaFinding(kind="true_peak", text="True Peak liegt bei 0.3 dB.")],
        ),
        # Absichtlich immer noch nicht sauber - die Korrektur hilft hier
        # nicht vollstaendig, damit sichtbar wird, ob cmd_build trotzdem
        # aufhoert.
        QaReport(
            length_s=110.0,
            target_length_s=110.0,
            lufs_integrated=-14.0,
            true_peak=-0.2,
            findings=[QaFinding(kind="true_peak", text="True Peak liegt bei -0.2 dB.")],
        ),
    ]
    qa_calls: list[Path] = []

    def fake_check_set(path: Path, plan: MixPlan) -> QaReport:
        qa_calls.append(path)
        return reports[len(qa_calls) - 1]

    render_calls: list[float] = []

    def fake_render(
        plan: MixPlan,
        sources: dict[str, Path],
        target: Path,
        master_gain_db: float = 0.0,
        sr: int | None = None,
    ) -> None:
        render_calls.append(master_gain_db)

    monkeypatch.setattr(cli, "check_set", fake_check_set)
    monkeypatch.setattr(cli, "render", fake_render)

    code = cmd_build(tmp_path, run.run_id, client, _budget())

    assert code == 0
    # Genau ein korrigierender Re-Render: der erste Aufruf unveraendert, der
    # zweite mit dem aus dem ersten Befund berechneten (negativen) Gain - und
    # kein dritter, obwohl der zweite Bericht immer noch nicht sauber ist.
    # Der Gain kommt aus den Konstanten statt als getippte Zahl: er zielt
    # bewusst UNTER die Decke (CORRECTION_HEADROOM_DB), und dieser Test soll
    # die Verdrahtung pinnen, nicht die Wahl des Sicherheitsabstands.
    expected_gain = TRUE_PEAK_CEILING_DB - CORRECTION_HEADROOM_DB - 0.3
    assert render_calls == [0.0, pytest.approx(expected_gain)]
    assert len(qa_calls) == 2

    bericht = run.path("qa-report.md").read_text(encoding="utf-8")
    assert "korrigiert" in bericht
    # Die AUSGELIEFERTEN Zahlen sind die der zweiten Messung, nicht die der
    # ersten, die die Korrektur erst ausgeloest hat.
    assert "-0.2 dB" in bericht
    assert "0.3 dB" not in bericht


def test_build_with_a_wrong_sample_rate_stops_before_any_agent(
    tmp_path: Path, profile: Profile, capsys
) -> None:
    """Finding 4: die Ratenpruefung gehoert in den Vorab-Check.

    Vorher passierte eine 48-kHz-Datei den Scan, die Analyse (librosa
    resampelt anstandslos), die Kuratierung, die Anordnung, den Entwurf und die
    Kritik - und riss den Lauf erst in Stufe 8 ab, mit rohem Traceback, ohne
    qa-report.md und nachdem jedes Token bezahlt war. Spec Abschnitt 7 stellt
    genau diese Klasse von Pruefung davor.
    """
    run = _prepared(tmp_path, profile, 4)
    click_track((tmp_path / "watch" / "t3.wav"), bpm=174.0, bars=14, sr=48_000)
    client = FakeClient([])

    code = cmd_build(tmp_path, run.run_id, client, _budget())

    assert code != 0
    assert client.calls == 0
    fehler = capsys.readouterr().err
    assert "t3.wav" in fehler
    assert "48000" in fehler
