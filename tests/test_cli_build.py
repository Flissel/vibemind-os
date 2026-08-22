import json
from datetime import date
from pathlib import Path

import soundfile as sf

from djset import cli
from djset.agents.base import Budget
from djset.cli import cmd_build
from djset.contracts import (
    REORDERABLE_KINDS,
    CritiqueLog,
    MixPlan,
    Profile,
    Shortlist,
)
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
