import json
from datetime import date
from pathlib import Path

from djset.agents.base import Budget
from djset.cli import cmd_build
from djset.contracts import MixPlan, Profile, Shortlist
from djset.run import Run
from tests.fixtures.fake_client import FakeClient
from tests.fixtures.make_audio import click_track


def _budget() -> Budget:
    return Budget(max_tokens=400_000, max_seconds=300.0)


def _prepared(tmp_path: Path, profile: Profile, count: int) -> Run:
    """Ein Lauf mit Profil und genug Material im watch-Ordner."""
    run = Run.create(tmp_path, "liquid dnb", date(2026, 8, 21))
    small = Profile.model_validate(
        profile.model_dump() | {"track_count": {"needed": count, "request": count}}
    )
    run.write_model("profile.json", small)
    watch = tmp_path / "watch"
    watch.mkdir(exist_ok=True)
    for index in range(count):
        click_track(watch / f"t{index}.wav", bpm=174.0, bars=14)
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

    # Gestaffelte Energie, und das ist kein Zufall: die vier Fixture-Tracks sind
    # identische Clicktracks, haben also dieselbe Tonart und dasselbe Tempo.
    # Bekaemen sie auch noch denselben Energiepegel, traefen alle drei
    # Bedingungen der Aehnlichkeitsregel zusammen und der Architect wiese jede
    # Reihenfolge zurueck. Ein Abstand von 0,2 je Nachbarpaar reicht.
    def analyst(energy: float) -> str:
        return json.dumps(
            {
                "usable": True,
                "notes": "Passt.",
                "energy_level": energy,
                "remeasure_with_bpm": None,
            }
        )

    curator = json.dumps(
        {"accepted": ["t0", "t1", "t2", "t3"], "rejected": [], "note": "Alles gut."}
    )
    architect = json.dumps({"order": ["t0", "t1", "t2", "t3"], "note": "Aufbau."})
    designer = json.dumps(
        {"choices": [{"technique": "blend", "bars": 16} for _ in range(3)]}
    )
    # Drei Runden Architect und Designer: die kurzen Fixture-Tracks verfehlen die
    # Ziellaenge zwangslaeufig, also laeuft die Critic-Schleife voll durch und
    # der Lauf nimmt am Ende den besten Plan.
    client = FakeClient(
        [analyst(0.3), analyst(0.5), analyst(0.7), analyst(0.9)]
        + [curator]
        + [architect, designer] * 3
    )

    code = cmd_build(tmp_path, run.run_id, client, _budget())

    assert code == 0
    assert run.has("shortlist.json")
    assert run.has("setlist.json")
    assert run.has("mixplan.json")
    plan = run.read_model("mixplan.json", MixPlan)
    assert [e.track_id for e in plan.entries] == ["t0", "t1", "t2", "t3"]
    shortlist = run.read_model("shortlist.json", Shortlist)
    assert shortlist.accepted == ["t0", "t1", "t2", "t3"]
