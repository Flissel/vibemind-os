import json
from pathlib import Path

from djset.cli import cmd_plan
from djset.contracts import Profile
from djset.run import Run
from tests.agents.test_profiler import DRAFT
from tests.agents.test_prompt_writer import _prompt_set
from tests.fixtures.fake_client import FakeClient


def _client() -> FakeClient:
    return FakeClient([json.dumps(DRAFT), _prompt_set(18)])


def test_plan_writes_both_artifacts(tmp_path: Path):
    code = cmd_plan(
        base=tmp_path, genre="liquid dnb", length_minutes=60.0, client=_client()
    )
    assert code == 0
    run = Run.latest_unfinished(tmp_path)
    assert run is not None
    assert run.has("profile.json")
    assert run.has("prompts.md")


def test_plan_profile_survives_the_round_trip(tmp_path: Path):
    cmd_plan(base=tmp_path, genre="liquid dnb", length_minutes=60.0, client=_client())
    run = Run.latest_unfinished(tmp_path)
    profile = run.read_model("profile.json", Profile)
    assert profile.genre == "liquid dnb"
    assert profile.length_minutes == 60.0
    assert profile.track_count.request == 18


def test_plan_prompts_markdown_has_every_track(tmp_path: Path):
    cmd_plan(base=tmp_path, genre="liquid dnb", length_minutes=60.0, client=_client())
    run = Run.latest_unfinished(tmp_path)
    text = run.path("prompts.md").read_text(encoding="utf-8")
    assert text.count("## Track") == 18


# Ruling 13: argparse laesst "--length -5" anstandslos durch. cmd_plan muss
# das selbst abfangen - mit einer klaren deutschen Meldung statt einer rohen
# pydantic-ValidationError aus build_profile.
def test_plan_rejects_a_non_positive_length(tmp_path: Path, capsys):
    code = cmd_plan(
        base=tmp_path, genre="liquid dnb", length_minutes=-5.0, client=FakeClient([])
    )
    assert code != 0
    assert "Fehler" in capsys.readouterr().err
    assert Run.latest_unfinished(tmp_path) is None


# Ruling 13: ein leeres "--genre" muss cmd_plan ebenso abfangen wie eine
# nicht-positive "--length" - gleiche Fehlerklasse, gleiche Form der Pruefung.
def test_plan_rejects_an_empty_genre(tmp_path: Path, capsys):
    code = cmd_plan(
        base=tmp_path, genre="", length_minutes=60.0, client=FakeClient([])
    )
    assert code != 0
    assert "Fehler" in capsys.readouterr().err
    assert Run.latest_unfinished(tmp_path) is None


# Ruling 13: eine Schemaverletzung, die auch nach allen Wiederholungen nicht
# behoben wird, muss als lesbare Meldung mit Exit-Code enden statt als
# Traceback.
def test_plan_agent_error_is_a_clean_failure(tmp_path: Path, capsys):
    broken = json.loads(json.dumps(DRAFT))
    broken["bpm"] = {"target": 300.0, "min": 170.0, "max": 178.0}
    client = FakeClient([json.dumps(broken)] * 3)

    code = cmd_plan(base=tmp_path, genre="liquid dnb", length_minutes=60.0, client=client)

    assert code != 0
    assert "Fehler" in capsys.readouterr().err
